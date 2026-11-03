"""Scripted stand-in for `codex app-server --listen stdio://` used by tests/test_stage_entrypoint.py.

Each process serves one model discovery or one turn, then exits. Cross-process state
(counters, the resumable thread, and the bad-asset-brief switch) lives in the JSON file
named by FAKE_STATE. Environment: OUTPUT (handle output dir), FAKE_ROOT (worker temp root),
FAKE_STATE (state file), FAKE_WORKSPACE (per-role workspace, keys the persisted thread),
FAKE_WITH_SNAPSHOTS ('1' or '0').
"""
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from runner.artifacts import ArtifactStore
from runner.graph import GraphStore
from tests.source_fixture import REGISTER
from workflows.marketing.gates import CHANNELS
from workflows.marketing.openfilm_brief import social_draft_inputs

STATE = Path(os.environ['FAKE_STATE'])
ROOT = Path(os.environ['FAKE_ROOT'])
OUTPUT = Path(os.environ['OUTPUT'])
WORKSPACE = os.environ['FAKE_WORKSPACE']
WITH_SNAPSHOTS = os.environ.get('FAKE_WITH_SNAPSHOTS') == '1'


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
    text = msg['params']['input'][0]['text']
    state['counter'] += 1
    turn = f"turn-{state['counter']}"
    if stage == 'source_review':
        state['source_reviews'] += 1
        data = json.dumps({
            'register_ref': refs[0],
            'verifier_id': 'synthetic-independent-verifier',
            'checks': [{'claim_id': 'claim-1', 'source_id': 'source-1', 'evidence_id': 'evidence-1', 'supported': True, 'primary': True}],
            'draft_ref': refs[1] if payload.get('draft') is not None else None,
            'omitted_claims': ['unregistered material claim'] if state['source_reviews'] == 2 else [],
            'coverage_complete': state['source_reviews'] != 2,
        }).encode()
        (OUTPUT / 'verification.json').write_bytes(data)
    elif stage == 'asset_brief':
        assert 'The pipeline attaches the stored draft of each channel to its assets' in text
        graph = GraphStore(str(ROOT / 'state.db'))
        try:
            drafts = social_draft_inputs(SimpleNamespace(graph=graph, artifacts=ArtifactStore(str(ROOT / 'artifacts'))), 'run-1')
        finally:
            graph.close()
        bad = state['bad_asset_brief']
        data = json.dumps({
            'content_ref': refs[0],
            'social_drafts': {channel: item['ref'] for channel, item in drafts.items()},
            'assets': [{'channel': channel, 'kind': 'png', 'width': 1080, 'height': 1080, 'duration_seconds': None,
                        'approved_text': ['invented text'] if bad else [payload['task']],
                        'claim_ids': ['invented-claim'] if bad else ['claim-1'],
                        'media': [], 'captions': '', 'alt_text': 'Synthetic graphic'} for channel in CHANNELS],
        }).encode()
        (OUTPUT / 'result.txt').write_bytes(data)
    else:
        data = (('# Fixture\n\n' if stage == 'canonical_draft' else '') + 'synthetic ' + stage + ' ' + turn).encode()
        (OUTPUT / 'result.txt').write_bytes(data)
    outputs = [sha(data)]
    if stage == 'brief':
        source_data = json.dumps(REGISTER).encode()
        (OUTPUT / 'sources.json').write_bytes(source_data)
        outputs.append(sha(source_data))
    if stage.endswith('_draft') and stage != 'canonical_draft':
        assert 'ready for independent review' in text
    if stage == 'canonical_draft' and state['resumed']:
        assert 'source_review_judgment' in payload['artifacts']
        assert 'unregistered material claim' in payload['artifacts']['source_review_judgment']
    if stage == 'canonical_review':
        assert 'Independently review the supplied canonical draft' in text
        assert 'do not require rendered media at this stage' in text
        assert 'synthetic brief' in payload['artifacts']['brief_and_sources']
        assert len(refs) == (6 if WITH_SNAPSHOTS else 5) and 'source_register' in payload['artifacts'] and 'source_verification' in payload['artifacts'] and 'page_rules' in payload['artifacts']
        assert ('source_snapshots' in payload['artifacts']) == WITH_SNAPSHOTS
        state['canonical_reviews'] += 1
    result = {'stage': stage, 'status': 'passed', 'input_versions': refs, 'outputs': outputs, 'findings': []}
    save_state(state)
    send({'id': ident, 'result': {'turn': {'id': turn}}})
    if stage == 'source_review':
        send({'method': 'item/completed', 'params': {'threadId': thread, 'turnId': turn, 'item': {'type': 'mcpToolCall', 'id': 'evidence-1', 'server': 'source-evidence', 'tool': 'fetch_primary', 'status': 'completed', 'arguments': {'url': 'https://example.invalid/evidence'}, 'result': {'content': [{'type': 'text', 'text': 'fixture primary source'}]}}}})
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
            state['threads'][WORKSPACE] = thread
            save_state(state)
            send({'id': ident, 'result': {'thread': {'id': thread}}})
        elif method == 'thread/resume':
            thread = msg['params']['threadId']
            assert state['threads'].get(WORKSPACE) == thread, 'resume must target the thread persisted for this role'
            state['resumed'].append(thread)
            save_state(state)
            send({'id': ident, 'result': {'thread': {'id': thread}}})
        elif method == 'turn/start':
            run_turn(msg, ident, state)
            return
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
