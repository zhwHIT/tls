"""Shared network pause for extraction workers; probes never generate text."""
from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import extract_llm_tls_events as extraction
from chronos_repro.llm import (
    DeepSeekClient, LLMError, RetryableLLMError, NonRetryableLLMError, InsufficientBalanceError,
)


class NetworkPauseStopped(NonRetryableLLMError):
    """A waiting request remains pending when the user stops the run."""


class ContentRejectedError(LLMError):
    """Provider rejected this unchanged document; retain error and continue others."""


def configure_direct_transport():
    """Disable environment and Windows system proxies in this worker process.

    Both API requests and reachability probes use urllib.request.urlopen.
    Install before starting any threads; TLS certificate checks stay enabled.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(opener)


def provider_error_fields(error):
    cause = error.__cause__
    status = cause.code if isinstance(cause, urllib.error.HTTPError) else None
    fields = {'http_status': status, 'error_type': type(error).__name__}
    raw = str(error)
    try:
        body = json.loads(raw[raw.index('{'):])
        detail = body.get('error', {})
        for key in ('message', 'type', 'code'):
            if isinstance(detail.get(key), (str, int)):
                fields['provider_' + key] = str(detail[key])[:500]
    except (ValueError, TypeError, AttributeError):
        pass
    return fields


def is_document_rejection(error):
    fields = provider_error_fields(error)
    return (fields['http_status'] == 400 and
            fields.get('provider_message', '').strip() == 'Content Exists Risk' and
            fields.get('provider_code') == 'invalid_request_error')


def is_transport_error(error):
    cause = error.__cause__
    # HTTP failures prove that a response arrived; normal retry rules apply.
    if isinstance(cause, urllib.error.HTTPError):
        return False
    return isinstance(cause, (urllib.error.URLError, TimeoutError, ConnectionError,
                              http.client.IncompleteRead, http.client.RemoteDisconnected))


def probe_endpoint(base_url):
    """Unauthenticated HEAD request: no summaries, prompts, or API keys sent."""
    request = urllib.request.Request(base_url.rstrip('/') + '/', method='HEAD',
                                     headers={'User-Agent': 'TLS-Network-Reachability'})
    try:
        with urllib.request.urlopen(request, timeout=5):
            return True
    except urllib.error.HTTPError:
        # 401/404/405 still demonstrate a working DNS/TLS/HTTP connection.
        return True
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
            http.client.HTTPException):
        return False


class NetworkGate:
    def __init__(self, output, base_url, interval=30.0, probe=None):
        self.output = Path(output)
        self.base_url = base_url
        self.interval = interval
        self.probe = probe or (lambda: probe_endpoint(base_url))
        self.condition = threading.Condition()
        self.error_log_lock = threading.Lock()
        self.paused = True
        self.cancelled = False
        self.probing = False
        self.next_probe = 0.0
        self.epoch = 0
        self.failures = 0
        self.probes = 0
        self.resumes = 0
        self.pause_started = extraction.now()
        self.reason = 'startup_connectivity_check'
        self._save()

    def _save(self):
        extraction.atomic_write_json(self.output / 'network_status.json', {
            'stage': 'cancelled' if self.cancelled else ('paused_network' if self.paused else 'online'),
            'reason': self.reason, 'paused_since_utc': self.pause_started,
            'updated_at_utc': extraction.now(), 'network_failures': self.failures,
            'probe_count': self.probes, 'resume_count': self.resumes,
            'probe_interval_seconds': self.interval,
            'probe_generates_text': False, 'in_flight_requests_may_finish': True,
        })

    def cancel(self, reason):
        with self.condition:
            if self.cancelled:
                return  # Preserve the first root cause, not a later worker cancellation.
            self.cancelled = True
            self.reason = reason
            self._save()
            self.condition.notify_all()

    def record_api_error(self, error, messages, action):
        fields = provider_error_fields(error)
        request_hash = extraction.digest(extraction.dumps(messages))
        row = {'at_utc': extraction.now(), 'request_sha256': request_hash,
               'action': action, **fields}
        identity_path = self.output / 'run_identity.json'
        if identity_path.exists():
            identity = json.loads(identity_path.read_text(encoding='utf-8'))
            row['job_id'] = extraction.digest(extraction.dumps({
                'identity': extraction.digest(extraction.dumps(identity)), 'messages': messages}))
        # Prompts, authorization headers and credentials are never logged here.
        with self.error_log_lock:
            with (self.output / 'api_errors.jsonl').open('a', encoding='utf-8') as f:
                f.write(extraction.dumps(row) + '\n')

    def mark_offline(self, error):
        with self.condition:
            self.failures += 1
            self.epoch += 1
            if not self.paused:
                self.pause_started = extraction.now()
                self.next_probe = time.monotonic() + self.interval
            self.paused = True
            self.reason = type(error.__cause__).__name__
            self._save()
            self.condition.notify_all()

    def before_request(self):
        while True:
            with self.condition:
                if self.cancelled or (self.output / 'STOP').exists():
                    raise NetworkPauseStopped('Paused request retained for a later run')
                if not self.paused:
                    return
                delay = self.next_probe - time.monotonic()
                if self.probing or delay > 0:
                    self.condition.wait(timeout=min(0.5, max(0.01, delay)) if delay > 0 else 0.5)
                    continue
                self.probing = True
                probe_epoch = self.epoch
            try:
                reachable = self.probe()
            except Exception:
                reachable = False
            with self.condition:
                self.probing = False
                self.probes += 1
                if reachable and probe_epoch == self.epoch and not self.cancelled:
                    self.paused = False
                    self.pause_started = None
                    self.reason = 'endpoint_reachable'
                    self.resumes += 1
                self.next_probe = time.monotonic() + self.interval
                self._save()
                self.condition.notify_all()


def guarded_client_class(gate, base_class=DeepSeekClient):
    class NetworkPausedClient(base_class):
        def _chat_once(self, messages, temperature=0.0):
            while True:
                gate.before_request()
                try:
                    return super()._chat_once(messages, temperature)
                except RetryableLLMError as error:
                    if not is_transport_error(error):
                        gate.record_api_error(error, messages, 'bounded_retry')
                        raise
                    # Keep this job in the worker, not in the failed-job bucket.
                    gate.mark_offline(error)
                except (InsufficientBalanceError, NonRetryableLLMError) as error:
                    if is_document_rejection(error):
                        gate.record_api_error(error, messages, 'retain_rejected_document_continue_other_jobs')
                        raise ContentRejectedError('Provider returned HTTP 400 Content Exists Risk') from error
                    gate.record_api_error(error, messages, 'stop_all_jobs')
                    gate.cancel(type(error).__name__)
                    raise
    return NetworkPausedClient


def main():
    configure_direct_transport()
    cfg = extraction.read_config(extraction.PROJECT / 'configs/llm_tls_extraction_v1.json')
    output = Path(cfg['output'])
    # Transport policy is recorded separately: prompts/model/cache keys and
    # the original extraction implementation remain bit-for-bit unchanged.
    extraction.atomic_write_json(output / 'transport_policy.json', {
        'policy': 'direct_pause_network_isolate_explicit_content_rejection_v3',
        'proxy_policy': 'disabled: explicit urllib ProxyHandler({}) for API and probes',
        'script_sha256': extraction.file_hash(__file__), 'installed_at_utc': extraction.now(),
        'network_probe': 'HEAD provider root without API key', 'probe_interval_seconds': 30,
        'network_resume': 'automatic_after_reachability',
        'ordinary_http_retry_policy': 'unchanged',
        'document_rejection_policy': 'HTTP 400 Content Exists Risk: record error, no retry or rewriting, continue other jobs',
        'note': 'A lost response may have been processed remotely; billing cannot be inferred from reachability.',
    })
    gate = NetworkGate(output, cfg['base_url'])
    extraction.DeepSeekClient = guarded_client_class(gate)
    try:
        extraction.run(cfg)
    finally:
        root_stop_reason = gate.reason if gate.cancelled else None
        gate.cancel('runner_exited')
        # STOP during a network wait must not permanently fail unsubmitted jobs.
        # Do not touch errors of any other type or completed responses.
        con = extraction.connect(output)
        count = con.execute("update jobs set status='pending',error=null where status='error' and error='NetworkPauseStopped'").rowcount
        con.commit()
        if count or root_stop_reason:
            extraction.export(cfg, con)
            report = extraction.status(con)
            report.update({'stage': 'stopped', 'stop_reason': root_stop_reason or 'user_stop_during_network_wait',
                           'waiting_jobs_restored_to_pending': count})
            extraction.atomic_write_json(output / 'progress.json', report)
        con.close()


if __name__ == '__main__':
    main()
