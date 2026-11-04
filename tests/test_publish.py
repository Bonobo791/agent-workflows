import sys
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
