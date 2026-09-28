"""Add diagnostic sidecars; never rewrite original trajectories or training gates."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def mark(folder):
    path = folder / 'trajectory.json'
    if not path.exists(): path = folder / 'checkpoint.json'
    if not path.exists(): return None
    raw = path.read_bytes(); trace = json.loads(raw)
    flagged = []
    for index, step in enumerate(trace['steps']):
        if step.get('snapshot_metadata', {}).get('detached_from_live_state') is True: continue
        packet = step.get('model_input', {})
        if packet.get('stage') != 'GAP_STATUS': continue
        gap = packet.get('gap', {}); attempts = set(gap.get('attempted_queries', []))
        proof = None
        for later in trace['steps'][index + 1:]:
            active = later.get('model_input', {}).get('state', {}).get('active_gap') or {}
            if active.get('gap_id') == gap.get('gap_id'):
                leaked = attempts - set(active.get('attempted_queries', []))
                if leaked:
                    proof = {'later_step_id': later['step_id'], 'future_queries_in_earlier_input': sorted(leaked)}
                    break
        flagged.append({'step_id':step['step_id'],'gap_id':gap.get('gap_id'),
            'status':'confirmed_future_query_backwrite' if proof else 'potential_shared_state_backwrite',
            'evidence':proof,'training_eligible':False})
    gate_path = folder / 'training_gate.json'
    gate = json.loads(gate_path.read_text(encoding='utf-8')) if gate_path.exists() else None
    report = {'observed_at':datetime.now().astimezone().isoformat(),
        'trajectory_file':path.name,'trajectory_sha256':hashlib.sha256(raw).hexdigest(),
        'runtime_status':trace['status'],'training_ready':False,'diagnostic_only':True,
        'reason':'Legacy step payloads were stored by reference; exact historical input requires independent evidence.',
        'original_files_unchanged':True,'original_training_gate':gate,'flagged_steps':flagged,
        'scope_note':'Flags refer to this file hash. Root policy also covers later files from the same frozen batch.'}
    destination = folder / 'diagnostic_integrity.json'
    # Separate sidecar is derived audit material; atomic replacement supports repeat audits of live checkpoints.
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(destination)
    return {'run':folder.relative_to(ROOT).as_posix(),'snapshot':path.name,'steps_flagged':len(flagged),
        'confirmed_steps':sum(f['status']=='confirmed_future_query_backwrite' for f in flagged),
        'existing_training_ready':gate.get('training_ready') if gate else None}


if __name__ == '__main__':
    reports = []
    for name in ['tisa_v97_authorized','tisa_v98_resumed_and_unstarted']:
        root = ROOT / 'artifacts' / name
        policy = {'diagnostic_only':True,'training_ready':False,
            'applies_to':'All existing and future topic outputs in this frozen legacy batch.',
            'requirements':['Review legacy input integrity before using step-derived labels.',
                            'Independent wire-message exports still require semantic and split review.'],
            'does_not_modify_original_trajectories_or_gates':True}
        (root/'diagnostic_integrity_policy.json').write_text(json.dumps(policy,indent=2)+'\n',encoding='utf-8')
        for folder in sorted(root.iterdir()):
            if folder.is_dir():
                result = mark(folder)
                if result: reports.append(result)
    out = ROOT / 'artifacts/legacy_integrity_marking_20260917.json'
    out.write_text(json.dumps({'observed_at':datetime.now().astimezone().isoformat(),'runs':reports},indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'runs':len(reports),'flagged_steps':sum(r['steps_flagged'] for r in reports),
                      'confirmed_steps':sum(r['confirmed_steps'] for r in reports),'report':str(out)},indent=2))
