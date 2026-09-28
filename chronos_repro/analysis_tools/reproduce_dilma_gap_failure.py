"""Replay the two saved responses with networking unavailable and verify request hashes."""
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
import batched_search_phase as runtime
from run_tisa_two_phase_annotation import LabelValidationError
from chronos_repro.compact_context import CompactContextClient
from chronos_repro.token_budget import TokenBudgetClient
from chronos_repro.llm import ChatResult
from chronos_repro.runtime_profile import REQUEST_OPTIONS
from chronos_repro.tisa_rollout import validate_gap_memory_action

folder = ROOT / 'artifacts/tisa_v98_resumed_and_unstarted/entities_Dilma_Rousseff'
trace = json.loads((folder / 'trajectory.json').read_text(encoding='utf-8'))
config = json.loads((folder / 'run_config.json').read_text(encoding='utf-8'))
saved_tokens = json.loads((folder / 'token_usage_audit.json').read_text(encoding='utf-8'))['token_counts']


class OfflineCacheOnly:
    model = config['model']
    base_url = 'https://api.deepseek.com'
    request_options = REQUEST_OPTIONS
    def __init__(self): self.exchanges = []
    def chat(self, messages, temperature=0):
        identity = {'model':self.model,'base_url':self.base_url,'temperature':temperature,
                    'messages':messages,'request_options':self.request_options}
        key = hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        path = folder / 'api_cache' / (key + '.json')
        if not path.exists(): raise AssertionError('Offline reconstruction differs from cached request: ' + key)
        stored = json.loads(path.read_text(encoding='utf-8'))
        assert stored['request_sha256'] == key
        response = stored['response']
        wire_hash = hashlib.sha256(json.dumps(messages,sort_keys=True).encode()).hexdigest()
        assert wire_hash == saved_tokens[len(self.exchanges)]['request_sha256']
        self.exchanges.append({'request_sha256':wire_hash,'cache_sha256':key,
                              'payload':json.loads(messages[-1]['content']),
                              'response':json.loads(response['text'])})
        return ChatResult(response['text'],response['model'],response['usage'],response.get('request_id'),attempts=0)


offline = OfflineCacheOnly()
settings = {**config['batch_controller']['tokens'], 'tokenizer_path':str(ROOT / config['batch_controller']['tokens']['tokenizer_path'])}
client = CompactContextClient(TokenBudgetClient(offline, settings), config['compact_context'])
state = {'dataset':trace['dataset'],'topic':trace['topic'],'phase':'GAP_REFINEMENT',
         'timeline_events':copy.deepcopy(trace['final_events']),
         'controller_memory':copy.deepcopy(trace['controller_memory']),
         'gap_memory':copy.deepcopy(trace['final_gap_memory'])}
output = {'steps':[],'audits':[]}
usage = {k:0 for k in ['prompt_tokens','completion_tokens','total_tokens','logical_calls','http_attempts']}
try:
    runtime.discover_gaps(client,state,config,output,usage)
    raise AssertionError('Expected the saved validation failure')
except LabelValidationError as error:
    failure = str(error)

events = {e['event_id']:e for e in trace['final_events']}
rows = []
for exchange in offline.exchanges:
    packet, response = exchange['payload'], exchange['response']
    ids = [e['event_id'] for e in packet['events']]
    gap = response['gaps'][0]; anchor = gap['left_event_id']; quote = gap['retrieval_target']['anchor_quote']
    try:
        validate_gap_memory_action(response,trace['final_events'],3,require_atomic=True)
        expanded_error = None
    except ValueError as error:
        expanded_error = str(error)
    rows.append({'request_sha256':exchange['request_sha256'],'cache_sha256':exchange['cache_sha256'],
        'visible_event_ids':ids,'response_anchor':anchor,'anchor_exists_globally':anchor in events,
        'anchor_in_event_window':anchor in ids,'memory_mentions':[
            f for f in packet['fact_memory'] if anchor in f.get('event_ids',[])],
        'anchor_quote':quote,'global_event_summary':events.get(anchor,{}).get('summary'),
        'quote_verbatim_in_global_summary':quote in events.get(anchor,{}).get('summary',''),
        'validation_if_global_events_were_allowed':expanded_error,
        'repair_constraints':packet.get('repair',{}).get('constraints'),
        'repair_error':packet.get('repair',{}).get('previous_error')})
report = {'api_calls':0,'replayed_cached_responses':len(rows),'exact_request_hashes_matched':True,
          'reproduced_error':failure,'saved_events':len(events),'attempts':rows,
          'runtime_source_changed':False,'original_trajectory_changed':False}
destination = ROOT / 'artifacts/dilma_gap_failure_analysis_20260917.json'
destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=True,indent=2))
