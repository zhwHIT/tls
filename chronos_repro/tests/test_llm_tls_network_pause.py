"""Network outages pause the entire extraction client without live API tests."""
import importlib
import json
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
n = importlib.import_module('llm_tls_network_pause')


def test_api_and_probe_ignore_proxy_settings(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({
                'choices': [{'message': {'content': 'local-test-result'}}]
            }).encode())

        def log_message(self, *args):
            pass

    # A default opener would try the invalid proxy; the explicit empty handler
    # must ignore both environment and platform proxy discovery.
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('TLS_TEST_KEY', 'local-test-only')
    monkeypatch.setattr(n.urllib.request, 'getproxies',
                        lambda: {'http': 'http://127.0.0.1:1', 'https': 'http://127.0.0.1:1'})
    monkeypatch.setattr(n.urllib.request, 'proxy_bypass', lambda host: False)
    monkeypatch.setattr(n.urllib.request, '_opener', None)
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        n.configure_direct_transport()
        base_url = f'http://127.0.0.1:{server.server_port}'
        assert n.probe_endpoint(base_url)
        client = n.DeepSeekClient(base_url=base_url, api_key_env='TLS_TEST_KEY', timeout=2)
        assert client.chat([]).text == 'local-test-result'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def transport_error():
    error = n.RetryableLLMError('connection failed')
    error.__cause__ = urllib.error.URLError('offline')
    return error


def test_all_workers_wait_then_resume_after_single_probe(tmp_path):
    allow_probe = threading.Event()
    entered = threading.Event()
    probes = []

    def probe():
        probes.append(1)
        entered.set()
        assert allow_probe.wait(2)
        return True

    gate = n.NetworkGate(tmp_path, 'https://invalid.example', interval=0.01, probe=probe)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(gate.before_request) for _ in range(8)]
        assert entered.wait(1)
        assert not any(f.done() for f in futures)
        allow_probe.set()
        for f in futures:
            f.result(timeout=2)
    assert len(probes) == 1
    assert json.loads((tmp_path / 'network_status.json').read_text())['stage'] == 'online'


def test_transport_failure_is_held_and_retried_only_after_recovery(tmp_path):
    connected = threading.Event()
    failed_probe = threading.Event()
    calls = []

    def probe():
        if not connected.is_set():
            failed_probe.set()
        return connected.is_set()

    gate = n.NetworkGate(tmp_path, 'https://invalid.example', interval=0.01, probe=probe)
    gate.paused = False

    class FakeClient:
        def _chat_once(self, messages, temperature):
            calls.append(1)
            if len(calls) == 1:
                raise transport_error()
            return 'saved-result'

    client = n.guarded_client_class(gate, FakeClient)()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client._chat_once, [], 0)
        assert failed_probe.wait(1)
        time.sleep(0.03)
        assert len(calls) == 1 and not future.done()
        connected.set()
        assert future.result(timeout=2) == 'saved-result'
    assert calls == [1, 1] and gate.failures == 1


def test_stop_file_releases_network_wait_without_api_call(tmp_path):
    gate = n.NetworkGate(tmp_path, 'https://invalid.example', interval=0.01, probe=lambda: False)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(gate.before_request)
        (tmp_path / 'STOP').touch()
        with pytest.raises(n.NetworkPauseStopped):
            future.result(timeout=2)


def test_http_and_invalid_json_are_not_misclassified_as_offline():
    error = n.RetryableLLMError('HTTP error')
    error.__cause__ = urllib.error.HTTPError('https://invalid.example', 429, 'rate limit', {}, None)
    assert not n.is_transport_error(error)
    error.__cause__ = json.JSONDecodeError('bad json', '', 0)
    assert not n.is_transport_error(error)
    assert n.is_transport_error(transport_error())


def test_balance_failure_cancels_other_waiters(tmp_path):
    gate = n.NetworkGate(tmp_path, 'https://invalid.example', interval=0.01, probe=lambda: True)

    class FakeClient:
        def _chat_once(self, messages, temperature):
            raise n.InsufficientBalanceError('balance')

    client = n.guarded_client_class(gate, FakeClient)()
    with pytest.raises(n.InsufficientBalanceError):
        client._chat_once([], 0)
    assert gate.cancelled
    with pytest.raises(n.NetworkPauseStopped):
        gate.before_request()


def http_error(status, message, code='invalid_request_error'):
    error = n.NonRetryableLLMError('DeepSeek HTTP error: ' + json.dumps({
        'error': {'message': message, 'type': 'invalid_request_error', 'code': code}}))
    error.__cause__ = urllib.error.HTTPError('https://invalid.example', status, message, {}, None)
    return error


def test_content_rejection_keeps_gate_open_and_does_not_retry_document(tmp_path):
    gate = n.NetworkGate(tmp_path, 'https://invalid.example', probe=lambda: True)
    calls = []

    class FakeClient:
        def _chat_once(self, messages, temperature):
            calls.append(messages)
            if messages == ['rejected-document']:
                raise http_error(400, 'Content Exists Risk')
            return 'another-article-result'

    client = n.guarded_client_class(gate, FakeClient)()
    with pytest.raises(n.ContentRejectedError):
        client._chat_once(['rejected-document'], 0)
    assert not gate.cancelled
    assert client._chat_once(['another-document'], 0) == 'another-article-result'
    assert len(calls) == 2
    log = json.loads((tmp_path / 'api_errors.jsonl').read_text())
    assert log['http_status'] == 400 and log['provider_message'] == 'Content Exists Risk'
    assert 'rejected-document' not in (tmp_path / 'api_errors.jsonl').read_text()


@pytest.mark.parametrize('status,message', [(401, 'Invalid key'), (400, 'Malformed request'), (403, 'Access denied')])
def test_other_terminal_errors_still_stop_all_jobs(tmp_path, status, message):
    gate = n.NetworkGate(tmp_path, 'https://invalid.example', probe=lambda: True)

    class FakeClient:
        def _chat_once(self, messages, temperature):
            raise http_error(status, message)

    client = n.guarded_client_class(gate, FakeClient)()
    with pytest.raises(n.NonRetryableLLMError):
        client._chat_once([], 0)
    assert gate.cancelled
    reason = gate.reason
    gate.cancel('runner_exited')
    assert gate.reason == reason
