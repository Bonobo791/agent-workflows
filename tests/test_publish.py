import contextlib
import fcntl
import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from http.client import IncompleteRead
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
        self.assertIn('unknown platform "mastodon"; use any of: linkedin, instagram, youtube', errors)

    def test_targets_come_back_in_platform_order(self):
        self.assertEqual(run.publish_targets(task_text() + '## Publish to\nyoutube, linkedin\n'), ['linkedin', 'youtube'])

    def test_x_in_publish_to_is_refused(self):
        errors = run.check_task(publish_task(extra='## Publish to\nx\n\n' + ARTICLE_URL))
        self.assertIn('x is not posted automatically, because the X API charges per post. Remove it from ## Publish to and post the thread by hand from final/05-x.md.', errors)

    def test_instagram_needs_images(self):
        errors = run.check_task(publish_task(deliverables='article, social, reel', extra='## Publish to\ninstagram\n\n' + ARTICLE_URL))
        self.assertIn('instagram needs images in Deliverables', errors)

    def test_youtube_needs_reel(self):
        errors = run.check_task(publish_task(deliverables='article, social, images', extra='## Publish to\nyoutube\n\n' + ARTICLE_URL))
        self.assertIn('youtube needs reel in Deliverables', errors)

    def test_schedule_line_format(self):
        errors = run.check_task(publish_task(extra=LINKEDIN + '## Schedule\n- linkedin: 2026-11-10 14:00\n\n'))
        self.assertIn('schedule line must look like "- linkedin: 2026-11-10T09:30Z": - linkedin: 2026-11-10 14:00', errors)

    def test_schedule_platform_must_be_a_target(self):
        errors = run.check_task(publish_task(extra=LINKEDIN + '## Schedule\n- instagram: 2026-11-10T14:00Z\n\n'))
        self.assertIn('schedule names instagram, which is not in ## Publish to', errors)

    def test_schedule_time_must_be_a_real_date(self):
        errors = run.check_task(publish_task(extra=LINKEDIN + '## Schedule\n- linkedin: 2026-11-31T14:00Z\n\n'))
        self.assertIn('schedule time is not a real date and time: - linkedin: 2026-11-31T14:00Z', errors)

    def test_schedule_times_adds_seconds(self):
        task = publish_task(extra='## Publish to\nlinkedin, instagram\n\n' + ARTICLE_URL + '## Schedule\n- instagram: 2026-11-10T14:00Z\n- linkedin: 2026-11-10T09:30Z\n\n')
        self.assertEqual(run.check_task(task), [])
        self.assertEqual(run.schedule_times(task), {'instagram': '2026-11-10T14:00:00Z', 'linkedin': '2026-11-10T09:30:00Z'})

PUBLISH_ALL = '## Publish to\nlinkedin, instagram, youtube\n\n' + ARTICLE_URL


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
        self.write('06-instagram/output.md', '## Caption\nA caption. Link in bio\n\n## Hashtags\n#a #b #c\n\n## Image text\nShort text\n')
        for name in ('linkedin.png', 'instagram.png'):
            self.write(f'07-images/{name}', b'png')
        self.write('08-reel/reel.mp4', b'mp4')
        for step in ('03-article', '04-linkedin', '06-instagram', '07-images', '08-reel'):
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
        self.assertEqual(ids, ['article', 'linkedin-post', 'linkedin-comment', 'instagram-post', 'youtube-short'])

    def test_article_link_is_replaced_everywhere(self):
        plan = self.plan()
        self.assertNotIn(run.LINK, json.dumps(plan, ensure_ascii=False))
        self.assertEqual(self.item(plan, 'linkedin-comment')['text'], 'Read it: https://moderaty.com/blog/demo')
        self.assertTrue(self.item(plan, 'youtube-short')['text'].endswith('https://moderaty.com/blog/demo'))


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
        self.assertEqual(paths, ['media/instagram.jpg', 'media/linkedin.png', 'media/reel.mp4'])
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
        self.write_task(PUBLISH_ALL + '## Schedule\n- instagram: 2026-11-10T14:00Z\n\n')
        plan = self.plan()
        self.assertEqual(self.item(plan, 'instagram-post')['not_before'], '2026-11-10T14:00:00Z')
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
        self.write_task('## Publish to\nyoutube\n\n' + ARTICLE_URL)
        self.assertEqual([item['id'] for item in self.plan()['items']], ['article', 'youtube-short'])

    def test_no_plan_item_is_for_x(self):
        self.assertNotIn('x', [item['platform'] for item in self.plan()['items']])

def make_skills(root):
    skills = root / 'skills'
    for skill in ('brief', 'seo-article', 'x'):
        (skills / skill).mkdir(parents=True)
        (skills / skill / 'SKILL.md').write_text(f'write {skill}')
        (skills / skill / 'REVIEW.md').write_text(f'review {skill}')
    return skills


class GateTest(PlanMixin, unittest.TestCase):
    def test_gate_gets_the_article_url_first(self):
        http = FakeHttp([(200, {}, b'<html>')])
        run.publish_gate(self.campaign, answers('a'), http)
        self.assertEqual(http.requests[0][:2], ('GET', 'https://moderaty.com/blog/demo'))

    def test_article_not_live_stops_before_the_plan(self):
        with self.assertRaisesRegex(run.WorkflowError, 'HTTP 404'):
            run.publish_gate(self.campaign, answers(), FakeHttp([(404, {}, b'')]))
        self.assertFalse((self.campaign / '09-publish' / 'plan.json').exists())
        self.assertFalse((self.campaign / '09-publish' / 'APPROVED').exists())

    def test_unreachable_article_stops_before_the_plan(self):
        with self.assertRaisesRegex(run.WorkflowError, 'could not reach the article'):
            run.publish_gate(self.campaign, answers(), FakeHttp([publish.NetworkError('timed out')]))
        self.assertFalse((self.campaign / '09-publish' / 'plan.json').exists())

    def test_no_redirect_handler_returns_none(self):
        self.assertIsNone(publish.NoRedirect().redirect_request(None, None, 308, 'x', {}, 'https://example.com'))

    def test_approve_writes_the_plan_hash(self):
        run.publish_gate(self.campaign, answers('a'), FakeHttp([(200, {}, b'')]))
        approval = json.loads((self.campaign / '09-publish' / 'APPROVED').read_text())
        plan_hash = hashlib.sha256((self.campaign / '09-publish' / 'plan.json').read_bytes()).hexdigest()
        self.assertEqual(approval['plan_sha256'], plan_hash)
        self.assertTrue(approval['approved_at'].endswith('Z'))

    def test_quit_writes_no_approval(self):
        with self.assertRaises(SystemExit):
            run.publish_gate(self.campaign, answers('q'), FakeHttp([(200, {}, b'')]))
        self.assertFalse((self.campaign / '09-publish' / 'APPROVED').exists())

    def test_approved_gate_does_not_rebuild(self):
        (self.campaign / '09-publish').mkdir()
        (self.campaign / '09-publish' / 'APPROVED').write_text('{}\n')
        with mock.patch('run.build_publish_plan') as build:
            run.publish_gate(self.campaign, answers(), FakeHttp([]))
        build.assert_not_called()

    def test_publish_log_without_approval_is_refused(self):
        (self.campaign / '09-publish').mkdir()
        (self.campaign / '09-publish' / 'publish.jsonl').write_text('')
        with self.assertRaisesRegex(run.WorkflowError, 'has a publish log but no APPROVED file'):
            run.publish_gate(self.campaign, answers('a'), FakeHttp([]))

    def test_campaign_without_publish_to_has_no_gate(self):
        root = Path(tempfile.mkdtemp())
        skills = make_skills(root)
        campaign = root / 'campaigns' / 'demo'
        campaign.mkdir(parents=True)
        (campaign / '00-task.md').write_text(task_text())
        run.run_campaign(campaign, FakeClient(['pass', 'pass']), ask=answers('a', 'a'), steps=TEXT_STEPS, skills_dir=skills, roles=ROLES)
        self.assertFalse((campaign / '09-publish').exists())
        self.assertIn('Nothing has been published.', (campaign / 'final' / 'index.md').read_text())

    def test_publish_to_calls_the_gate_after_the_final_package(self):
        skills = make_skills(Path(tempfile.mkdtemp()))
        (self.campaign / '00-task.md').write_text(publish_task(extra=PUBLISH_ALL))
        for step in ('01-brief', '02-article', '03-x'):
            self.write(f'{step}/output.md', 'Text.\n')
            self.write(f'{step}/APPROVED', 'ok\n')
        shown = io.StringIO()
        announced = []

        def gate(campaign, ask, http):
            announced.append('All steps approved' in shown.getvalue())

        with contextlib.redirect_stdout(shown), mock.patch('run.publish_gate', side_effect=gate) as gate_mock:
            run.run_campaign(self.campaign, FakeClient([]), ask=answers(), steps=TEXT_STEPS, skills_dir=skills, roles=ROLES)
        gate_mock.assert_called_once()
        self.assertEqual(announced, [True])
        self.assertIn('Posts go out after 09-publish is approved', (self.campaign / 'final' / 'index.md').read_text())

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


class CutOffAnswer:
    """What urllib gives back when the body stops early: reading it raises IncompleteRead."""
    status = 201
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        raise IncompleteRead(b'{"data"')


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

    def test_missing_plan_is_refused(self):
        self.approve([item('a')])
        (self.step / 'plan.json').unlink()
        with self.assertRaisesRegex(publish.PublishError, 'APPROVED exists but plan.json is missing; nothing was sent'):
            self.run_once()
        self.assertEqual(self.calls, [])

    def test_retry_after_is_seconds_or_a_date_and_anything_else_is_no_header(self):
        epoch = publish.parse_time(APPROVED_AT)
        self.assertEqual(publish.header_wait(publish.Response(429, {'retry-after': '120'}, b''), epoch), 120)
        self.assertEqual(publish.header_wait(publish.Response(429, {'retry-after': 'Wed, 04 Nov 2026 10:10:00 GMT'}, b''), epoch), 600)
        self.assertIsNone(publish.header_wait(publish.Response(429, {'retry-after': 'soon'}, b''), epoch))
        self.assertIsNone(publish.header_wait(publish.Response(429, {'x-rate-limit-reset': 'later'}, b''), epoch))

    def test_config_that_is_not_json_is_refused(self):
        config = self.root / 'publish.json'
        config.write_text('{not json')
        config.chmod(0o600)
        with self.assertRaisesRegex(publish.PublishError, 'is not valid JSON'):
            publish.load_config(config)

    def test_a_body_cut_off_mid_read_is_a_network_error(self):
        with mock.patch.object(publish.OPENER, 'open', return_value=CutOffAnswer()):
            with self.assertRaises(publish.OutcomeUnknown):
                publish.send(publish.http_request, 'LinkedIn post', True, 0, 'POST', 'https://api.linkedin.com/rest/posts', body=b'{}')
            with self.assertRaises(publish.RetryLater):
                publish.send(publish.http_request, 'token', False, 0, 'POST', 'https://oauth2.googleapis.com/token', body=b'x')

    def test_unreadable_approval_stops_only_that_campaign(self):
        bad = self.root / 'a-bad' / '09-publish'
        bad.mkdir(parents=True)
        (bad / 'APPROVED').write_text('{not json')
        self.approve([item('article', platform='website', action='check', url='https://moderaty.com/blog/demo')])
        self.http.answers = [(200, {}, b'')]
        config = self.root / 'publish.json'
        config.write_text('{}')
        config.chmod(0o600)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = publish.publish_due(self.root, config, http=self.http, clock=self.clock, sleep=self.sleep)
        self.assertEqual(code, 1)
        self.assertIn('a-bad: 09-publish/APPROVED is unreadable; nothing was sent', err.getvalue())
        self.assertEqual(self.state('article')['state'], 'posted')

    def test_missing_final_index_stops_only_the_published_list(self):
        self.approve([item('a')])
        (self.campaign / 'final' / 'index.md').unlink()
        with self.assertRaisesRegex(publish.PublishError, 'final/index.md is missing; the posts are live, but the Published list was not written'):
            self.run_once()
        self.assertEqual(self.state('a')['state'], 'posted')

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


LINKEDIN_CONFIG = {'organization_id': '12345678', 'access_token': 'tok', 'version': '202611'}


class AdapterTest(unittest.TestCase):
    """Calls one real adapter through a FakeHttp. The clock stands still at the approval time."""

    def setUp(self):
        self.campaign = Path(tempfile.mkdtemp()) / 'demo'
        self.step = self.campaign / '09-publish'
        (self.step / 'media').mkdir(parents=True)
        self.epoch = publish.parse_time(APPROVED_AT)
        self.sleeps = []

    def ctx(self, it, http, config=None, states=None, attempt=1):
        return publish.ItemContext(self.campaign, self.step, it, states or {}, config or {}, http,
                                   lambda: self.epoch, self.sleeps.append, {}, attempt)


class LinkedInTest(AdapterTest):
    def image_answers(self):
        return [(200, {}, json.dumps({'value': {'uploadUrl': 'https://upload.example/1', 'image': 'urn:li:image:1'}}).encode()),
                (201, {}, b''), (201, {'x-restli-id': 'urn:li:share:9'}, b'')]

    def with_image(self, text):
        (self.step / 'media' / 'linkedin.png').write_bytes(b'png')
        return item('linkedin-post', platform='linkedin', text=text, media=[{'path': 'media/linkedin.png', 'sha256': 'unused'}])

    def test_linkedin_text_escapes_reserved_characters(self):
        self.assertEqual(publish.linkedin_text('Try it (free) at moderaty.com #Moderaty #creators ~ now @you'),
                         r'Try it \(free\) at moderaty.com {hashtag|\#|Moderaty} {hashtag|\#|creators} \~ now \@you')

    def test_post_with_image_initializes_uploads_then_posts(self):
        http = FakeHttp(self.image_answers())
        publish.linkedin_item(self.ctx(self.with_image('Hi'), http, {'linkedin': LINKEDIN_CONFIG}))
        self.assertEqual([(method, url) for method, url, _, _ in http.requests], [
            ('POST', 'https://api.linkedin.com/rest/images?action=initializeUpload'),
            ('PUT', 'https://upload.example/1'),
            ('POST', 'https://api.linkedin.com/rest/posts')])

    def test_post_body(self):
        http = FakeHttp(self.image_answers())
        publish.linkedin_item(self.ctx(self.with_image('Try (free)'), http, {'linkedin': LINKEDIN_CONFIG}))
        _, _, headers, body = http.requests[2]
        self.assertEqual(headers['Authorization'], 'Bearer tok')
        self.assertEqual(headers['Linkedin-Version'], '202611')
        self.assertEqual(headers['X-Restli-Protocol-Version'], '2.0.0')
        data = json.loads(body)
        self.assertEqual(data['author'], 'urn:li:organization:12345678')
        self.assertEqual(data['commentary'], r'Try \(free\)')
        self.assertEqual(data['visibility'], 'PUBLIC')
        self.assertEqual(data['content'], {'media': {'id': 'urn:li:image:1'}})

    def test_post_without_image_has_no_content(self):
        http = FakeHttp([(201, {'x-restli-id': 'urn:li:share:9'}, b'')])
        publish.linkedin_item(self.ctx(item('linkedin-post', platform='linkedin', text='Hi'), http, {'linkedin': LINKEDIN_CONFIG}))
        self.assertNotIn('content', json.loads(http.requests[0][3]))

    def test_post_id_comes_from_x_restli_id(self):
        http = FakeHttp([(201, {'x-restli-id': 'urn:li:share:9'}, b'')])
        result = publish.linkedin_item(self.ctx(item('linkedin-post', platform='linkedin', text='Hi'), http, {'linkedin': LINKEDIN_CONFIG}))
        self.assertEqual(result, ('urn:li:share:9', 'https://www.linkedin.com/feed/update/urn:li:share:9/'))

    def test_comment_url_encodes_the_post_urn(self):
        states = {'linkedin-post': {'post_id': 'urn:li:share:9', 'url': 'https://www.linkedin.com/feed/update/urn:li:share:9/'}}
        http = FakeHttp([(201, {'x-restli-id': 'urn:li:comment:7'}, b'')])
        result = publish.linkedin_item(self.ctx(item('linkedin-comment', platform='linkedin', action='comment', text='Read it'),
                                                http, {'linkedin': LINKEDIN_CONFIG}, states))
        self.assertEqual(http.requests[0][1], 'https://api.linkedin.com/rest/socialActions/urn%3Ali%3Ashare%3A9/comments')
        self.assertEqual(json.loads(http.requests[0][3]), {'actor': 'urn:li:organization:12345678', 'object': 'urn:li:share:9',
                                                           'message': {'text': 'Read it'}})
        self.assertEqual(result, ('urn:li:comment:7', 'https://www.linkedin.com/feed/update/urn:li:share:9/'))

    def test_upload_500_retries(self):
        init = (200, {}, json.dumps({'value': {'uploadUrl': 'https://upload.example/1', 'image': 'urn:li:image:1'}}).encode())
        with self.assertRaises(publish.RetryLater):
            publish.linkedin_item(self.ctx(self.with_image('Hi'), FakeHttp([init, (500, {}, b'')]), {'linkedin': LINKEDIN_CONFIG}))

    def test_malformed_image_init_stops(self):
        for answer in ((200, {}, b'<html>'), (200, {}, json.dumps({'value': {'image': 'urn:li:image:1'}}).encode())):
            with self.subTest(answer=answer), self.assertRaises(publish.StopItem):
                publish.linkedin_item(self.ctx(self.with_image('Hi'), FakeHttp([answer]), {'linkedin': LINKEDIN_CONFIG}))

    def test_post_401_stops(self):
        with self.assertRaises(publish.StopItem):
            publish.linkedin_item(self.ctx(item('linkedin-post', platform='linkedin', text='Hi'), FakeHttp([(401, {}, b'expired')]),
                                           {'linkedin': LINKEDIN_CONFIG}))

    def test_post_timeout_is_unknown(self):
        with self.assertRaises(publish.OutcomeUnknown):
            publish.linkedin_item(self.ctx(item('linkedin-post', platform='linkedin', text='Hi'), FakeHttp([publish.NetworkError('timed out')]),
                                           {'linkedin': LINKEDIN_CONFIG}))

    def test_missing_post_id_is_unknown(self):
        with self.assertRaises(publish.OutcomeUnknown):
            publish.linkedin_item(self.ctx(item('linkedin-post', platform='linkedin', text='Hi'), FakeHttp([(201, {}, b'')]),
                                           {'linkedin': LINKEDIN_CONFIG}))

    def test_missing_version_stops(self):
        without = {key: value for key, value in LINKEDIN_CONFIG.items() if key != 'version'}
        for settings in (without, {**LINKEDIN_CONFIG, 'version': '2611'}):
            with self.subTest(settings=settings), self.assertRaises(publish.StopItem):
                publish.linkedin_item(self.ctx(item('linkedin-post', platform='linkedin', text='Hi'), FakeHttp([]), {'linkedin': settings}))


class InstagramTest(AdapterTest):
    def setUp(self):
        super().setUp()
        self.public = Path(tempfile.mkdtemp()) / 'public'
        self.config = {'instagram': {'user_id': '17841', 'access_token': 'igtok', 'api_version': 'v26.0',
                                     'media_dir': str(self.public), 'media_base_url': 'https://media.moderaty.com/social'}}
        (self.step / 'media' / 'instagram.jpg').write_bytes(b'jpeg')

    def insta_item(self):
        return item('instagram-post', platform='instagram', text='A caption\n\n#a #b #c', media=[{'path': 'media/instagram.jpg', 'sha256': 'a' * 64}])

    def image_ok(self):
        return (200, {'content-type': 'image/jpeg'}, b'jpeg')

    def container(self, container_id='c1'):
        return (200, {}, json.dumps({'id': container_id}).encode())

    def status(self, value):
        return (200, {}, json.dumps({'status_code': value}).encode())

    def published(self, media_id='m1'):
        return (200, {}, json.dumps({'id': media_id}).encode())

    def permalink(self):
        return (200, {}, json.dumps({'permalink': 'https://www.instagram.com/p/abc/'}).encode())

    def test_image_is_copied_and_its_public_url_checked(self):
        http = FakeHttp([self.image_ok(), self.container(), self.status('FINISHED'), self.published(), self.permalink()])
        result = publish.instagram_item(self.ctx(self.insta_item(), http, self.config))
        self.assertEqual(result, ('m1', 'https://www.instagram.com/p/abc/'))
        self.assertEqual((self.public / 'demo' / 'aaaaaaaaaaaaaaaa.jpg').read_bytes(), b'jpeg')
        self.assertEqual(http.requests[0][:2], ('GET', 'https://media.moderaty.com/social/demo/aaaaaaaaaaaaaaaa.jpg'))

    def test_png_content_type_waits(self):
        http = FakeHttp([(200, {'content-type': 'image/png'}, b'png')])
        with self.assertRaises(publish.RetryLater):
            publish.instagram_item(self.ctx(self.insta_item(), http, self.config))

    def test_container_status_then_publish(self):
        http = FakeHttp([self.image_ok(), self.container(), self.status('FINISHED'), self.published(), self.permalink()])
        publish.instagram_item(self.ctx(self.insta_item(), http, self.config))
        sequence = [(method, url.split('?')[0]) for method, url, _, _ in http.requests[1:]]
        self.assertEqual(sequence, [('POST', 'https://graph.instagram.com/v26.0/17841/media'),
                                    ('GET', 'https://graph.instagram.com/v26.0/c1'),
                                    ('POST', 'https://graph.instagram.com/v26.0/17841/media_publish'),
                                    ('GET', 'https://graph.instagram.com/v26.0/m1')])
        self.assertIn(b'creation_id=c1', http.requests[3][3])

    def test_fresh_container_is_reused(self):
        http = FakeHttp([self.image_ok(), self.status('FINISHED'), self.published(), self.permalink()])
        ctx = self.ctx(self.insta_item(), http, self.config)
        ctx.data.update({'container_id': 'c-old', 'container_created_at': publish.now_text(self.epoch - 3600)})
        publish.instagram_item(ctx)
        self.assertIn('/c-old?', http.requests[1][1])
        self.assertFalse(any(method == 'POST' and url.endswith('/media') for method, url, _, _ in http.requests))

    def test_container_older_than_24_hours_is_replaced(self):
        http = FakeHttp([self.image_ok(), self.container('c-new'), self.status('FINISHED'), self.published(), self.permalink()])
        ctx = self.ctx(self.insta_item(), http, self.config)
        ctx.data.update({'container_id': 'c-old', 'container_created_at': publish.now_text(self.epoch - 86401)})
        publish.instagram_item(ctx)
        self.assertIn(b'creation_id=c-new', http.requests[3][3])

    def test_error_status_stops(self):
        http = FakeHttp([self.image_ok(), self.container(), self.status('ERROR')])
        with self.assertRaises(publish.StopItem):
            publish.instagram_item(self.ctx(self.insta_item(), http, self.config))

    def test_in_progress_five_times_retries_later(self):
        http = FakeHttp([self.image_ok(), self.container()] + [self.status('IN_PROGRESS')] * 5)
        with self.assertRaises(publish.RetryLater):
            publish.instagram_item(self.ctx(self.insta_item(), http, self.config))
        self.assertEqual(len(self.sleeps), 4)

    def test_expired_container_is_cleared(self):
        http = FakeHttp([self.image_ok(), self.container(), self.status('EXPIRED')])
        ctx = self.ctx(self.insta_item(), http, self.config)
        with self.assertRaises(publish.RetryLater):
            publish.instagram_item(ctx)
        self.assertIsNone(ctx.data['container_id'])
        self.assertIsNone(ctx.data['container_created_at'])

    def test_publishing_cap_retries(self):
        cap = (400, {}, json.dumps({'error': {'code': 9, 'error_subcode': 2207042, 'message': 'limit reached'}}).encode())
        http = FakeHttp([self.image_ok(), self.container(), self.status('FINISHED'), cap])
        with self.assertRaises(publish.RetryLater):
            publish.instagram_item(self.ctx(self.insta_item(), http, self.config))

    def test_media_publish_timeout_is_unknown(self):
        http = FakeHttp([self.image_ok(), self.container(), self.status('FINISHED'), publish.NetworkError('timed out')])
        with self.assertRaises(publish.OutcomeUnknown):
            publish.instagram_item(self.ctx(self.insta_item(), http, self.config))

    def test_malformed_container_or_status_answer_stops(self):
        sequences = [[self.image_ok(), (200, {}, b'<html>')],
                     [self.image_ok(), (200, {}, b'{}')],
                     [self.image_ok(), self.container(), (200, {}, b'<html>')],
                     [self.image_ok(), self.container(), (200, {}, b'{}')]]
        for sequence in sequences:
            with self.subTest(sequence=sequence), self.assertRaises(publish.StopItem):
                publish.instagram_item(self.ctx(self.insta_item(), FakeHttp(sequence), self.config))

    def test_malformed_publish_answer_is_unknown(self):
        for answer in ((200, {}, b'<html>'), (200, {}, b'{}')):
            http = FakeHttp([self.image_ok(), self.container(), self.status('FINISHED'), answer])
            with self.subTest(answer=answer), self.assertRaises(publish.OutcomeUnknown):
                publish.instagram_item(self.ctx(self.insta_item(), http, self.config))

    def test_malformed_permalink_still_posts(self):
        http = FakeHttp([self.image_ok(), self.container(), self.status('FINISHED'), self.published(), (200, {}, b'<html>')])
        self.assertEqual(publish.instagram_item(self.ctx(self.insta_item(), http, self.config)), ('m1', ''))

    def test_error_body_that_is_not_json_follows_the_status_table(self):
        http = FakeHttp([self.image_ok(), self.container(), self.status('FINISHED'), (400, {}, b'<html>')])
        with self.assertRaises(publish.StopItem):
            publish.instagram_item(self.ctx(self.insta_item(), http, self.config))

    def test_permalink_failure_still_posts(self):
        http = FakeHttp([self.image_ok(), self.container(), self.status('FINISHED'), self.published(), publish.NetworkError('down')])
        self.assertEqual(publish.instagram_item(self.ctx(self.insta_item(), http, self.config)), ('m1', ''))


YT_CONFIG = {'client_id': 'cid', 'client_secret': 'csec', 'refresh_token': 'rt', 'category_id': '28'}
SESSION = 'https://upload.example/session/1'


class YouTubeTest(AdapterTest):
    def setUp(self):
        super().setUp()
        (self.step / 'media' / 'reel.mp4').write_bytes(b'0123456789')  # 10 bytes

    def short(self):
        return item('youtube-short', platform='youtube', action='upload', title='Demo title', text='Meta\n\nhttps://moderaty.com/blog/demo',
                    media=[{'path': 'media/reel.mp4', 'sha256': 'unused'}])

    def token(self):
        return (200, {}, json.dumps({'access_token': 'at'}).encode())

    def session(self, location=SESSION):
        return (200, {'location': location}, b'')

    def done(self, video_id='vid1'):
        return (200, {}, json.dumps({'id': video_id}).encode())

    def test_refresh_token_is_exchanged_first(self):
        http = FakeHttp([self.token(), self.session(), self.done()])
        publish.youtube_item(self.ctx(self.short(), http, {'youtube': YT_CONFIG}))
        self.assertEqual(http.requests[0][:2], ('POST', 'https://oauth2.googleapis.com/token'))
        self.assertIn(b'grant_type=refresh_token', http.requests[0][3])
        method, url, headers, body = http.requests[1]
        self.assertEqual(headers['Authorization'], 'Bearer at')
        self.assertEqual(headers['X-Upload-Content-Length'], '10')
        self.assertEqual(json.loads(body), {'snippet': {'title': 'Demo title', 'description': 'Meta\n\nhttps://moderaty.com/blog/demo',
                                                        'categoryId': '28'},
                                            'status': {'privacyStatus': 'public', 'selfDeclaredMadeForKids': False}})

    def test_new_session_is_saved_before_the_upload(self):
        http = FakeHttp([self.token(), self.session(), publish.NetworkError('reset')])
        ctx = self.ctx(self.short(), http, {'youtube': YT_CONFIG})
        with self.assertRaises(publish.RetryLater):
            publish.youtube_item(ctx)
        self.assertEqual(ctx.data['session_url'], SESSION)

    def test_saved_session_resumes_after_the_range(self):
        http = FakeHttp([self.token(), (308, {'range': 'bytes=0-3'}, b''), self.done()])
        ctx = self.ctx(self.short(), http, {'youtube': YT_CONFIG})
        ctx.data['session_url'] = SESSION
        publish.youtube_item(ctx)
        method, url, headers, body = http.requests[2]
        self.assertEqual(body, b'456789')
        self.assertEqual(headers['Content-Range'], 'bytes 4-9/10')

    def test_saved_session_without_range_starts_at_zero(self):
        http = FakeHttp([self.token(), (308, {}, b''), self.done()])
        ctx = self.ctx(self.short(), http, {'youtube': YT_CONFIG})
        ctx.data['session_url'] = SESSION
        publish.youtube_item(ctx)
        self.assertEqual(http.requests[2][3], b'0123456789')
        self.assertEqual(http.requests[2][2]['Content-Range'], 'bytes 0-9/10')

    def test_finished_upload_found_by_the_status_check_is_posted(self):
        http = FakeHttp([self.token(), self.done('vid2')])
        ctx = self.ctx(self.short(), http, {'youtube': YT_CONFIG})
        ctx.data['session_url'] = SESSION
        self.assertEqual(publish.youtube_item(ctx), ('vid2', 'https://www.youtube.com/shorts/vid2'))
        self.assertEqual(len(http.requests), 2)

    def test_expired_session_starts_over(self):
        http = FakeHttp([self.token(), (404, {}, b''), self.session('https://upload.example/session/new'), self.done()])
        ctx = self.ctx(self.short(), http, {'youtube': YT_CONFIG})
        ctx.data['session_url'] = SESSION
        publish.youtube_item(ctx)
        self.assertEqual(http.requests[3][1], 'https://upload.example/session/new')
        self.assertEqual(http.requests[3][2]['Content-Range'], 'bytes 0-9/10')

    def test_network_error_during_upload_retries(self):
        http = FakeHttp([self.token(), self.session(), publish.NetworkError('reset')])
        with self.assertRaises(publish.RetryLater):
            publish.youtube_item(self.ctx(self.short(), http, {'youtube': YT_CONFIG}))

    def test_upload_limit_exceeded_retries(self):
        limit = (403, {}, json.dumps({'error': {'errors': [{'reason': 'uploadLimitExceeded'}]}}).encode())
        http = FakeHttp([self.token(), self.session(), limit])
        with self.assertRaises(publish.RetryLater):
            publish.youtube_item(self.ctx(self.short(), http, {'youtube': YT_CONFIG}))

    def test_malformed_token_answer_stops(self):
        with self.assertRaises(publish.StopItem):
            publish.youtube_item(self.ctx(self.short(), FakeHttp([(200, {}, b'<html>')]), {'youtube': YT_CONFIG}))

    def test_status_answer_200_without_a_video_id_is_unknown(self):
        for answer in ((200, {}, b'<html>'), (200, {}, b'{}')):
            ctx = self.ctx(self.short(), FakeHttp([self.token(), answer]), {'youtube': YT_CONFIG})
            ctx.data['session_url'] = SESSION
            with self.subTest(answer=answer), self.assertRaises(publish.OutcomeUnknown):
                publish.youtube_item(ctx)

    def test_status_answer_with_an_odd_range_stops(self):
        ctx = self.ctx(self.short(), FakeHttp([self.token(), (308, {'range': 'items=0-3'}, b'')]), {'youtube': YT_CONFIG})
        ctx.data['session_url'] = SESSION
        with self.assertRaises(publish.StopItem):
            publish.youtube_item(ctx)

    def test_upload_answer_without_a_video_id_is_unknown(self):
        http = FakeHttp([self.token(), self.session(), (200, {}, b'{}')])
        with self.assertRaises(publish.OutcomeUnknown):
            publish.youtube_item(self.ctx(self.short(), http, {'youtube': YT_CONFIG}))

    def test_refused_refresh_token_stops(self):
        for status in (400, 401):
            with self.subTest(status=status), self.assertRaises(publish.StopItem):
                publish.youtube_item(self.ctx(self.short(), FakeHttp([(status, {}, b'invalid_grant')]), {'youtube': YT_CONFIG}))


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.campaigns = Path(tempfile.mkdtemp()) / 'campaigns'
        self.campaigns.mkdir()
        patcher = mock.patch.object(run, 'CAMPAIGNS_DIR', self.campaigns)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, *argv):
        with mock.patch.object(sys, 'argv', ['run.py', *argv]):
            run.main()

    def approved_campaign(self, events):
        campaign = self.campaigns / 'demo'
        (campaign / '09-publish').mkdir(parents=True)
        (campaign / '00-task.md').write_text(publish_task(deliverables='article, social', extra='## Publish to\nlinkedin\n\n' + ARTICLE_URL))
        plan = {'campaign': 'demo', 'article_url': 'https://moderaty.com/blog/demo', 'items': [
            {'id': 'article', 'platform': 'website', 'action': 'check', 'url': 'https://moderaty.com/blog/demo', 'depends_on': [], 'not_before': None},
            {'id': 'linkedin-post', 'platform': 'linkedin', 'action': 'post', 'text': 'One', 'media': [], 'depends_on': ['article'], 'not_before': None},
            {'id': 'linkedin-comment', 'platform': 'linkedin', 'action': 'comment', 'text': 'Two', 'media': [], 'depends_on': ['linkedin-post'], 'not_before': None}]}
        (campaign / '09-publish' / 'plan.json').write_text(json.dumps(plan))
        (campaign / '09-publish' / 'APPROVED').write_text('{}\n')
        (campaign / '09-publish' / 'publish.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
        return campaign

    def test_publish_requires_due_or_retry(self):
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit) as raised:
            self.run_main('publish')
        self.assertEqual(raised.exception.code, 2)

    def test_status_shows_item_states(self):
        self.approved_campaign([
            {'time': 't', 'item': 'article', 'event': 'attempt', 'attempt': 1},
            {'time': 't', 'item': 'article', 'event': 'posted', 'attempt': 1, 'post_id': '', 'url': 'https://moderaty.com/blog/demo'},
            {'time': 't', 'item': 'linkedin-post', 'event': 'attempt', 'attempt': 1},
            {'time': 't', 'item': 'linkedin-post', 'event': 'posted', 'attempt': 1, 'post_id': 'urn:li:share:1',
             'url': 'https://www.linkedin.com/feed/update/urn:li:share:1/'},
            {'time': 't', 'item': 'linkedin-comment', 'event': 'attempt', 'attempt': 1},
            {'time': 't', 'item': 'linkedin-comment', 'event': 'failed', 'attempt': 1, 'error': 'HTTP 403: duplicate content'}])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_main('status', 'demo')
        text = out.getvalue()
        self.assertIn('09-publish', text)
        self.assertIn('approved', text)
        self.assertIn('https://www.linkedin.com/feed/update/urn:li:share:1/', text)
        self.assertIn('failed', text)
        self.assertIn('HTTP 403: duplicate content', text)

    def test_retry_of_a_posted_item_is_refused(self):
        self.approved_campaign([{'time': 't', 'item': 'linkedin-post', 'event': 'attempt', 'attempt': 1},
                                {'time': 't', 'item': 'linkedin-post', 'event': 'posted', 'attempt': 1, 'post_id': 'urn:li:share:1',
                                 'url': 'https://www.linkedin.com/feed/update/urn:li:share:1/'}])
        with self.assertRaises(SystemExit) as raised:
            self.run_main('publish', '--retry', 'demo', 'linkedin-post')
        self.assertIn('its state is posted', str(raised.exception.code))

    def test_status_stops_when_the_plan_is_missing(self):
        campaign = self.approved_campaign([])
        (campaign / '09-publish' / 'plan.json').unlink()
        with self.assertRaises(SystemExit) as raised:
            self.run_main('status', 'demo')
        self.assertIn('APPROVED exists but plan.json is missing; nothing was sent', str(raised.exception.code))

    def test_retry_clears_a_failed_item(self):
        campaign = self.approved_campaign([{'time': 't', 'item': 'linkedin-comment', 'event': 'attempt', 'attempt': 1},
                                           {'time': 't', 'item': 'linkedin-comment', 'event': 'failed', 'attempt': 1, 'error': 'bad token'}])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_main('publish', '--retry', 'demo', 'linkedin-comment')
        self.assertEqual(out.getvalue(), 'Reset linkedin-comment. The next publish --due sends it.\n')
        plan = json.loads((campaign / '09-publish' / 'plan.json').read_text())
        self.assertEqual(publish.item_states(campaign / '09-publish', plan, 0)['linkedin-comment']['state'], 'pending')

if __name__ == '__main__':
    unittest.main()
