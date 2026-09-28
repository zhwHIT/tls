"""Persistent request accounting with optional uncapped VERIFY transport."""
import json
from pathlib import Path

from .llm import DeepSeekClient, LLMError, InsufficientBalanceError


class RequestLimitError(LLMError):
    pass


def budgeted_requests(ledger):
    return ledger.get('budgeted_requests_started', ledger['requests_started'])


def verify_request(messages):
    try:
        payload = json.loads(messages[-1]['content'])
        return isinstance(payload, dict) and payload.get('stage') == 'VERIFY'
    except (ValueError, KeyError, IndexError, TypeError):
        return False


class LimitedDeepSeekClient(DeepSeekClient):
    def __init__(self, *args, request_limit: int, request_ledger_path=None, initial_requests=0,
                 unlimited_verify=False, **kwargs):
        if request_limit < 1:
            raise ValueError('request_limit must be positive')
        super().__init__(*args, **kwargs)
        self.request_limit = request_limit
        if type(unlimited_verify) is not bool:
            raise ValueError('unlimited_verify must be Boolean')
        self.unlimited_verify = unlimited_verify
        self.http_requests_started = 0
        self.request_ledger_path = Path(request_ledger_path) if request_ledger_path else None
        self.ledger = {'request_limit': request_limit, 'requests_started': initial_requests, 'balance_stop': False}
        if self.request_ledger_path and self.request_ledger_path.exists():
            self.ledger = json.loads(self.request_ledger_path.read_text(encoding='utf-8'))
        if 'unlimited_verify' in self.ledger and self.ledger['unlimited_verify'] != unlimited_verify:
            raise ValueError('Cannot change request accounting mode in an existing ledger')
        if unlimited_verify and 'unlimited_verify' not in self.ledger:
            self.ledger.update(unlimited_verify=True, budgeted_requests_started=self.ledger['requests_started'],
                               verify_requests_started=0, legacy_unclassified_requests=self.ledger['requests_started'])
        if (self.ledger['request_limit'] != request_limit or
                type(self.ledger['requests_started']) is not int or self.ledger['requests_started'] < 0):
            raise ValueError('Invalid request ledger or changed authorization ceiling')
        if unlimited_verify:
            counts = [self.ledger.get(k) for k in ('budgeted_requests_started', 'verify_requests_started',
                                                  'legacy_unclassified_requests')]
            if (any(type(n) is not int or n < 0 for n in counts)
                    or counts[0] + counts[1] != self.ledger['requests_started'] or counts[2] > counts[0]):
                raise ValueError('Invalid classified request ledger')

    @property
    def http_requests_total(self):
        return self.ledger['requests_started']

    def _save_ledger(self):
        if self.request_ledger_path:
            self.request_ledger_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.request_ledger_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.ledger, indent=2) + '\n', encoding='utf-8')
            from .atomic_io import replace_with_retry
            replace_with_retry(temporary, self.request_ledger_path)

    def _chat_once(self, messages, temperature):
        if self.ledger['balance_stop']:
            raise InsufficientBalanceError('Previous balance error recorded; no further API calls permitted')
        exempt = self.unlimited_verify and verify_request(messages)
        if not exempt and budgeted_requests(self.ledger) >= self.request_limit:
            raise RequestLimitError('Per-invocation API request ceiling reached; stop without claiming completion')
        self.http_requests_started += 1
        self.ledger['requests_started'] += 1
        if self.unlimited_verify:
            self.ledger['verify_requests_started' if exempt else 'budgeted_requests_started'] += 1
        self._save_ledger()  # Reserve before transport; even interrupted attempts count.
        try:
            return super()._chat_once(messages, temperature)
        except InsufficientBalanceError:
            self.ledger['balance_stop'] = True
            self._save_ledger()
            raise
