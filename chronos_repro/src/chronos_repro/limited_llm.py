"""Per-invocation HTTP request ceiling; never exposed to the student policy."""
import json
from pathlib import Path

from .llm import DeepSeekClient, LLMError, InsufficientBalanceError


class RequestLimitError(LLMError):
    pass


class LimitedDeepSeekClient(DeepSeekClient):
    def __init__(self, *args, request_limit: int, request_ledger_path=None, initial_requests=0, **kwargs):
        if request_limit < 1:
            raise ValueError('request_limit must be positive')
        super().__init__(*args, **kwargs)
        self.request_limit = request_limit
        self.http_requests_started = 0
        self.request_ledger_path = Path(request_ledger_path) if request_ledger_path else None
        self.ledger = {'request_limit': request_limit, 'requests_started': initial_requests, 'balance_stop': False}
        if self.request_ledger_path and self.request_ledger_path.exists():
            self.ledger = json.loads(self.request_ledger_path.read_text(encoding='utf-8'))
        if (self.ledger['request_limit'] != request_limit or
                type(self.ledger['requests_started']) is not int or self.ledger['requests_started'] < 0):
            raise ValueError('Invalid request ledger or changed authorization ceiling')

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
        if self.http_requests_total >= self.request_limit:
            raise RequestLimitError('Per-invocation API request ceiling reached; stop without claiming completion')
        self.http_requests_started += 1
        self.ledger['requests_started'] += 1
        self._save_ledger()  # Reserve before transport; even interrupted attempts count.
        try:
            return super()._chat_once(messages, temperature)
        except InsufficientBalanceError:
            self.ledger['balance_stop'] = True
            self._save_ledger()
            raise
