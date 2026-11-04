"""Posting after the owner approves 09-publish: the queue, the retries and the event log.

This module does not import run.py (run.py imports this one). Every HTTP call goes through an `http`
callable, so tests never reach the network.
"""
import base64
import calendar
import collections
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

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


LITTLE_TEXT_RESERVED = set('\\|{}@[]()<>#*_~')


def little_escape(part):
    return ''.join('\\' + char if char in LITTLE_TEXT_RESERVED else char for char in part)


def linkedin_text(text):
    """Escape for LinkedIn's little-text format. A hashtag becomes {hashtag|\\#|Tag}."""
    pieces, position = [], 0
    for match in re.finditer(r'(?<!\w)#(\w+)', text):
        pieces.append(little_escape(text[position:match.start()]))
        pieces.append('{hashtag|\\#|' + little_escape(match.group(1)) + '}')
        position = match.end()
    pieces.append(little_escape(text[position:]))
    return ''.join(pieces)


def platform_settings(ctx, platform, keys):
    settings = ctx.config.get(platform)
    if not isinstance(settings, dict):
        raise StopItem(f"publish.json has no '{platform}' section")
    for key in keys:
        if key not in settings:
            raise StopItem(f"publish.json '{platform}' section has no '{key}'")
    return settings


def answer_json(response, what):
    try:
        return json.loads(response.body)
    except ValueError:
        raise StopItem(f'{what}: the answer is not JSON') from None


def dig(data, what, *keys):
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            raise StopItem(f'{what}: the answer has no "{key}"')
        data = data[key]
    return data


def linkedin_item(ctx):
    settings = platform_settings(ctx, 'linkedin', ['organization_id', 'access_token', 'version'])
    version = str(settings['version'])
    if not re.fullmatch(r'\d{6}', version):
        raise StopItem(f'publish.json linkedin "version" must be six digits such as 202611, not {version!r}')
    headers = {'Authorization': f"Bearer {settings['access_token']}", 'Linkedin-Version': version, 'X-Restli-Protocol-Version': '2.0.0'}
    org = f"urn:li:organization:{settings['organization_id']}"
    if ctx.item['action'] == 'comment':
        return linkedin_comment(ctx, org, headers)
    return linkedin_post(ctx, org, headers)


def linkedin_post(ctx, org, headers):
    image = upload_linkedin_image(ctx, org, headers, ctx.step_dir / ctx.item['media'][0]['path']) if ctx.item.get('media') else None
    body = {'author': org, 'commentary': linkedin_text(ctx.item['text']), 'visibility': 'PUBLIC',
            'distribution': {'feedDistribution': 'MAIN_FEED', 'targetEntities': [], 'thirdPartyDistributionChannels': []},
            'lifecycleState': 'PUBLISHED', 'isReshareDisabledByAuthor': False}
    if image is not None:
        body['content'] = {'media': {'id': image}}
    ctx.sent()
    answer = check(send(ctx.http, 'LinkedIn post', True, ctx.clock(), 'POST', 'https://api.linkedin.com/rest/posts',
                        headers={**headers, 'Content-Type': 'application/json'}, body=json.dumps(body).encode()),
                   {201}, 'LinkedIn post', True, ctx.clock())
    post_id = answer.headers.get('x-restli-id')
    if not post_id:
        raise OutcomeUnknown('LinkedIn answered 201 without x-restli-id; check the page')
    return post_id, f'https://www.linkedin.com/feed/update/{post_id}/'


def upload_linkedin_image(ctx, org, headers, path):
    what = 'LinkedIn image init'
    init = check(send(ctx.http, what, False, ctx.clock(), 'POST', 'https://api.linkedin.com/rest/images?action=initializeUpload',
                      headers={**headers, 'Content-Type': 'application/json'},
                      body=json.dumps({'initializeUploadRequest': {'owner': org}}).encode()),
                 {200}, what, False, ctx.clock())
    answer = answer_json(init, what)
    upload_url, image = dig(answer, what, 'value', 'uploadUrl'), dig(answer, what, 'value', 'image')
    check(send(ctx.http, 'LinkedIn image upload', False, ctx.clock(), 'PUT', upload_url,
               headers={'Authorization': headers['Authorization'], 'Content-Type': 'application/octet-stream'}, body=path.read_bytes()),
          {200, 201}, 'LinkedIn image upload', False, ctx.clock())
    return image


def linkedin_comment(ctx, org, headers):
    parent = ctx.states['linkedin-post']['post_id']
    ctx.sent()
    answer = check(send(ctx.http, 'LinkedIn comment', True, ctx.clock(), 'POST',
                        f'https://api.linkedin.com/rest/socialActions/{urllib.parse.quote(parent, safe="")}/comments',
                        headers={**headers, 'Content-Type': 'application/json'},
                        body=json.dumps({'actor': org, 'object': parent, 'message': {'text': ctx.item['text']}}).encode()),
                   {201}, 'LinkedIn comment', True, ctx.clock())
    comment_id = answer.headers.get('x-restli-id')
    if not comment_id:
        raise OutcomeUnknown('LinkedIn answered 201 without x-restli-id; check the comment')
    return comment_id, ctx.states['linkedin-post']['url']


def oauth_enc(text):
    return urllib.parse.quote(text, safe='-._~')


def oauth1_base_string(method, url, params):
    """`params` holds every request parameter as (key, value) pairs: the oauth_ ones and the URL's query. The body is not included."""
    parameter_string = '&'.join(f'{key}={value}' for key, value in sorted((oauth_enc(k), oauth_enc(v)) for k, v in params))
    return '&'.join([method.upper(), oauth_enc(url.split('?', 1)[0]), oauth_enc(parameter_string)])


def oauth1_header(method, url, api_key, api_secret, token, token_secret, nonce, timestamp):
    oauth = {'oauth_consumer_key': api_key, 'oauth_nonce': nonce, 'oauth_signature_method': 'HMAC-SHA1',
             'oauth_timestamp': timestamp, 'oauth_token': token, 'oauth_version': '1.0'}
    query = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)
    base = oauth1_base_string(method, url, list(oauth.items()) + query)
    key = f'{oauth_enc(api_secret)}&{oauth_enc(token_secret)}'
    oauth['oauth_signature'] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return 'OAuth ' + ', '.join(f'{oauth_enc(k)}="{oauth_enc(v)}"' for k, v in sorted(oauth.items()))


def multipart(fields, files):
    """`fields` are (name, value) pairs; `files` are (name, filename, content_type, data). Returns (Content-Type, body)."""
    boundary = secrets.token_hex(16)
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode() for name, value in fields]
    for name, filename, content_type, data in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f'Content-Type: {content_type}\r\n\r\n'.encode() + data + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return f'multipart/form-data; boundary={boundary}', b''.join(parts)


def published_field(response, what, *keys):
    """An id from a 2xx answer to a publish call. The post is live already, so a missing id is OutcomeUnknown, never StopItem."""
    try:
        value = json.loads(response.body)
        for key in keys:
            value = value[key]
    except (ValueError, KeyError, TypeError):
        raise OutcomeUnknown(f'{what} answered without {".".join(keys)}; check the account') from None
    return value


def x_item(ctx):
    settings = platform_settings(ctx, 'x', ['username', 'api_key', 'api_secret', 'access_token', 'access_token_secret'])

    def auth(method, url):
        return oauth1_header(method, url, settings['api_key'], settings['api_secret'], settings['access_token'],
                             settings['access_token_secret'], secrets.token_hex(16), str(int(ctx.clock())))

    body = {'text': ctx.item['text']}
    if ctx.item.get('media'):
        upload_url = 'https://api.x.com/2/media/upload'
        content_type, data = multipart([('media_category', 'tweet_image')],
                                       [('media', 'x.png', 'image/png', (ctx.step_dir / ctx.item['media'][0]['path']).read_bytes())])
        upload = check(send(ctx.http, 'X media upload', False, ctx.clock(), 'POST', upload_url,
                            headers={'Authorization': auth('POST', upload_url), 'Content-Type': content_type}, body=data),
                       {200, 201}, 'X media upload', False, ctx.clock())
        body['media'] = {'media_ids': [dig(answer_json(upload, 'X media upload'), 'X media upload', 'data', 'id')]}
    if 'reply_to' in ctx.item:
        body['reply'] = {'in_reply_to_tweet_id': ctx.states[ctx.item['reply_to']]['post_id']}
    ctx.sent()
    post_url = 'https://api.x.com/2/tweets'
    answer = check(send(ctx.http, 'X post', True, ctx.clock(), 'POST', post_url,
                        headers={'Authorization': auth('POST', post_url), 'Content-Type': 'application/json'}, body=json.dumps(body).encode()),
                   {201}, 'X post', True, ctx.clock())
    tweet_id = published_field(answer, 'X post', 'data', 'id')
    return tweet_id, f"https://x.com/{settings['username']}/status/{tweet_id}"


FORM_HEADERS = {'Content-Type': 'application/x-www-form-urlencoded'}
DAY_SECONDS = 24 * 60 * 60


def instagram_item(ctx):
    settings = platform_settings(ctx, 'instagram', ['user_id', 'access_token', 'api_version', 'media_dir', 'media_base_url'])
    version = str(settings['api_version'])
    if not re.fullmatch(r'v\d+\.\d+', version):
        raise StopItem(f'publish.json instagram "api_version" must look like v26.0, not {version!r}')
    base = f'https://graph.instagram.com/{version}'
    image_url = instagram_public_url(ctx, settings)
    container_id = instagram_container(ctx, base, settings, image_url)
    instagram_wait_for_container(ctx, base, settings['access_token'], container_id)
    media_id = instagram_publish(ctx, base, settings, container_id)
    return media_id, instagram_permalink(ctx, base, settings['access_token'], media_id)


def instagram_public_url(ctx, settings):
    name = f"{ctx.item['media'][0]['sha256'][:16]}.jpg"
    dest = Path(settings['media_dir']) / ctx.campaign.name / name
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ctx.step_dir / ctx.item['media'][0]['path'], dest)
    url = settings['media_base_url'].rstrip('/') + f'/{ctx.campaign.name}/{name}'
    response = send(ctx.http, 'Instagram image URL', False, ctx.clock(), 'GET', url)
    if response.status != 200 or not response.headers.get('content-type', '').startswith('image/jpeg'):
        raise RetryLater(f'{url} does not serve the JPEG yet (HTTP {response.status}); the web folder may still be syncing')
    return url


def instagram_container(ctx, base, settings, image_url):
    created = ctx.data.get('container_created_at')
    if ctx.data.get('container_id') and created and ctx.clock() - parse_time(created) < DAY_SECONDS:
        return ctx.data['container_id']
    what = 'Instagram container'
    body = urllib.parse.urlencode({'image_url': image_url, 'caption': ctx.item['text'], 'access_token': settings['access_token']})
    answer = check(send(ctx.http, what, False, ctx.clock(), 'POST', f"{base}/{settings['user_id']}/media",
                        headers=FORM_HEADERS, body=body.encode()), {200}, what, False, ctx.clock())
    container_id = dig(answer_json(answer, what), what, 'id')
    ctx.save({'container_id': container_id, 'container_created_at': now_text(ctx.clock())})
    return container_id


def instagram_wait_for_container(ctx, base, token, container_id):
    what = 'Instagram container status'
    for number in range(1, 6):
        if number > 1:
            ctx.sleep(60)
        query = urllib.parse.urlencode({'fields': 'status_code', 'access_token': token})
        answer = check(send(ctx.http, what, False, ctx.clock(), 'GET', f'{base}/{container_id}?{query}'), {200}, what, False, ctx.clock())
        status = dig(answer_json(answer, what), what, 'status_code')
        if status == 'FINISHED':
            return
        if status == 'EXPIRED':
            ctx.save({'container_id': None, 'container_created_at': None})
            raise RetryLater('the Instagram container expired; it will be created again')
        if status != 'IN_PROGRESS':
            raise StopItem(f'Instagram rejected the media: container status {status}')
    raise RetryLater('the Instagram container is still IN_PROGRESS after 5 checks')


def instagram_daily_cap(response):
    try:
        error = json.loads(response.body).get('error', {})
    except (ValueError, AttributeError):
        return False
    return error.get('code') == 9 and error.get('error_subcode') == 2207042


def instagram_publish(ctx, base, settings, container_id):
    what = 'Instagram publish'
    body = urllib.parse.urlencode({'creation_id': container_id, 'access_token': settings['access_token']})
    ctx.sent()
    response = send(ctx.http, what, True, ctx.clock(), 'POST', f"{base}/{settings['user_id']}/media_publish",
                    headers=FORM_HEADERS, body=body.encode())
    if response.status != 200 and instagram_daily_cap(response):  # read before check: this answer is a 400, not a status to stop on
        raise RetryLater('Instagram allows 100 published posts per 24 hours and that limit is reached; it will be retried')
    return published_field(check(response, {200}, what, True, ctx.clock()), what, 'id')


def instagram_permalink(ctx, base, token, media_id):
    query = urllib.parse.urlencode({'fields': 'permalink', 'access_token': token})
    try:  # the post is live already, so no failure here may change the item's result
        response = ctx.http('GET', f'{base}/{media_id}?{query}')
    except NetworkError:
        return ''
    if response.status != 200:
        return ''
    try:
        return json.loads(response.body)['permalink']
    except (ValueError, KeyError, TypeError):
        return ''


ADAPTERS = {'website': check_article, 'linkedin': linkedin_item, 'x': x_item, 'instagram': instagram_item}
