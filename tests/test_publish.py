import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'marketing'))
import run  # noqa: E402
from test_run import task_text  # noqa: E402

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


if __name__ == '__main__':
    unittest.main()
