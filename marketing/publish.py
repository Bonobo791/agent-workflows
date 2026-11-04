"""Posting after the owner approves 09-publish: the queue, the retries and the event log.

This module does not import run.py (run.py imports this one). Every HTTP call goes through an `http`
callable, so tests never reach the network.
"""
import calendar
import collections
import fcntl
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

RETRY_MINUTES = [5, 10, 20, 40]
MAX_ATTEMPTS = 5
HTTP_TIMEOUT = 60      # seconds, every request except the YouTube upload PUT
UPLOAD_TIMEOUT = 900   # seconds, the YouTube upload PUT
PLATFORM_NAMES = {'website': 'website', 'linkedin': 'LinkedIn', 'x': 'X', 'instagram': 'Instagram', 'youtube': 'YouTube'}
SENT_STOPPED = 'the publish request was sent but the run stopped before the answer; check the account'


class PublishError(Exception):
    """Stops one campaign's run: bad config, or a plan that changed after approval."""


class NetworkError(Exception):
    """Raised only by http_request."""


class RetryLater(Exception):
    def __init__(self, message, wait_seconds=None):
        super().__init__(message)
        self.wait_seconds = wait_seconds


class StopItem(Exception):
    """The item fails at once."""


class OutcomeUnknown(Exception):
    """A publish call was sent and no clear answer came back. The item is never sent again by the timer."""


Response = collections.namedtuple('Response', ['status', 'headers', 'body'])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # every 3xx comes back as a Response; nothing is followed


OPENER = urllib.request.build_opener(NoRedirect)


def lower_headers(headers):
    return {key.lower(): value for key, value in headers.items()}


def http_request(method, url, headers=None, body=None, timeout=HTTP_TIMEOUT):
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with OPENER.open(request, timeout=timeout) as answer:
            return Response(answer.status, lower_headers(answer.headers), answer.read())
    except urllib.error.HTTPError as error:  # must come before URLError, which HTTPError subclasses
        with error:
            return Response(error.code, lower_headers(error.headers), error.read())
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise NetworkError(str(error)) from None


def now_text(epoch):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))


def parse_time(text):
    return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def header_wait(response, epoch):
    if 'retry-after' in response.headers:
        return int(response.headers['retry-after'])
    if 'x-rate-limit-reset' in response.headers:
        return int(response.headers['x-rate-limit-reset']) - int(epoch)
    return None


def check(response, ok, what, publish_call, epoch):
    if response.status in ok:
        return response
    message = f'{what}: HTTP {response.status}: {response.body.decode("utf-8", "replace")[:300]}'
    if response.status == 429:
        raise RetryLater(message, header_wait(response, epoch))
    if 500 <= response.status <= 599:
        if publish_call and response.status != 503:
            raise OutcomeUnknown(message)
        raise RetryLater(message)
    raise StopItem(message)


def send(http, what, publish_call, epoch, *args, **kwargs):
    try:
        return http(*args, **kwargs)
    except NetworkError as error:
        if publish_call:
            raise OutcomeUnknown(f'{what}: the request was sent but no answer came back ({error})') from None
        raise RetryLater(f'{what}: no answer ({error})') from None


def load_config(path):
    if not path.is_file():
        raise PublishError(f'no config at {path}; see the README')
    if path.stat().st_mode & 0o077:
        raise PublishError(f'{path} must be readable only by you: chmod 600 {path}')
    try:
        config = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise PublishError(f'{path} is not valid JSON: {error}') from None
    if not isinstance(config, dict):
        raise PublishError(f'{path} must hold a JSON object')
    return config


def append_event(step_dir, clock, item_id, event, **fields):
    path = step_dir / 'publish.jsonl'
    if path.is_file():
        data = path.read_bytes()
        if data and not data.endswith(b'\n'):  # a record cut off by a crash never completed: remove it
            path.write_bytes(data[:data.rfind(b'\n') + 1])
    record = {'time': now_text(clock()), 'item': item_id, 'event': event, **fields}
    with path.open('a') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def read_events(step_dir):
    path = step_dir / 'publish.jsonl'
    if not path.is_file():
        return []
    # Every record ends with a newline, so the text after the last one was cut off by a crash.
    # That record never completed: a 'sent' record is written before its call, so the call was not made.
    lines = path.read_text().split('\n')[:-1]
    return [json.loads(line) for line in lines if line.strip()]


def item_states(step_dir, plan, epoch):
    events = read_events(step_dir)
    states = {}
    for item in plan['items']:
        mine = [event for event in events if event['item'] == item['id']]
        state = {'state': 'pending', 'attempts': 0, 'next_attempt_at': None, 'post_id': None, 'url': None, 'error': None, 'data': {}}
        for event in mine:
            kind = event['event']
            if kind == 'attempt':
                state['attempts'] = event['attempt']
            elif kind == 'progress':
                state['data'].update(event['data'])
            elif kind == 'posted':
                state.update(state='posted', post_id=event['post_id'], url=event['url'])
            elif kind == 'retry':
                state.update(state='waiting', error=event['error'], next_attempt_at=event['next_attempt_at'])
            elif kind == 'failed':
                state.update(state='failed', error=event['error'])
            elif kind == 'unknown':
                state.update(state='unknown', error=event['error'])
            elif kind == 'reset':
                state.update(state='pending', attempts=0, data={}, error=None)
        if mine and mine[-1]['event'] == 'sent':
            state.update(state='unknown', error=SENT_STOPPED)
        elif mine and mine[-1]['event'] in ('attempt', 'progress'):
            state.update(state='waiting', next_attempt_at=now_text(epoch))  # stopped while preparing: nothing public happened
        states[item['id']] = state
    for item in plan['items']:  # dependencies come before their dependants in the plan, so one pass is enough
        state = states[item['id']]
        if state['state'] not in ('posted', 'failed', 'unknown') and any(states[dep]['state'] in ('failed', 'unknown', 'blocked') for dep in item['depends_on']):
            state['state'] = 'blocked'
    return states


def due(item, states, approved_at, epoch):
    state = states[item['id']]
    if state['state'] == 'waiting':
        if parse_time(state['next_attempt_at']) > epoch:
            return False
    elif state['state'] != 'pending':
        return False
    if parse_time(item['not_before'] or approved_at) > epoch:
        return False
    return all(states[dep]['state'] == 'posted' for dep in item['depends_on'])


class ItemContext:
    def __init__(self, campaign, step_dir, item, states, config, http, clock, sleep, data, attempt):
        self.campaign = campaign
        self.step_dir = step_dir
        self.item = item
        self.states = states
        self.config = config
        self.http = http
        self.clock = clock
        self.sleep = sleep
        self.data = data
        self.attempt = attempt

    def save(self, data):
        self.data.update(data)
        append_event(self.step_dir, self.clock, self.item['id'], 'progress', attempt=self.attempt, data=data)

    def sent(self):
        append_event(self.step_dir, self.clock, self.item['id'], 'sent', attempt=self.attempt)


def advice(kind, campaign, item):
    command = f"python3 marketing/run.py publish --retry {campaign.name} {item['id']}"
    if kind == 'unknown':
        return f"Check the {PLATFORM_NAMES.get(item['platform'], item['platform'])} account; if the post is not there, run: {command}"
    return f'Fix the cause, then run: {command}'


def attempt_item(campaign, step_dir, item, states, config, http, clock, sleep, adapters):
    item_id = item['id']
    attempt = states[item_id]['attempts'] + 1
    append_event(step_dir, clock, item_id, 'attempt', attempt=attempt)
    ctx = ItemContext(campaign, step_dir, item, states, config, http, clock, sleep, dict(states[item_id]['data']), attempt)
    try:
        post_id, url = adapters[item['platform']](ctx)
    except RetryLater as error:
        if attempt < MAX_ATTEMPTS:
            wait = RETRY_MINUTES[attempt - 1] * 60
            if error.wait_seconds is not None:
                wait = max(wait, error.wait_seconds)
            next_at = now_text(clock() + wait)
            append_event(step_dir, clock, item_id, 'retry', attempt=attempt, error=str(error), next_attempt_at=next_at)
            print(f'{campaign.name} {item_id}: waiting until {next_at}: {error}')
            return
        report_failure(campaign, step_dir, item, clock, 'failed', attempt, error)
    except StopItem as error:
        report_failure(campaign, step_dir, item, clock, 'failed', attempt, error)
    except OutcomeUnknown as error:
        report_failure(campaign, step_dir, item, clock, 'unknown', attempt, error)
    else:
        append_event(step_dir, clock, item_id, 'posted', attempt=attempt, post_id=post_id, url=url)
        print(f'{campaign.name} {item_id}: posted {url}')


def report_failure(campaign, step_dir, item, clock, kind, attempt, error):
    append_event(step_dir, clock, item['id'], kind, attempt=attempt, error=str(error))
    print(f"error: {campaign.name} {item['id']}: {kind}: {str(error).rstrip('.')}. {advice(kind, campaign, item)}", file=sys.stderr)


def add_published_list(campaign, plan, states):
    index = campaign / 'final' / 'index.md'
    text = index.read_text()
    if '## Published' in text:
        return
    lines = [f"- {item['id']}: {states[item['id']]['url']}" for item in plan['items'] if item['id'] != 'article']
    index.write_text(text.rstrip('\n') + '\n\n## Published\n\n' + '\n'.join(lines) + '\n')


def publish_campaign(campaign, config, http, clock=time.time, sleep=time.sleep, adapters=None):
    adapters = ADAPTERS if adapters is None else adapters
    step_dir = campaign / '09-publish'
    approval = json.loads((step_dir / 'APPROVED').read_text())
    plan_path = step_dir / 'plan.json'
    if not plan_path.is_file() or file_hash(plan_path) != approval['plan_sha256']:
        raise PublishError(f'{campaign.name}: plan.json changed after approval; nothing was sent')
    plan = json.loads(plan_path.read_text())
    for item in plan['items']:
        if item['platform'] not in adapters:
            raise PublishError(f"{campaign.name}: no publisher for {item['platform']}; nothing was sent")
        for media in item.get('media', []):
            path = step_dir / media['path']
            if not path.is_file() or file_hash(path) != media['sha256']:
                raise PublishError(f"{campaign.name}: {media['path']} changed after approval; nothing was sent")
    while True:
        ran = False
        for item in plan['items']:
            states = item_states(step_dir, plan, clock())
            if due(item, states, approval['approved_at'], clock()):
                attempt_item(campaign, step_dir, item, states, config, http, clock, sleep, adapters)
                ran = True
        if not ran:
            break
    states = item_states(step_dir, plan, clock())
    if all(states[item['id']]['state'] == 'posted' for item in plan['items']):
        add_published_list(campaign, plan, states)
    return sum(state['state'] in ('failed', 'unknown') for state in states.values())


def publish_due(campaigns_dir, config_path, http=http_request, clock=time.time, sleep=time.sleep):
    campaigns_dir.mkdir(parents=True, exist_ok=True)
    with (campaigns_dir / '.publish.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('another publish run is active; skipping')
            return 0
        try:
            config = load_config(config_path)
        except PublishError as error:
            print(f'error: {error}', file=sys.stderr)
            return 1
        bad = False
        for campaign in sorted(campaigns_dir.iterdir()):
            if not (campaign / '09-publish' / 'APPROVED').is_file():
                continue
            try:
                failed = publish_campaign(campaign, config, http, clock=clock, sleep=sleep)
            except PublishError as error:
                print(f'error: {error}', file=sys.stderr)
                bad = True
                continue
            bad = bad or failed > 0
        return 1 if bad else 0


def reset_item(campaign, item_id, clock=time.time):
    step_dir = campaign / '09-publish'
    if not (step_dir / 'plan.json').is_file():
        raise PublishError(f'{campaign.name}: nothing is approved for posting')
    plan = json.loads((step_dir / 'plan.json').read_text())
    if item_id not in [item['id'] for item in plan['items']]:
        raise PublishError(f'{campaign.name}: no item {item_id} in the plan')
    state = item_states(step_dir, plan, clock())[item_id]['state']
    if state not in ('failed', 'unknown'):
        raise PublishError(f'{campaign.name} {item_id}: its state is {state}; only failed or unknown items can be reset')
    append_event(step_dir, clock, item_id, 'reset')


def check_article(ctx):
    url = ctx.item['url']
    try:
        response = ctx.http('GET', url)
    except NetworkError as error:
        raise RetryLater(f'the article is not reachable yet: {error}') from None
    if response.status != 200:
        raise RetryLater(f'the article answered HTTP {response.status}; it may not be live yet')
    return '', url


ADAPTERS = {'website': check_article}
