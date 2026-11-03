"""Scripted stand-in for `codex app-server --listen stdio://` used by tests/test_engineering_entrypoint.py.

Each process serves one turn (plan, implement, test or qa), then exits. Cross-process
state (counters, threads started per workspace, QA review count, resumes) lives in the JSON file
named by FAKE_STATE. Environment: OUTPUT (handle output dir), FAKE_STATE (state file),
FAKE_WORKSPACE (per-role workspace, keys the persisted thread).
"""
import hashlib
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

STATE = Path(os.environ['FAKE_STATE'])
OUTPUT = Path(os.environ['OUTPUT'])
WORKSPACE = os.environ['FAKE_WORKSPACE']


def load_state():
    return json.loads(STATE.read_text())


def save_state(state):
    STATE.write_text(json.dumps(state, sort_keys=True))


def send(value):
    sys.stdout.write(json.dumps(value, separators=(',', ':')) + '\n')
    sys.stdout.flush()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run_turn(msg, ident, state):
    thread = msg['params']['threadId']
    payload = json.JSONDecoder().raw_decode(msg['params']['input'][0]['text'].split('\n', 1)[1])[0]
    stage = payload['stage']
    refs = payload['input_versions']
    state['counter'] += 1
    turn = f"turn-{state['counter']}"
    status = 'passed'
    findings = []
    if stage == 'test':
        data = json.dumps({'commands': [{'command': 'synthetic-check', 'exit_code': 0, 'output': 'fixture passed'}]}).encode()
    else:
        data = ('synthetic ' + stage + ' ' + turn).encode()
    (OUTPUT / 'result.txt').write_bytes(data)
    if stage == 'implement':
        content = 'corrected fixture v2' if len(state['resumed']) > 1 else 'corrected fixture' if state['resumed'] else 'implemented fixture'
        Path(WORKSPACE, 'new.txt').write_text(content)
        subprocess.run(['git', 'add', 'new.txt'], cwd=WORKSPACE, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'implementation'], cwd=WORKSPACE, check=True, capture_output=True)
    if stage == 'qa':
        state['canonical_reviews'] += 1
        if state['canonical_reviews'] in (1, 3):
            status = 'blocked'
            findings = [{'id': 'f1', 'severity': 'blocking', 'description': 'Fix fixture', 'location': 'new.txt', 'evidence': refs[1], 'remedy': 'Correct content', 'resolution': 'open'}]
    result = {'stage': stage, 'status': status, 'input_versions': refs, 'outputs': [sha(data)], 'findings': findings}
    if status == 'blocked':
        result['reason'] = 'correction required'
    save_state(state)
    send({'id': ident, 'result': {'turn': {'id': turn}}})
    send({'method': 'item/agentMessage/delta', 'params': {'threadId': thread, 'turnId': turn, 'delta': json.dumps(result)}})
    send({'method': 'turn/completed', 'params': {'threadId': thread, 'turn': {'id': turn, 'status': 'completed'}}})


def serve():
    state = load_state()
    for line in sys.stdin:
        msg = json.loads(line)
        method = msg.get('method')
        ident = msg.get('id')
        if method == 'initialize':
            send({'id': ident, 'result': {}})
        elif method == 'model/list':
            send({'id': ident, 'result': {'data': [{'model': 'fake-terra', 'displayName': 'GPT-6.1 Terra', 'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}]}], 'nextCursor': None}})
        elif method == 'thread/start':
            state['counter'] += 1
            thread = f"thread-{state['counter']}"
            state['threads'].setdefault(WORKSPACE, []).append(thread)
            save_state(state)
            send({'id': ident, 'result': {'thread': {'id': thread}}})
        elif method == 'thread/resume':
            thread = msg['params']['threadId']
            assert thread in state['threads'].get(WORKSPACE, []), 'resume must target a thread started in this workspace'
            state['resumed'].append(thread)
            save_state(state)
            send({'id': ident, 'result': {'thread': {'id': thread}}})
        elif method == 'turn/start':
            run_turn(msg, ident, state)
            return


def record_error():
    state = load_state()
    state['errors'].append(traceback.format_exc())
    save_state(state)
    sys.stderr.write(traceback.format_exc())


if __name__ == '__main__':
    try:
        serve()
    except BaseException:
        record_error()
        sys.exit(1)
