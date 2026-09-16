"""Wait for one existing run, then evaluate locally. Never starts an LLM client."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def assessment(trace, evaluation, audit, ledger):
    coverage = evaluation.get('coverage', {})
    gap_memory = trace.get('final_gap_memory', {})
    phases = {s['phase'] for s in trace.get('steps', [])}
    phase2_complete = any(s['phase'] == 'GAP_REFINEMENT' and s['action'] == 'STOP'
                          and not s.get('observation', {}).get('forced') for s in trace.get('steps', []))
    return {'runtime_status': trace.get('status'),
        'full_two_phase_executed': {'SKELETON_EXPLORATION', 'GAP_REFINEMENT'}.issubset(phases),
        'phase2_autonomous_stop': phase2_complete,
        'forced_stops': sum(bool(s.get('observation', {}).get('forced')) for s in trace.get('steps', []) if s['action'] == 'STOP'),
        'gold_date_hits': coverage.get('covered_gold_dates'), 'gold_date_count': coverage.get('gold_date_count'),
        'gold_date_recall': coverage.get('gold_date_recall'),
        'date_target_reached': coverage.get('gold_date_recall', 0) >= .7,
        'evidence_audit_passed': audit.get('valid', False),
        'remaining_gaps': sum(g.get('status') in {'OPEN', 'IN_PROGRESS', 'FAILED'} for g in gap_memory.get('gaps', [])),
        'gap_state_available': bool(gap_memory),
        'semantic_coverage_established': False, 'training_ready': False,
        'http_attempts': ledger.get('requests_started'), 'http_limit': ledger.get('request_limit'),
        'balance_stopped': ledger.get('balance_stop', False), 'new_api_calls_by_evaluator': 0,
        'limitations': ['Tests and lexical/ROUGE scores do not establish semantic faithfulness or training-label quality.',
                       'Missing terminal gap memory must not be interpreted as zero unresolved gaps.']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--report-md', required=True)
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--max-wait-seconds', type=int, default=21600)
    args = parser.parse_args()
    folder = Path(args.run_dir).resolve()
    report_path = Path(args.report_md).resolve()
    project = Path(__file__).resolve().parents[1]
    if sys.prefix.lower() != r'D:\miniforge\envs\tls'.lower():
        raise ValueError('Run this evaluator inside the tls conda environment')
    config = read(folder / 'run_config.json')
    if not config or config.get('phase2_teacher_guidance') is not False:
        raise ValueError('Expected the approved reference-free coverage run')
    status_path = folder / 'evaluation_followup_status.json'
    lock = folder / 'evaluation_followup.lock'
    with lock.open('x', encoding='utf-8') as handle:
        handle.write('One local evaluator is already assigned to this run.\n')
    started = time.monotonic()
    try:
        while not (folder / 'manifest.json').exists():
            elapsed = int(time.monotonic() - started)
            write_json(status_path, {'status': 'waiting_for_existing_run', 'elapsed_seconds': elapsed,
                                     'api_calls': 0, 'launches_llm': False})
            if not args.wait or elapsed >= args.max_wait_seconds:
                raise TimeoutError('Run has not finalized; no API restarted and no training data approved')
            time.sleep(30)
        manifest = read(folder / 'manifest.json')
        trajectory_path = folder / 'trajectory.json'
        if not trajectory_path.exists() or manifest.get('status') in {'running', 'checkpoint'}:
            raise ValueError('Manifest is not a terminal run result')
        write_json(status_path, {'status': 'evaluating_locally', 'api_calls': 0})
        commands = [
            ['audit_coverage_rollout.py', '--run-dir', str(folder), '--index', str(project / config['index']),
             '--topic', config['topic'], '--output', str(folder / 'evidence_audit.json')],
            ['audit_gold_evidence_layers.py', '--run-dir', str(folder), '--data', str(project / config['data']),
             '--index', str(project / config['index']), '--topic', config['topic'], '--rouge'],
        ]
        diagnostics = []
        for script, *arguments in commands:
            result = subprocess.run([sys.executable, str(project / 'scripts' / script), *arguments],
                                    cwd=project, capture_output=True, text=True, encoding='utf-8', errors='replace')
            diagnostics.append({'script': script, 'exit_code': result.returncode,
                                'stdout': result.stdout, 'stderr': result.stderr})
        trace = read(trajectory_path)
        evaluation = read(folder / 'evaluation.json', {})
        audit = read(folder / 'evidence_audit.json', {})
        ledger = read(folder / 'request_ledger.json', {})
        result = assessment(trace, evaluation, audit, ledger)
        result['diagnostic_commands_succeeded'] = all(r['exit_code'] == 0 for r in diagnostics)
        write_json(folder / 'acceptance_report.json', result)
        write_json(folder / 'local_evaluation_commands.json', diagnostics)
        events = trace.get('final_events', [])
        md = ['# Egypt 完整运行与本地验收结果', '',
              '本报告由本地收尾脚本生成，不调用模型，不启动新轨迹，不扩大授权范围。', '',
              f"- 程序状态：`{result['runtime_status']}`。",
              f"- 最终事件：{len(events)}；不同日期：{len({e['time'] for e in events})}。",
              f"- gold 日期命中：{result['gold_date_hits']}/{result['gold_date_count']}；recall：{result['gold_date_recall']}。",
              f"- 日期 recall ≥70%：{result['date_target_reached']}。",
              f"- 两阶段均执行：{result['full_two_phase_executed']}；第二阶段自主停止：{result['phase2_autonomous_stop']}。",
              f"- 强制 STOP 次数：{result['forced_stops']}；终态 gap memory 可用：{result['gap_state_available']}。",
              f"- 来源与日期表达式审计通过：{result['evidence_audit_passed']}。",
              f"- HTTP 累计：{result['http_attempts']}/{result['http_limit']}；余额停止：{result['balance_stopped']}。", '',
              '## 结论边界', '',
              '词汇匹配和 Timeline ROUGE 不是语义蕴含评测。即使日期目标通过，也不自动认为事件覆盖、STOP 判断或训练标签质量通过。',
              '当前 training_ready=false；需要语义复核、精确推理 prompt 对齐与独立数据划分后，才能进入训练。', '',
              '## 产物', '',
              f'运行目录：`{folder}`。', '',
              '- `acceptance_report.json`：分项验收结论。',
              '- `evidence_audit.json`：片段来源、正文偏移、日期表达式检查。',
              '- `gold_evidence_layers.private.json`：本地日期分层诊断与 Timeline ROUGE，禁止作为本次策略输入。',
              '- `local_evaluation_commands.json`：本地评测命令输出与退出码。', '',
              '下一步根据未通过项修复，不自动更换目录重置额度，不自动启动新的付费运行。', '']
        report_path.parent.mkdir(parents=True, exist_ok=True)
        if report_path.exists():
            raise FileExistsError('Preserving an existing report; select another explicit report path')
        report_path.write_text('\n'.join(md), encoding='utf-8')
        write_json(status_path, {'status': 'local_evaluation_finished', 'api_calls': 0,
                                'report_md': str(report_path), 'training_ready': False})
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as error:
        write_json(status_path, {'status': 'stopped_local_error', 'error': str(error), 'api_calls': 0})
        raise
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
