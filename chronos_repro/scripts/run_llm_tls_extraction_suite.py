"""Durable supervisor for the user-authorized full three-dataset extraction."""
import json
import argparse
import ctypes
import os
import subprocess
import sys
from pathlib import Path

import extract_llm_tls_events as e


def wait_for_adopted_prepare(pid):
    """Wait for the already-running Windows preparation process, without killing it."""
    if os.name != 'nt':
        raise RuntimeError('Preparation handoff is implemented for this Windows workspace')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:  # Process already exited.
            return
        raise OSError(ctypes.get_last_error(), 'Cannot wait for preparation process')
    try:
        while True:
            outcome = kernel.WaitForSingleObject(handle, 1000)
            if outcome == 0:
                return
            if outcome != 258:
                raise RuntimeError('Waiting for preparation process failed')
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adopt-prepare-pid', type=int)
    parser.add_argument('--skip-prepare', action='store_true')
    args = parser.parse_args()
    cfg = e.read_config(e.PROJECT / 'configs/llm_tls_extraction_v1.json')
    output = Path(cfg['output'])
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / 'suite.lock'
    with lock_path.open('x', encoding='utf-8') as lock:
        lock.write(e.dumps({'pid': os.getpid(), 'started_at_utc': e.now()}))
    state = {'supervisor_pid': os.getpid(), 'started_at_utc': e.now(), 'stages': [],
             'datasets': list(cfg['datasets']), 'completed': False}
    try:
        pilot_path = output / 'pilot_report.json'
        if not pilot_path.exists():
            raise RuntimeError('Run and inspect the authorized API pilot before the full suite')
        pilot = json.loads(pilot_path.read_text(encoding='utf-8'))
        if not pilot['passed']:
            raise RuntimeError('Pilot output format failed')
        # Keep all actual pilot billing, including duplicate source requests in
        # the first pilot; cache aggregation alone would undercount that cost.
        unique_pilot, extra_pilot = {}, []
        for row in pilot['rows']:
            if row['job_id'] in unique_pilot:
                extra_pilot.append(row)
            unique_pilot[row['job_id']] = row
        state['pilot_total_returned_usage'] = pilot['usage']
        state['pilot_duplicate_requests'] = len(extra_pilot)
        state['pilot_duplicate_usage_not_in_unique_job_totals'] = {
            k: sum(r['response']['usage'].get(k, 0) for r in extra_pilot)
            for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        for stage in ('prepare', 'run'):
            if (output / 'STOP').exists():
                state['stage'] = 'stopped_before_' + stage
                break
            if stage == 'prepare' and args.skip_prepare:
                con = e.connect(output, readonly=True)
                try:
                    if not e.meta_get(con, 'prepared') or not (output / 'preparation_report.json').exists():
                        raise RuntimeError('Cannot skip unfinished preparation')
                finally:
                    con.close()
                state['stages'].append({'stage': stage, 'reused_completed_preparation': True})
                continue
            state['stage'] = stage
            state['updated_at_utc'] = e.now()
            e.atomic_write_json(output / 'suite_status.json', state)
            if stage == 'prepare' and args.adopt_prepare_pid:
                state['child_pid'] = args.adopt_prepare_pid
                state['network_pause_enabled_for_extraction'] = True
                state['handoff'] = 'adopt_existing_preparation_without_restarting_it'
                e.atomic_write_json(output / 'suite_status.json', state)
                wait_for_adopted_prepare(args.adopt_prepare_pid)
                con = e.connect(output, readonly=True)
                try:
                    prepared = e.meta_get(con, 'prepared')
                finally:
                    con.close()
                if not prepared or not (output / 'preparation_report.json').exists():
                    state['stage'] = 'prepare_failed'
                    break
                state['stages'].append({'stage': stage, 'returncode': 0, 'finished_at_utc': e.now(),
                                        'adopted_existing_process': True})
                continue
            with (output / f'{stage}.log').open('a', encoding='utf-8') as log:
                command = ([sys.executable, '-u', str(Path(__file__).with_name('llm_tls_network_pause.py'))]
                           if stage == 'run' else
                           [sys.executable, '-u', str(Path(__file__).with_name('extract_llm_tls_events.py')), stage])
                state['network_pause_enabled_for_extraction'] = True
                child = subprocess.Popen(command,
                                         cwd=e.PROJECT, stdout=log, stderr=subprocess.STDOUT)
                state['child_pid'] = child.pid
                e.atomic_write_json(output / 'suite_status.json', state)
                returncode = child.wait()
            state['stages'].append({'stage': stage, 'returncode': returncode, 'finished_at_utc': e.now()})
            if returncode:
                state['stage'] = stage + '_failed'
                break
        else:
            report = json.loads((output / 'export_report.json').read_text(encoding='utf-8'))
            state['completed'] = report['complete']
            state['all_outputs_parse_clean'] = report['all_outputs_parse_clean']
            state['stage'] = 'completed' if report['complete'] else 'incomplete'
            state['total_returned_usage'] = {
                k: report['usage'][k] + state['pilot_duplicate_usage_not_in_unique_job_totals'][k]
                for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        state['updated_at_utc'] = e.now()
        e.atomic_write_json(output / 'suite_status.json', state)
    finally:
        lock_path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
