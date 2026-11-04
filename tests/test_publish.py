import contextlib
import fcntl
import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'marketing'))
import publish  # noqa: E402
import run  # noqa: E402
from test_run import task_text, FakeClient, answers, ROLES, TEXT_STEPS  # noqa: E402

ARTICLE_URL = '## Article URL\nhttps://moderaty.com/blog/demo\n\n'
LINKEDIN = '## Publish to\nlinkedin\n\n' + ARTICLE_URL


def publish_task(deliverables='article, social, images, reel', extra=''):
    return task_text(deliverables, design_system='moderaty') + extra


class PublishTaskTest(unittest.TestCase):
    def test_task_without_publish_to_is_unchanged(self):
        self.assertEqual(run.check_task(task_text()), [])
        self.assertEqual(run.publish_targets(task_text()), [])

    def test_publish_to_needs_article_url(self):
        errors = run.check_task(publish_task(extra='## Publish to\nlinkedin\n\n'))
        self.assertIn('## Publish to needs ## Article URL: the live https:// address of the article', errors)

    def test_article_url_must_be_https(self):
        errors = run.check_task(publish_task(extra='## Publish to\nlinkedin\n\n## Article URL\nhttp://moderaty.com/blog/demo\n\n'))
        self.assertIn('## Article URL must be one https:// address', errors)

    def test_unknown_platform_is_an_error(self):
        errors = run.check_task(publish_task(extra='## Publish to\nmastodon\n\n' + ARTICLE_URL))
        self.assertIn('unknown platform "mastodon"; use any of: linkedin, x, instagram, youtube', errors)

    def test_targets_come_back_in_platform_order(self):
        self.assertEqual(run.publish_targets(task_text() + '## Publish to\nyoutube, x\n'), ['x', 'youtube'])

    def test_instagram_needs_images(self):
        errors = run.check_task(publish_task(deliverables='article, social, reel', extra='## Publish to\ninstagram\n\n' + ARTICLE_URL))
        self.assertIn('instagram needs images in Deliverables', errors)

    def test_youtube_needs_reel(self):
        errors = run.check_task(publish_task(deliverables='article, social, images', extra='## Publish to\nyoutube\n\n' + ARTICLE_URL))
        self.assertIn('youtube needs reel in Deliverables', errors)

    def test_schedule_line_format(self):
        errors = run.check_task(publish_task(extra=LINKEDIN + '## Schedule\n- linkedin: 2026-11-10 14:00\n\n'))
        self.assertIn('schedule line must look like "- x: 2026-11-10T14:00Z": - linkedin: 2026-11-10 14:00', errors)

    def test_schedule_platform_must_be_a_target(self):
        errors = run.check_task(publish_task(extra=LINKEDIN + '## Schedule\n- x: 2026-11-10T14:00Z\n\n'))
        self.assertIn('schedule names x, which is not in ## Publish to', errors)

    def test_schedule_times_adds_seconds(self):
        task = publish_task(extra='## Publish to\nlinkedin, x\n\n' + ARTICLE_URL + '## Schedule\n- x: 2026-11-10T14:00Z\n- linkedin: 2026-11-10T09:30Z\n\n')
        self.assertEqual(run.check_task(task), [])
        self.assertEqual(run.schedule_times(task), {'x': '2026-11-10T14:00:00Z', 'linkedin': '2026-11-10T09:30:00Z'})


PUBLISH_ALL = '## Publish to\nlinkedin, x, instagram, youtube\n\n' + ARTICLE_URL


def fake_tool(args, cwd):
    Path(args[-1]).write_bytes(b'jpeg')  # stands in for ffmpeg: the last argument is the output file


class PlanMixin:
    """A campaign whose steps are all approved, with small outputs and media."""

    def setUp(self):
        self.campaign = Path(tempfile.mkdtemp()) / 'demo'
        self.campaign.mkdir(parents=True)
        self.write_task(PUBLISH_ALL)
        self.write('03-article/output.md', 'Meta description: ' + 'm' * 155 + '\n# Demo title\nBody.')
        self.write('04-linkedin/output.md', '## Post\nTry it (free) #Moderaty\nLink in the first comment.\n\n## First comment\nRead it: [ARTICLE LINK]\n')
        self.write('05-x/output.md', '1/ First post.\n\n2/ Read it: [ARTICLE LINK]\n')
        self.write('06-instagram/output.md', '## Caption\nA caption. Link in bio\n\n## Hashtags\n#a #b #c\n\n## Image text\nShort text\n')
        for name in ('linkedin.png', 'x.png', 'instagram.png'):
            self.write(f'07-images/{name}', b'png')
        self.write('08-reel/reel.mp4', b'mp4')
        for step in ('03-article', '04-linkedin', '05-x', '06-instagram', '07-images', '08-reel'):
            self.write(f'{step}/APPROVED', '2026-11-04T10:00:00Z\n')
        self.tool = mock.Mock(side_effect=fake_tool)
        patcher = mock.patch('run.run_tool', self.tool)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, rel, data):
        path = self.campaign / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)

    def write_task(self, extra):
        (self.campaign / '00-task.md').write_text(publish_task(extra=extra))

    def plan(self):
        return run.build_publish_plan(self.campaign)

    def item(self, plan, item_id):
        return next(item for item in plan['items'] if item['id'] == item_id)


class PlanTest(PlanMixin, unittest.TestCase):
    def test_items_follow_platform_order(self):
        ids = [item['id'] for item in self.plan()['items']]
        self.assertEqual(ids, ['article', 'linkedin-post', 'linkedin-comment', 'x-1', 'x-2', 'instagram-post', 'youtube-short'])

    def test_article_link_is_replaced_everywhere(self):
        plan = self.plan()
        self.assertNotIn(run.LINK, json.dumps(plan, ensure_ascii=False))
        self.assertEqual(self.item(plan, 'linkedin-comment')['text'], 'Read it: https://moderaty.com/blog/demo')
        self.assertIn('Read it: https://moderaty.com/blog/demo', self.item(plan, 'x-2')['text'])

    def test_x_posts_reply_in_order(self):
        plan = self.plan()
        self.assertNotIn('reply_to', self.item(plan, 'x-1'))
        self.assertEqual(self.item(plan, 'x-2')['reply_to'], 'x-1')
        self.assertEqual(self.item(plan, 'x-2')['depends_on'], ['x-1'])
        self.assertTrue(self.item(plan, 'x-2')['text'].startswith('2/ '))

    def test_linkedin_comment_depends_on_the_post(self):
        comment = self.item(self.plan(), 'linkedin-comment')
        self.assertEqual(comment['depends_on'], ['linkedin-post'])
        self.assertEqual(comment['text'], 'Read it: https://moderaty.com/blog/demo')

    def test_instagram_caption_joins_caption_and_hashtags(self):
        self.assertEqual(self.item(self.plan(), 'instagram-post')['text'], 'A caption. Link in bio\n\n#a #b #c')

    def test_instagram_image_is_converted_with_ffmpeg(self):
        self.plan()
        ffmpeg = [call.args[0] for call in self.tool.call_args_list if call.args[0][0] == 'ffmpeg']
        self.assertEqual(len(ffmpeg), 1)
        self.assertEqual(ffmpeg[0][ffmpeg[0].index('-q:v') + 1], '2')
        self.assertTrue(ffmpeg[0][-1].endswith('instagram.jpg'))

    def test_media_hashes_match_the_files(self):
        plan = self.plan()
        paths = sorted(media['path'] for item in plan['items'] for media in item.get('media', []))
        self.assertEqual(paths, ['media/instagram.jpg', 'media/linkedin.png', 'media/reel.mp4', 'media/x.png'])
        for item in plan['items']:
            for media in item.get('media', []):
                data = (self.campaign / '09-publish' / media['path']).read_bytes()
                self.assertEqual(media['sha256'], hashlib.sha256(data).hexdigest())

    def test_plan_bytes_are_the_same_when_rebuilt(self):
        self.plan()
        first = (self.campaign / '09-publish' / 'plan.json').read_bytes()
        self.plan()
        self.assertEqual((self.campaign / '09-publish' / 'plan.json').read_bytes(), first)

    def test_schedule_sets_not_before(self):
        self.write_task(PUBLISH_ALL + '## Schedule\n- x: 2026-11-10T14:00Z\n\n')
        plan = self.plan()
        self.assertEqual(self.item(plan, 'x-1')['not_before'], '2026-11-10T14:00:00Z')
        self.assertEqual(self.item(plan, 'x-2')['not_before'], '2026-11-10T14:00:00Z')
        self.assertIsNone(self.item(plan, 'linkedin-post')['not_before'])

    def test_long_youtube_title_is_an_error(self):
        self.write('03-article/output.md', 'Meta description: ' + 'm' * 155 + '\n# ' + 'T' * 101 + '\nBody.')
        with self.assertRaisesRegex(run.WorkflowError, 'the article title is 101 characters; a YouTube title allows 100'):
            self.plan()

    def test_unapproved_step_is_an_error(self):
        (self.campaign / '04-linkedin' / 'APPROVED').unlink()
        with self.assertRaisesRegex(run.WorkflowError, '04-linkedin is not approved yet'):
            self.plan()

    def test_platforms_not_listed_are_left_out(self):
        self.write_task('## Publish to\nx\n\n' + ARTICLE_URL)
        self.assertEqual([item['id'] for item in self.plan()['items']], ['article', 'x-1', 'x-2'])

    def test_preview_shows_x_cost(self):
        preview = run.plan_preview(self.plan(), self.campaign / '09-publish')
        self.assertIn('X cost: about $0.215', preview)


def make_skills(root):
    skills = root / 'skills'
    for skill in ('brief', 'seo-article', 'x'):
        (skills / skill).mkdir(parents=True)
        (skills / skill / 'SKILL.md').write_text(f'write {skill}')
        (skills / skill / 'REVIEW.md').write_text(f'review {skill}')
    return skills


class GateTest(PlanMixin, unittest.TestCase):
    def test_approve_writes_the_plan_hash(self):
        run.publish_gate(self.campaign, answers('a'))
        approval = json.loads((self.campaign / '09-publish' / 'APPROVED').read_text())
        self.assertEqual(approval['plan_sha256'], hashlib.sha256((self.campaign / '09-publish' / 'plan.json').read_bytes()).hexdigest())
        self.assertTrue(approval['approved_at'].endswith('Z'))

    def test_quit_writes_no_approval(self):
        with self.assertRaises(SystemExit):
            run.publish_gate(self.campaign, answers('q'))
        self.assertFalse((self.campaign / '09-publish' / 'APPROVED').exists())

    def test_approved_gate_does_not_rebuild(self):
        (self.campaign / '09-publish').mkdir()
        (self.campaign / '09-publish' / 'APPROVED').write_text('{}\n')
        with mock.patch('run.build_publish_plan') as build:
            run.publish_gate(self.campaign, answers())
        build.assert_not_called()

    def test_publish_log_without_approval_is_refused(self):
        (self.campaign / '09-publish').mkdir()
        (self.campaign / '09-publish' / 'publish.jsonl').write_text('')
        with self.assertRaisesRegex(run.WorkflowError, 'has a publish log but no APPROVED file'):
            run.publish_gate(self.campaign, answers('a'))

    def test_campaign_without_publish_to_has_no_gate(self):
        root = Path(tempfile.mkdtemp())
        skills = make_skills(root)
        campaign = root / 'campaigns' / 'demo'
        campaign.mkdir(parents=True)
        (campaign / '00-task.md').write_text(task_text())
        run.run_campaign(campaign, FakeClient(['pass', 'pass']), ask=answers('a', 'a'), steps=TEXT_STEPS, skills_dir=skills, roles=ROLES)
        self.assertFalse((campaign / '09-publish').exists())

    def test_publish_to_calls_the_gate_after_the_final_package(self):
        skills = make_skills(Path(tempfile.mkdtemp()))
        (self.campaign / '00-task.md').write_text(publish_task(extra=PUBLISH_ALL))
        for step in ('01-brief', '02-article', '03-x'):
            self.write(f'{step}/output.md', 'Text.\n')
            self.write(f'{step}/APPROVED', 'ok\n')
        with mock.patch('run.publish_gate') as gate:
            run.run_campaign(self.campaign, FakeClient([]), ask=answers(), steps=TEXT_STEPS, skills_dir=skills, roles=ROLES)
        gate.assert_called_once()
        self.assertTrue((self.campaign / 'final' / 'index.md').is_file())


APPROVED_AT = '2026-11-04T10:00:00Z'


class FakeHttp:
    """Answers in order: a (status, headers, body) tuple becomes a Response, an exception is raised. Running out fails the test."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, method, url, headers=None, body=None, timeout=60):
        self.requests.append((method, url, headers, body))
        if not self.answers:
            raise AssertionError(f'unexpected request: {method} {url}')
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return publish.Response(*answer)


def item(item_id, depends=(), not_before=None, platform='test', **extra):
    return {'id': item_id, 'platform': platform, 'action': 'post', 'text': item_id, 'media': [],
            'depends_on': list(depends), 'not_before': not_before, **extra}


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.campaign = self.root / 'demo'
        self.step = self.campaign / '09-publish'
        self.step.mkdir(parents=True)
        (self.campaign / 'final').mkdir()
        (self.campaign / 'final' / 'index.md').write_text('# Final package: demo\n\nBuilt.')  # no trailing newline, as build_final writes it
        self.epoch = [publish.parse_time(APPROVED_AT)]
        self.script = {}  # item id -> answers for the test adapter, in order
        self.calls = []   # item ids the test adapter was asked to publish
        self.http = FakeHttp([])
        self.adapters = {'test': self.fake_adapter, 'website': publish.check_article}

    def clock(self):
        return self.epoch[0]

    def sleep(self, seconds):
        self.epoch[0] += seconds

    def fake_adapter(self, ctx):
        item_id = ctx.item['id']
        self.calls.append(item_id)
        answers = self.script.get(item_id, [])
        if answers:
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            publish.check(answer, {200}, 'test call', False, ctx.clock())  # a Response: judged by the failure table
        return f'p-{item_id}', f'https://x.example/{item_id}'

    def approve(self, items):
        plan = {'campaign': 'demo', 'article_url': 'https://moderaty.com/blog/demo', 'items': items}
        (self.step / 'plan.json').write_text(json.dumps(plan, indent=2, ensure_ascii=False) + '\n')
        approval = {'approved_at': APPROVED_AT, 'plan_sha256': publish.file_hash(self.step / 'plan.json')}
        (self.step / 'APPROVED').write_text(json.dumps(approval) + '\n')

    def run_once(self):
        return publish.publish_campaign(self.campaign, {}, self.http, clock=self.clock, sleep=self.sleep, adapters=self.adapters)

    def state(self, item_id):
        plan = json.loads((self.step / 'plan.json').read_text())
        return publish.item_states(self.step, plan, self.clock())[item_id]

    def test_items_wait_for_their_dependencies(self):
        self.approve([item('a'), item('b', depends=['a'])])
        self.script['a'] = [publish.RetryLater('busy')]
        self.run_once()
        self.assertEqual(self.calls, ['a'])
        self.sleep(300)
        self.run_once()
        self.assertEqual(self.calls, ['a', 'a', 'b'])
        self.assertEqual(self.state('b')['state'], 'posted')

    def test_not_before_holds_an_item(self):
        self.approve([item('a', not_before='2026-11-04T11:00:00Z')])
        self.run_once()
        self.assertEqual(self.calls, [])
        self.epoch[0] = publish.parse_time('2026-11-04T11:00:00Z')
        self.run_once()
        self.assertEqual(self.calls, ['a'])

    def test_null_not_before_uses_the_approval_time(self):
        self.epoch[0] -= 60
        self.approve([item('a')])
        self.run_once()
        self.assertEqual(self.calls, [])
        self.sleep(60)
        self.run_once()
        self.assertEqual(self.calls, ['a'])

    def test_retry_waits_5_then_10_minutes(self):
        self.approve([item('a')])
        self.script['a'] = [publish.RetryLater('busy'), publish.RetryLater('busy')]
        self.run_once()
        self.assertEqual(publish.parse_time(self.state('a')['next_attempt_at']), self.clock() + 300)
        self.run_once()  # not due yet, so nothing is sent
        self.assertEqual(self.calls, ['a'])
        self.sleep(300)
        self.run_once()
        self.assertEqual(self.calls, ['a', 'a'])
        self.assertEqual(publish.parse_time(self.state('a')['next_attempt_at']), self.clock() + 600)

    def test_fifth_retry_marks_failed(self):
        self.approve([item('a')])
        self.script['a'] = [publish.RetryLater('busy') for _ in range(5)]
        for _ in range(4):
            self.run_once()
            self.epoch[0] = publish.parse_time(self.state('a')['next_attempt_at'])
        self.run_once()
        self.assertEqual(self.state('a')['state'], 'failed')
        self.assertEqual(self.state('a')['attempts'], 5)
        self.assertEqual(len(self.calls), 5)

    def test_retry_after_header_sets_a_longer_wait(self):
        self.approve([item('a')])
        self.script['a'] = [publish.Response(429, {'retry-after': '3600'}, b'slow down')]
        self.run_once()
        self.assertEqual(publish.parse_time(self.state('a')['next_attempt_at']), self.clock() + 3600)

    def test_rate_limit_reset_header_sets_the_wait(self):
        self.approve([item('a')])
        self.script['a'] = [publish.Response(429, {'x-rate-limit-reset': str(self.clock() + 900)}, b'')]
        self.run_once()
        self.assertEqual(publish.parse_time(self.state('a')['next_attempt_at']), self.clock() + 900)

    def test_stop_item_fails_at_once_and_blocks_dependants(self):
        self.approve([item('a'), item('b', depends=['a'])])
        self.script['a'] = [publish.StopItem('bad token')]
        self.run_once()
        self.assertEqual(self.state('a')['state'], 'failed')
        self.assertEqual(self.state('b')['state'], 'blocked')
        self.assertEqual(self.calls, ['a'])

    def test_outcome_unknown_is_never_resent(self):
        self.approve([item('a')])
        self.script['a'] = [publish.OutcomeUnknown('no answer')]
        self.run_once()
        self.sleep(2 * 86400)
        self.run_once()
        self.assertEqual(self.calls, ['a'])
        self.assertEqual(self.state('a')['state'], 'unknown')

    def test_stop_after_sent_counts_as_unknown(self):
        self.approve([item('a')])
        publish.append_event(self.step, self.clock, 'a', 'attempt', attempt=1)
        publish.append_event(self.step, self.clock, 'a', 'sent', attempt=1)
        self.run_once()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state('a')['state'], 'unknown')

    def test_stop_while_preparing_is_retried(self):
        self.approve([item('a')])
        publish.append_event(self.step, self.clock, 'a', 'attempt', attempt=1)
        self.run_once()
        self.assertEqual(self.calls, ['a'])
        self.assertEqual(self.state('a')['state'], 'posted')

    def test_posted_item_is_never_sent_again(self):
        self.approve([item('a')])
        self.run_once()
        self.sleep(86400)
        self.run_once()
        self.assertEqual(self.calls, ['a'])

    def test_changed_plan_is_refused(self):
        self.approve([item('a')])
        plan = json.loads((self.step / 'plan.json').read_text())
        plan['items'][0]['text'] = 'changed after approval'
        (self.step / 'plan.json').write_text(json.dumps(plan))
        with self.assertRaises(publish.PublishError):
            self.run_once()
        self.assertEqual(self.calls, [])

    def test_changed_media_is_refused(self):
        (self.step / 'media').mkdir()
        (self.step / 'media' / 'reel.mp4').write_bytes(b'version one')
        self.approve([item('a', media=[{'path': 'media/reel.mp4', 'sha256': publish.file_hash(self.step / 'media' / 'reel.mp4')}])])
        (self.step / 'media' / 'reel.mp4').write_bytes(b'version two')
        with self.assertRaises(publish.PublishError):
            self.run_once()
        self.assertEqual(self.calls, [])

    def test_second_run_is_skipped_while_locked(self):
        lock = (self.root / '.publish.lock').open('a')
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = publish.publish_due(self.root, self.root / 'publish.json', http=self.http, clock=self.clock, sleep=self.sleep)
        self.assertEqual(code, 0)
        self.assertIn('another publish run is active; skipping', out.getvalue())

    def test_config_readable_by_others_is_refused(self):
        config = self.root / 'publish.json'
        config.write_text('{}')
        config.chmod(0o644)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = publish.publish_due(self.root, config, http=self.http, clock=self.clock, sleep=self.sleep)
        self.assertEqual(code, 1)
        self.assertIn(f'chmod 600 {config}', err.getvalue())
        config.chmod(0o600)
        self.assertEqual(publish.load_config(config), {})

    def test_article_404_waits(self):
        self.approve([item('article', platform='website', action='check', url='https://moderaty.com/blog/demo')])
        self.http.answers = [(404, {}, b'')]
        self.run_once()
        self.assertEqual(self.state('article')['state'], 'waiting')
        self.http.answers = [(200, {}, b'<html>')]
        self.sleep(300)
        self.run_once()
        self.assertEqual(self.state('article')['state'], 'posted')

    def test_publish_network_error_is_unknown_and_prepare_network_error_retries(self):
        with self.assertRaises(publish.OutcomeUnknown):
            publish.send(FakeHttp([publish.NetworkError('timeout')]), 'post', True, 0, 'POST', 'https://x')
        with self.assertRaises(publish.RetryLater):
            publish.send(FakeHttp([publish.NetworkError('timeout')]), 'token', False, 0, 'POST', 'https://x')

    def test_5xx_on_publish_call_is_unknown_except_503(self):
        ok = {201}
        with self.assertRaises(publish.OutcomeUnknown):
            publish.check(publish.Response(500, {}, b''), ok, 'post', True, 0)
        with self.assertRaises(publish.OutcomeUnknown):
            publish.check(publish.Response(502, {}, b''), ok, 'post', True, 0)
        with self.assertRaises(publish.RetryLater):
            publish.check(publish.Response(503, {}, b''), ok, 'post', True, 0)
        with self.assertRaises(publish.RetryLater):
            publish.check(publish.Response(500, {}, b''), ok, 'upload', False, 0)
        with self.assertRaises(publish.StopItem):
            publish.check(publish.Response(401, {}, b'no'), ok, 'post', True, 0)

    def test_published_list_is_added_once(self):
        self.approve([item('article'), item('a')])
        self.run_once()
        self.run_once()
        text = (self.campaign / 'final' / 'index.md').read_text()
        self.assertEqual(text.count('## Published'), 1)
        self.assertIn('- a: https://x.example/a', text)
        self.assertNotIn('- article:', text)

    def test_reset_makes_a_failed_item_pending(self):
        self.approve([item('a')])
        self.script['a'] = [publish.StopItem('bad token')]
        self.run_once()
        self.assertEqual(self.state('a')['state'], 'failed')
        publish.reset_item(self.campaign, 'a', clock=self.clock)
        self.assertEqual(self.state('a')['state'], 'pending')
        self.run_once()
        self.assertEqual(self.state('a')['state'], 'posted')

    def test_no_redirect_handler_returns_none(self):
        self.assertIsNone(publish.NoRedirect().redirect_request(None, None, 308, 'x', {}, 'https://example.com'))

    def test_http_error_becomes_a_response_and_url_error_a_network_error(self):
        error = urllib.error.HTTPError('https://x.example', 404, 'Not Found', {}, io.BytesIO(b'missing'))
        with mock.patch.object(publish.OPENER, 'open', side_effect=error):
            self.assertEqual(publish.http_request('GET', 'https://x.example'), publish.Response(404, {}, b'missing'))
        with mock.patch.object(publish.OPENER, 'open', side_effect=urllib.error.URLError('down')):
            with self.assertRaises(publish.NetworkError):
                publish.http_request('GET', 'https://x.example')

    def test_a_line_cut_off_by_a_crash_is_ignored(self):
        self.approve([item('a')])
        publish.append_event(self.step, self.clock, 'a', 'attempt', attempt=1)
        with (self.step / 'publish.jsonl').open('a') as handle:
            handle.write('{"time": "2026-11-04T10:00:00Z", "item": "a", "ev')  # the process died while writing 'sent'
        self.run_once()
        self.assertEqual(self.calls, ['a'])
        self.assertEqual(self.state('a')['state'], 'posted')
        self.assertEqual([event['event'] for event in publish.read_events(self.step)], ['attempt', 'attempt', 'posted'])


if __name__ == '__main__':
    unittest.main()
