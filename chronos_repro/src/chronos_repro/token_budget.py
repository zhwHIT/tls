"""DeepSeek tokenizer preflight and authoritative server usage audit."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .compact_context import ContextLimitError


class TokenBudgetClient:
    def __init__(self, client, settings):
        from tokenizers import Tokenizer
        self.client = client
        self.model = client.model
        self.settings = settings
        path = Path(settings['tokenizer_path'])
        self.tokenizer = Tokenizer.from_file(str(path))
        self.tokenizer_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        self.records = []
        self.accepted = []
        self.last_exchange = None

    def estimate(self, messages):
        # Official data, with conservative overhead for the server chat wrapper.
        return sum(len(self.tokenizer.encode(m['content'], add_special_tokens=False).ids)
                   for m in messages) + 64 + 12 * len(messages)

    def chat(self, messages, temperature=0):
        self.last_exchange = None
        payload = json.loads(messages[-1]['content'])
        stage = payload.get('stage', '')
        estimate = self.estimate(messages)
        if estimate > self.settings.get('preflight_limit', 3800):
            raise ContextLimitError(f'{stage}: estimated input {estimate} exceeds preflight limit; rebuild or page the state')
        raw = self.client
        while hasattr(raw, 'client'):
            raw = raw.client
        old = copy.deepcopy(getattr(raw, 'request_options', {}))
        raw.request_options = {**old, 'max_tokens': 512 if stage == 'BATCH_POLICY' else 4096}
        try:
            result = self.client.chat(messages, temperature=temperature)
        finally:
            raw.request_options = old
        measured_usage = result.usage or getattr(self.client, 'last_response_usage', {})
        actual = measured_usage.get('prompt_tokens')
        completion = measured_usage.get('completion_tokens')
        record = {'stage': stage, 'estimated_prompt_tokens': estimate,
                  'prompt_tokens': actual, 'completion_tokens': completion,
                  'http_attempts': result.attempts,
                  'new_response_usage': copy.deepcopy(result.usage) if result.attempts else {},
                  'input_limit': self.settings.get('input_limit', 4096),
                  'request_sha256': hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest(),
                  'cache_replay_without_usage': actual is None and result.attempts == 0}
        self.records.append(record)
        if actual is not None and actual > self.settings.get('input_limit', 4096):
            raise ContextLimitError(f'{stage}: server input {actual} exceeds input limit; response quarantined')
        if actual is None and result.attempts != 0:
            raise ContextLimitError('Live response lacks prompt_tokens; token acceptance is unknown')
        if stage == 'BATCH_POLICY' and completion is not None and completion > 512:
            raise ContextLimitError('Controller output exceeds 512 tokens')
        self.last_exchange = {'messages': copy.deepcopy(messages), 'response': result.text,
                              'stage': stage, 'usage': copy.deepcopy(measured_usage)}
        return result

    def accept_last_response(self, checked):
        if self.last_exchange and self.last_exchange['stage'] == 'BATCH_POLICY':
            self.accepted.append({'messages': self.last_exchange['messages'] + [
                {'role': 'assistant', 'content': json.dumps(checked, ensure_ascii=False)}],
                'metadata': {'action': checked['action'], 'usage': self.last_exchange['usage'],
                             'phase': json.loads(self.last_exchange['messages'][-1]['content'])['state']['phase'],
                             'is_repair': 'repair' in json.loads(self.last_exchange['messages'][-1]['content']),
                             'exact_inference_messages': True, 'training_ready': False,
                             'semantic_review_required': True}})
        self.last_exchange = None

    def statistics(self):
        totals = {key: sum(r.get('new_response_usage', {}).get(key, 0) for r in self.records)
                  for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        return {'tokenizer_sha256': self.tokenizer_sha256, 'token_counts': self.records,
                'all_returned_live_responses_usage': totals,
                'returned_live_response_count': sum(bool(r.get('http_attempts')) for r in self.records),
                'accepted_controller_samples': len(self.accepted),
                'tokenizer_note': 'Official DeepSeek offline estimate; API usage is authoritative. Not Qwen validation.',
                'client': self.client.statistics() if hasattr(self.client, 'statistics') else {}}
