import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'marketing'))
import run  # noqa: E402

ROLES = {
    'writer': {'model': 'w', 'effort': 'max'},
    'reviewer': {'model': 'r', 'effort': 'medium'},
    'creator': {'model': 'r', 'effort': 'high'},
}
TEXT_STEPS = [
    {'id': '01-brief', 'skill': 'brief', 'kind': 'text', 'role': 'writer', 'deliverable': 'article', 'web': False, 'inputs': ['00-task']},
    {'id': '02-article', 'skill': 'seo-article', 'kind': 'text', 'role': 'writer', 'deliverable': 'article', 'web': False, 'inputs': ['00-task', '01-brief']},
    {'id': '03-x', 'skill': 'x', 'kind': 'text', 'role': 'writer', 'deliverable': 'social', 'web': False, 'inputs': ['01-brief', '02-article']},
]
USAGE = {'inputTokens': 100, 'cachedInputTokens': 40, 'outputTokens': 10, 'reasoningOutputTokens': 5, 'totalTokens': 110}
MODELS = {'w': ['max'], 'r': ['medium', 'high']}
SAMPLE_TASK = Path(__file__).resolve().parents[1] / 'marketing' / 'examples' / 'sample-task.md'


def task_text(deliverables='article', without=None):
    parts = [
        ('Deliverables', deliverables),
        ('Goal', 'Get existing users to try the export before the tax season.'),
        ('Audience', 'Freelancers who do their own taxes.'),
        ('Details', 'Product: Ledgerly, a bookkeeping app for freelancers.'),
        ('Sources', 'None.'),
        ('Required headings', 'None.'),
        ('Constraints', 'None.'),
    ]
    body = ''.join(f'## {name}\n{text}\n\n' for name, text in parts if name != without)
    return '# Task: announce the new export feature\n\n' + body


class FakeClient:
    """Stands in for CodexClient. `verdicts` is the reviewer's answers, in order."""

    def __init__(self, verdicts, writer_text=None, models=MODELS):
        self.verdicts = list(verdicts)
        self.writer_text = writer_text
        self.models_available = models
        self.prompts = []
        self.calls = []
        self.web_searches = []

    def models(self):
        return self.models_available

    def run_turn(self, prompt, model, effort, cwd, output_schema=None, images=(), web_search='disabled'):
        self.prompts.append(prompt)
        self.calls.append((model, effort))
        self.web_searches.append(web_search)
        thread_id = f'thread-{len(self.prompts)}'
        if output_schema is run.REVIEW_SCHEMA:
            verdict = self.verdicts.pop(0)
            findings = [] if verdict == 'pass' else [{'severity': 'blocking', 'issue': 'too short', 'fix': 'make it longer'}]
            text = json.dumps({'verdict': verdict, 'summary': 'Checked.', 'findings': findings})
        elif self.writer_text is not None:
            text = self.writer_text
        else:
            text = json.dumps({'output': f'draft {len(self.prompts)}', 'notes': ''})
        return {'thread_id': thread_id, 'text': text, 'usage': USAGE}


def answers(*values):
    queue = list(values)
    return lambda _prompt: queue.pop(0)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.skills = self.tmp / 'skills'
        for skill in ('brief', 'seo-article', 'x'):
            (self.skills / skill).mkdir(parents=True)
            (self.skills / skill / 'SKILL.md').write_text(f'write {skill}')
            (self.skills / skill / 'REVIEW.md').write_text(f'review {skill}')
        self.campaign = self.tmp / 'campaigns' / 'demo'
        self.campaign.mkdir(parents=True)
        (self.campaign / '00-task.md').write_text(task_text())

    def go(self, client, ask):
        return run.run_campaign(self.campaign, client, ask=ask, steps=TEXT_STEPS, skills_dir=self.skills, roles=ROLES)

    def test_approving_every_step_builds_final_package(self):
        client = FakeClient(['pass', 'pass'])
        final = self.go(client, answers('a', 'a'))
        self.assertTrue((self.campaign / '01-brief' / 'APPROVED').is_file())
        self.assertTrue((self.campaign / '02-article' / 'APPROVED').is_file())
        self.assertIn('02-article.md', (final / 'index.md').read_text())
        self.assertEqual(len(client.prompts), 4)

    def test_deliverables_pick_the_steps(self):
        self.go(FakeClient(['pass', 'pass']), answers('a', 'a'))
        self.assertFalse((self.campaign / '03-x').exists())

    def test_invalid_task_stops_before_any_codex_call(self):
        (self.campaign / '00-task.md').write_text(task_text(without='Goal'))
        client = FakeClient([])
        with self.assertRaises(run.WorkflowError):
            self.go(client, answers())
        self.assertEqual(client.prompts, [])

    def test_reviewer_revise_triggers_one_automatic_rewrite(self):
        client = FakeClient(['revise', 'pass', 'pass'])
        self.go(client, answers('a', 'a'))
        self.assertEqual(len(client.prompts), 6)
        self.assertIn('too short', client.prompts[2])

    def test_second_revise_goes_to_owner(self):
        client = FakeClient(['revise', 'revise', 'pass'])
        self.go(client, answers('a', 'a'))
        self.assertEqual(len(client.prompts), 6)
        self.assertTrue((self.campaign / '01-brief' / 'APPROVED').is_file())

    def test_owner_notes_reach_the_writer(self):
        client = FakeClient(['pass', 'pass', 'pass'])
        self.go(client, answers('r', 'add the price', 'a', 'a'))
        self.assertIn('add the price', client.prompts[2])

    def test_quit_then_resume_goes_straight_to_decision(self):
        with self.assertRaises(SystemExit):
            self.go(FakeClient(['pass']), answers('q'))
        client = FakeClient(['pass'])
        self.go(client, answers('a', 'a'))
        self.assertEqual(len(client.prompts), 2)
        self.assertTrue(client.prompts[0].startswith('write seo-article'))

    def test_missing_skill_stops_before_any_codex_call(self):
        (self.skills / 'seo-article' / 'REVIEW.md').unlink()
        client = FakeClient([])
        with self.assertRaises(run.WorkflowError):
            self.go(client, answers())
        self.assertEqual(client.prompts, [])

    def test_invalid_json_from_writer_stops(self):
        with self.assertRaises(run.WorkflowError):
            self.go(FakeClient([], writer_text='not json'), answers())

    def test_usage_is_recorded_per_call(self):
        self.go(FakeClient(['pass', 'pass']), answers('a', 'a'))
        totals = run.usage_totals(self.campaign / '01-brief')
        self.assertEqual(totals['inputTokens'], 200)
        self.assertEqual(totals['cachedInputTokens'], 80)

    def test_roles_choose_model_and_effort(self):
        client = FakeClient(['pass', 'pass'])
        self.go(client, answers('a', 'a'))
        self.assertEqual(client.calls[0], ('w', 'max'))
        self.assertEqual(client.calls[1], ('r', 'medium'))

    def test_missing_model_stops_before_any_turn(self):
        client = FakeClient([], models={'r': ['medium']})
        with self.assertRaises(run.WorkflowError):
            self.go(client, answers())
        self.assertEqual(client.calls, [])

    def test_unsupported_effort_stops_before_any_turn(self):
        client = FakeClient([], models={'w': ['high'], 'r': ['medium']})
        with self.assertRaises(run.WorkflowError):
            self.go(client, answers())
        self.assertEqual(client.calls, [])

    def test_safe_write_refuses_paths_outside_project(self):
        with self.assertRaises(run.WorkflowError):
            run.safe_write(self.tmp / 'p', '../escape.html', 'x')


class TaskFormatTest(unittest.TestCase):
    def test_valid_task_has_no_errors(self):
        self.assertEqual(run.check_task(task_text()), [])

    def test_unknown_deliverable_is_an_error(self):
        errors = run.check_task(task_text('article, podcast'))
        self.assertTrue(any('podcast' in error for error in errors))

    def test_reel_without_social_is_an_error(self):
        errors = run.check_task(task_text('article, reel'))
        self.assertTrue(any('social' in error for error in errors))

    def test_sample_task_is_valid(self):
        self.assertEqual(run.check_task(SAMPLE_TASK.read_text()), [])

    def test_select_steps_keeps_order(self):
        ids = [step['id'] for step in run.select_steps(task_text('social, article'), run.STEPS)]
        self.assertEqual(ids, ['01-sources', '02-brief', '03-article', '04-linkedin', '05-x', '06-instagram'])


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / 'skills' / 'import').mkdir(parents=True)
        (self.tmp / 'skills' / 'import' / 'SKILL.md').write_text('import the issue')
        self.campaign = self.tmp / 'campaigns' / 'mc-32'
        self.campaign.mkdir(parents=True)

    def test_import_writes_the_task(self):
        client = FakeClient([], writer_text=json.dumps({'output': task_text(), 'notes': ''}))
        run.import_issue(client, self.campaign, 'MC-32', skills_dir=self.tmp / 'skills', roles=ROLES)
        self.assertEqual(run.check_task((self.campaign / '00-task.md').read_text()), [])
        self.assertIn('MC-32', client.prompts[0])

    def test_import_with_empty_output_stops(self):
        client = FakeClient([], writer_text=json.dumps({'output': '', 'notes': 'tracker unreachable'}))
        with self.assertRaises(run.WorkflowError):
            run.import_issue(client, self.campaign, 'MC-32', skills_dir=self.tmp / 'skills', roles=ROLES)
        self.assertFalse((self.campaign / '00-task.md').exists())


class SkillFilesTest(unittest.TestCase):
    def test_every_step_has_skill_files(self):
        run.check_skills(run.SKILLS_DIR, run.STEPS)
        run.load_skill(run.SKILLS_DIR, 'import', 'SKILL.md')


if __name__ == '__main__':
    unittest.main()
