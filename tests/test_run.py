import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'marketing'))
import run  # noqa: E402

ROLES = {
    'writer': {'model': 'w', 'effort': 'max'},
    'reviewer': {'model': 'r', 'effort': 'medium'},
    'creator': {'model': 'r', 'effort': 'high'},
}
TEXT_STEPS = [
    {'id': '01-brief', 'skill': 'brief', 'kind': 'text', 'role': 'writer', 'deliverable': 'article', 'web': False, 'check': None,
     'inputs': ['00-task'], 'review_inputs': ['00-task'], 'writer_skills': [], 'review_skills': []},
    {'id': '02-article', 'skill': 'seo-article', 'kind': 'text', 'role': 'writer', 'deliverable': 'article', 'web': False, 'check': None,
     'inputs': ['00-task', '01-brief'], 'review_inputs': ['00-task', '01-brief'], 'writer_skills': [], 'review_skills': []},
    {'id': '03-x', 'skill': 'x', 'kind': 'text', 'role': 'writer', 'deliverable': 'social', 'web': False, 'check': 'x',
     'inputs': ['01-brief', '02-article'], 'review_inputs': ['01-brief', '02-article'], 'writer_skills': [], 'review_skills': []},
]
USAGE = {'inputTokens': 100, 'cachedInputTokens': 40, 'outputTokens': 10, 'reasoningOutputTokens': 5, 'totalTokens': 110}
MODELS = {'w': ['max'], 'r': ['medium', 'high']}
SAMPLE_TASK = Path(__file__).resolve().parents[1] / 'marketing' / 'examples' / 'sample-task.md'


META_LINE = 'Meta description: ' + 'x' * 155  # a valid meta description length


def full_brief(plan, channel='Not needed.'):
    texts = {'Channel ideas': channel, 'Visual ideas': 'Not needed.'}
    body = ''.join(f"## {name}\n{texts.get(name, 'Text.')}\n\n" for name in run.BRIEF_SECTIONS if name not in ('Article plan', 'Facts'))
    return f'## Article plan\n{plan}\n\n## Facts\n- A fact https://example.com (checked 2026-11-03)\n\n' + body


def article_text(meta_chars=155, words=6):
    return 'Meta description: ' + 'x' * meta_chars + '\n# Title\n' + ' '.join(['word'] * (words - 1))


def task_text(deliverables='article', without=None, design_system='None.'):
    parts = [
        ('Deliverables', deliverables),
        ('Goal', 'Get existing users to try the export before the tax season.'),
        ('Audience', 'Freelancers who do their own taxes.'),
        ('Details', 'Product: Ledgerly, a bookkeeping app for freelancers.'),
        ('Sources', 'None.'),
        ('Required headings', 'None.'),
        ('Constraints', 'None.'),
        ('Design system', design_system),
    ]
    body = ''.join(f'## {name}\n{text}\n\n' for name, text in parts if name != without)
    return '# Task: announce the new export feature\n\n' + body


class FakeClient:
    """Stands in for CodexClient. `verdicts` is the reviewer's answers, in order."""

    def __init__(self, verdicts, writer_text=None, models=MODELS, writer_outputs=()):
        self.verdicts = list(verdicts)
        self.writer_text = writer_text
        self.writer_outputs = list(writer_outputs)  # JSON texts for the writer's calls, in order
        self.models_available = models
        self.prompts = []
        self.calls = []
        self.web_searches = []
        self.thread_params = []  # the thread_id each call was sent with (None means a new thread)

    def models(self):
        return self.models_available

    def run_turn(self, prompt, model, effort, cwd, output_schema=None, images=(), web_search='disabled', thread_id=None, persist=False):
        self.prompts.append(prompt)
        self.calls.append((model, effort))
        self.web_searches.append(web_search)
        self.thread_params.append(thread_id)
        thread_id = thread_id or f'thread-{len(self.prompts)}'
        if output_schema is run.REVIEW_SCHEMA:
            verdict = self.verdicts.pop(0)
            findings = [] if verdict == 'pass' else [{'severity': 'blocking', 'issue': 'too short', 'fix': 'make it longer'}]
            text = json.dumps({'verdict': verdict, 'summary': 'Checked.', 'findings': findings})
        elif self.writer_outputs:
            text = self.writer_outputs.pop(0)
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

    def test_third_revise_goes_to_owner(self):
        client = FakeClient(['revise', 'revise', 'revise', 'pass'])
        self.go(client, answers('a', 'a'))
        self.assertEqual(len(client.prompts), 8)
        self.assertTrue((self.campaign / '01-brief' / 'APPROVED').is_file())

    def test_two_revises_then_pass(self):
        client = FakeClient(['revise', 'revise', 'pass', 'pass'])
        self.go(client, answers('a', 'a'))
        self.assertEqual(len(client.prompts), 8)
        self.assertIsNone(client.thread_params[0])  # call 1 starts a new writer thread
        self.assertEqual(client.thread_params[2], 'thread-1')  # call 3 continues it
        self.assertEqual(client.thread_params[4], 'thread-1')  # call 5 continues it too

    def test_owner_reject_resets_the_loop(self):
        client = FakeClient(['pass', 'revise', 'revise', 'revise', 'pass'])
        self.go(client, answers('r', 'shorter', 'a', 'a'))
        self.assertEqual(len(client.prompts), 10)

    def test_failed_checks_count_as_a_round(self):
        (self.campaign / '00-task.md').write_text(task_text('article, social'))
        texts = ['brief', 'article', 'a' * 300, 'a' * 300, 'a' * 300]
        client = FakeClient(['pass', 'pass'], writer_outputs=[json.dumps({'output': text, 'notes': ''}) for text in texts])
        self.go(client, answers('a', 'a', 'a'))
        self.assertEqual(len(client.prompts), 7)  # three X writer calls; the reviewer is never called for X
        self.assertIn('write x', client.prompts[4])
        self.assertIn('the limit is 280', client.prompts[5])
        self.assertIn('the limit is 280', client.prompts[6])

    def test_owner_notes_reach_the_writer(self):
        client = FakeClient(['pass', 'pass', 'pass'])
        self.go(client, answers('r', 'add the price', 'a', 'a'))
        self.assertIn('add the price', client.prompts[2])

    def test_reviewer_revise_continues_the_writer_thread(self):
        client = FakeClient(['revise', 'pass', 'pass'])
        self.go(client, answers('a', 'a'))
        self.assertEqual(client.thread_params[2], 'thread-1')  # the rewrite continues the first writer thread
        self.assertIsNone(client.thread_params[1])  # reviewers always start a new thread
        self.assertIn('too short', client.prompts[2])
        self.assertNotIn('Ledgerly', client.prompts[2])  # the revision does not resend the task

    def test_owner_notes_reach_the_writer_thread(self):
        client = FakeClient(['pass', 'pass', 'pass'])
        self.go(client, answers('r', 'add the price', 'a', 'a'))
        self.assertIn('add the price', client.prompts[2])
        self.assertEqual(client.thread_params[2], 'thread-1')

    def test_reject_after_resume_uses_the_saved_thread(self):
        with self.assertRaises(SystemExit):
            self.go(FakeClient(['pass']), answers('q'))
        client = FakeClient(['pass', 'pass'])
        self.go(client, answers('r', 'shorter', 'a', 'a'))
        self.assertEqual(client.thread_params[0], 'thread-1')
        self.assertIn('shorter', client.prompts[0])

    def test_prompts_put_inputs_before_the_skill(self):
        client = FakeClient(['pass', 'pass'])
        self.go(client, answers('a', 'a'))
        prompt = client.prompts[0]
        self.assertLess(prompt.index('--- BEGIN 00-task ---'), prompt.index('write brief'))

    def test_usage_counts_each_thread_once(self):
        self.go(FakeClient(['revise', 'pass', 'pass']), answers('a', 'a'))
        self.assertEqual(run.usage_totals(self.campaign / '01-brief')['inputTokens'], 300)

    def test_quit_then_resume_goes_straight_to_decision(self):
        with self.assertRaises(SystemExit):
            self.go(FakeClient(['pass']), answers('q'))
        client = FakeClient(['pass'])
        self.go(client, answers('a', 'a'))
        self.assertEqual(len(client.prompts), 2)
        self.assertIn('write seo-article', client.prompts[0])

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

    def test_failed_checks_skip_the_reviewer(self):
        (self.campaign / '00-task.md').write_text(task_text('article, social'))
        texts = ['brief', 'article', 'a' * 300, 'Export invoices in one click. [ARTICLE LINK]']
        client = FakeClient(['pass', 'pass', 'pass'], writer_outputs=[json.dumps({'output': text, 'notes': ''}) for text in texts])
        self.go(client, answers('a', 'a', 'a'))
        self.assertEqual(len(client.prompts), 7)  # the X draft that fails the checks gets no reviewer call
        self.assertIn('the limit is 280', client.prompts[5])

    def test_run_log_records_each_call_and_decision(self):
        with run.campaign_log(self.campaign, console=False):
            self.go(FakeClient(['pass', 'pass']), answers('a', 'a'))
        text = (self.campaign / 'run.log').read_text()
        self.assertIn('writer: asking w (effort max', text)
        self.assertIn('reviewer: asking r (effort medium', text)
        self.assertIn('[01-brief] review verdict pass', text)
        self.assertIn('[01-brief] approved by the owner', text)
        self.assertIn('final package written', text)
        self.assertNotIn('Ledgerly', text)  # task text is never logged

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


class ChecksTest(unittest.TestCase):
    def test_x_counts_the_link_as_23_characters(self):
        self.assertTrue(run.check_x('a' * 260 + ' ' + run.LINK, '', None))
        self.assertEqual(run.check_x('a' * 250 + ' ' + run.LINK, '', None), [])

    def test_x_thread_needs_the_link_in_the_last_post(self):
        self.assertTrue(run.check_x('1/ First post ' + run.LINK + '\n2/ Second post', '', None))
        self.assertEqual(run.check_x('1/ First post\n2/ Second post ' + run.LINK, '', None), [])

    def test_instagram_image_text_word_limit(self):
        def instagram(words):
            return f'## Caption\nHook. Link in bio\n\n## Hashtags\n#one #two #three\n\n## Image text\n{words}\n'
        self.assertEqual(run.check_instagram(instagram('one two three four five six seven eight'), '', None), [])
        self.assertTrue(run.check_instagram(instagram('one two three four five six seven eight nine'), '', None))

    def test_linkedin_link_goes_in_first_comment(self):
        bad = '## Post\nHook line.\n\nBody with a link ' + run.LINK + ' #a #b #c\n\n## First comment\n' + run.LINK + '\n'
        good = '## Post\nHook line.\n\nBody text. #a #b #c\n\nLink in the first comment.\n\n## First comment\nRead the guide: ' + run.LINK + '\n'
        self.assertTrue(run.check_linkedin(bad, '', None))
        self.assertEqual(run.check_linkedin(good, '', None), [])

    def test_article_required_headings_in_order(self):
        task = task_text().replace('## Required headings\nNone.', '## Required headings\n- Pricing\n- Setup')
        brief = full_brief('- Target length: 6 words')
        good = META_LINE + '\n\n# Export guide\n\n## Pricing\nText.\n\n## Setup\nText.\n'
        bad = META_LINE + '\n\n# Export guide\n\n## Setup\nText.\n\n## Pricing\nText.\n'
        self.assertEqual(run.check_article(good, task, brief), [])
        self.assertTrue(run.check_article(bad, task, brief))

    def test_sources_facts_need_a_source(self):
        bad = '## Facts\n- Ledgerly exports CSV files\n\n## Not found\nNone.\n'
        good = '## Facts\n- Ledgerly exports CSV files https://example.com/export (checked 2026-11-03)\n- Plans start in December (from task)\n\n## Not found\nNone.\n'
        self.assertTrue(run.check_sources(bad, '', None))
        self.assertEqual(run.check_sources(good, '', None), [])


class ImagesTest(unittest.TestCase):
    def setUp(self):
        self.step_dir = Path(tempfile.mkdtemp()) / '07-images'
        self.step_dir.mkdir(parents=True)

    def test_images_error_includes_writer_notes(self):
        with self.assertRaises(run.WorkflowError) as caught:
            run.materialize_images(self.step_dir, {'projects': [], 'notes': 'blocked'})
        self.assertIn('blocked', str(caught.exception))

    def test_images_need_the_three_names(self):
        projects = [{'name': 'linkedin', 'look_time': 1, 'files': []}, {'name': 'x', 'look_time': 1, 'files': []}]
        with self.assertRaises(run.WorkflowError) as caught:
            run.materialize_images(self.step_dir, {'projects': projects, 'notes': ''})
        self.assertIn('instagram', str(caught.exception))


class DesignSystemTest(unittest.TestCase):
    def test_design_system_is_required_for_images(self):
        self.assertTrue(run.check_task(task_text('article, social, images', design_system='None.')))

    def test_design_system_file_must_exist(self):
        errors = run.check_task(task_text('article, social, images', design_system='nosuchsystem'))
        self.assertTrue(any('not found' in error for error in errors))

    def test_check_design_system(self):
        template = (run.DESIGN_DIR / 'TEMPLATE.md').read_text()
        self.assertEqual(run.check_design_system(template), [])
        self.assertTrue(run.check_design_system(template.replace('- accent: #3366FF\n', '')))

    def test_images_get_the_design_system_first(self):
        campaign = Path(tempfile.mkdtemp()) / 'campaigns' / 'demo'
        campaign.mkdir(parents=True)
        (campaign / '00-task.md').write_text(task_text('article, social, images', design_system='moderaty'))
        brief = campaign / '02-brief'
        brief.mkdir()
        (brief / 'APPROVED').write_text('done\n')
        (brief / 'output.md').write_text('## Core message\nOne idea.\n\n## Facts\n- Fact https://example.com (checked 2026-11-03)\n\n## Visual ideas\nA card.\n')
        for step_id in ('04-linkedin', '05-x', '06-instagram'):
            step_dir = campaign / step_id
            step_dir.mkdir()
            (step_dir / 'APPROVED').write_text('done\n')
            (step_dir / 'output.md').write_text('Text.\n')
        step = next(s for s in run.STEPS if s['id'] == '07-images')
        prompt = run.writer_prompt(run.SKILLS_DIR, campaign, step)
        self.assertLess(prompt.index('--- BEGIN design-system ---'), prompt.index('--- BEGIN 02-brief#Core message ---'))


class StopTest(unittest.TestCase):
    def test_stop_kills_a_server_that_ignores_terminate(self):
        import codex_client

        class FakeProc:
            def __init__(self):
                self.waits, self.killed = 0, False

            def terminate(self):
                pass

            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise subprocess.TimeoutExpired('codex', timeout)

            def kill(self):
                self.killed = True

        class FakeStream:
            closed = False

            def close(self):
                self.closed = True

        client = codex_client.CodexClient.__new__(codex_client.CodexClient)  # no real server
        client.proc, client.stderr = FakeProc(), FakeStream()
        client.close()
        self.assertTrue(client.proc.killed)
        self.assertTrue(client.stderr.closed)


class OpenFilmTest(unittest.TestCase):
    def test_run_tool_timeout_is_a_workflow_error(self):
        with mock.patch.object(run.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffmpeg', 900)):
            with self.assertRaises(run.WorkflowError) as caught:
                run.run_tool([sys.executable, '-c', 'pass'], Path(tempfile.mkdtemp()))
        self.assertIn('did not finish within 900 s', str(caught.exception))

    def test_write_project_marks_it_opened(self):
        project = Path(tempfile.mkdtemp()) / 'p'
        run.write_project(project, [{'path': 'film.html', 'content': '<html></html>'}])
        self.assertTrue((project / '.film' / 'opened').is_file())

    def test_run_tool_error_shows_stdout_and_stderr(self):
        command = [sys.executable, '-c', 'import sys; print("OUT"); print("ERR", file=sys.stderr); sys.exit(1)']
        with self.assertRaises(run.WorkflowError) as caught:
            run.run_tool(command, Path(tempfile.mkdtemp()))
        self.assertIn('OUT', str(caught.exception))
        self.assertIn('ERR', str(caught.exception))


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.skills = self.tmp / 'skills'
        (self.skills / 'brief').mkdir(parents=True)
        (self.skills / 'brief' / 'SKILL.md').write_text('write brief')
        (self.skills / 'brief' / 'REVIEW.md').write_text('review brief')
        self.campaign = self.tmp / 'campaigns' / 'demo'
        self.campaign.mkdir(parents=True)
        (self.campaign / '00-task.md').write_text(task_text())

    def test_library_skills_go_between_inputs_and_contract(self):
        prompt = run.writer_prompt(run.SKILLS_DIR, self.campaign, run.STEPS[0])  # 01-sources
        self.assertLess(prompt.index('--- BEGIN 00-task ---'), prompt.index('--- BEGIN skill: content-research-and-sourcing ---'))
        self.assertLess(prompt.index('--- BEGIN skill: content-research-and-sourcing ---'), prompt.index(run.CONTRACT_RULE))
        self.assertLess(prompt.index(run.CONTRACT_RULE), prompt.index('# Sources researcher'))

    def test_missing_library_skill_stops_before_any_codex_call(self):
        step = dict(TEXT_STEPS[0], writer_skills=['no-such-skill'])
        client = FakeClient([])
        with self.assertRaises(run.WorkflowError):
            run.run_campaign(self.campaign, client, ask=answers(), steps=[step], skills_dir=self.skills, roles=ROLES)
        self.assertEqual(client.prompts, [])

    def test_social_steps_share_the_longest_prefix(self):
        for step_id, text in (('02-brief', '## Facts\n- A fact https://example.com (checked 2026-11-03)\n## Channel ideas\nIdea.\n'),
                              ('03-article', '# Title\n## One\nBody.\n')):
            folder = self.campaign / step_id
            folder.mkdir()
            (folder / 'output.md').write_text(text)
            (folder / 'APPROVED').write_text('done\n')
        social = [step for step in run.STEPS if step['id'] in ('04-linkedin', '05-x', '06-instagram')]
        prompts = [run.writer_prompt(run.SKILLS_DIR, self.campaign, step) for step in social]
        self.assertIn('--- END skill: hook-writer ---', os.path.commonprefix(prompts))


class LengthTest(unittest.TestCase):
    def test_brief_needs_one_target_length(self):
        self.assertTrue(any('exactly one' in e for e in run.check_brief(full_brief('- Content type: guide.'), task_text(), None)))
        self.assertTrue(any('exactly one' in e for e in run.check_brief(full_brief('- Target length: 800 words\n- Target length: 900 words'), task_text(), None)))
        self.assertEqual(run.check_brief(full_brief('- Target length: 800 words'), task_text(), None), [])

    def test_article_meta_description_length(self):
        brief = full_brief('- Target length: 6 words')

        def meta_errors(meta_chars):
            return [e for e in run.check_article(article_text(meta_chars, 6), task_text(), brief) if 'meta description' in e]

        self.assertTrue(meta_errors(149))
        self.assertEqual(meta_errors(150), [])
        self.assertEqual(meta_errors(160), [])
        self.assertTrue(meta_errors(161))

    def test_article_length_within_20_percent(self):
        brief = full_brief('- Target length: 1000 words')

        def length_errors(count):
            return [e for e in run.check_article(article_text(155, count), task_text(), brief) if 'article is' in e]

        self.assertTrue(length_errors(799))
        self.assertEqual(length_errors(800), [])
        self.assertEqual(length_errors(1200), [])
        self.assertTrue(length_errors(1201))

    def test_article_check_without_target_length_raises(self):
        with self.assertRaises(run.WorkflowError):
            run.check_article(article_text(155, 6), task_text(), full_brief('- Content type: guide.'))


class ChannelIdeasTest(unittest.TestCase):
    def test_brief_needs_all_three_channel_ideas(self):
        social = task_text('article, social')
        target = '- Target length: 800 words'
        all_three = '- LinkedIn: one idea.\n- X: one idea.\n- Instagram: one idea.'
        self.assertEqual(run.check_brief(full_brief(target, all_three), social, None), [])
        missing = '- LinkedIn: one idea.\n- X: one idea.'
        self.assertTrue(any('Instagram' in e for e in run.check_brief(full_brief(target, missing), social, None)))
        self.assertTrue(run.check_brief(full_brief(target, 'Not needed.'), social, None))
        self.assertTrue(run.check_brief(full_brief(target, all_three), task_text(), None))  # article only: must say Not needed.


class StatusTest(unittest.TestCase):
    def test_status_total_includes_import_usage(self):
        import contextlib
        import io
        tmp = Path(tempfile.mkdtemp())
        campaign = tmp / 'campaigns' / 'demo'
        campaign.mkdir(parents=True)
        (campaign / '00-task.md').write_text(task_text())
        record = {'time': 'x', 'call': 'importer', 'model': 'm', 'effort': 'e', 'thread': 't1',
                  'total': {'inputTokens': 100, 'cachedInputTokens': 40, 'outputTokens': 10, 'reasoningOutputTokens': 5}}
        (campaign / 'usage.jsonl').write_text(json.dumps(record) + '\n')
        out = io.StringIO()
        with mock.patch.object(run, 'CAMPAIGNS_DIR', tmp / 'campaigns'), contextlib.redirect_stdout(out):
            run.cmd_status('demo')
        total = [line for line in out.getvalue().splitlines() if line.startswith('total')][0]
        self.assertIn('input 100 (non-cached 60, cached 40), output 10 (reasoning 5)', total)


class FrameTest(unittest.TestCase):
    def test_sample_frames_keeps_ten_frames_with_seconds(self):
        def fake_ffmpeg(args, cwd):
            for index in range(1, 26):
                (cwd / f'{index:03d}.png').write_bytes(b'x')

        with mock.patch.object(run, 'run_tool', side_effect=fake_ffmpeg):
            frames, seconds = run.sample_frames(Path('film.mp4'), Path(tempfile.mkdtemp()) / 'frames')
        self.assertEqual(len(frames), 10)
        self.assertEqual(seconds, [0, 2, 5, 7, 10, 12, 15, 17, 20, 22])
        self.assertIn('0, 2, 5', run.frame_note([str(second) for second in seconds]))


class CubicFixesTest(unittest.TestCase):
    def test_linkedin_post_needs_the_link_line(self):
        body = '## Post\nHook line.\n\nBody text. #a #b #c\n\nLink in the first comment.\n\n## First comment\nRead: ' + run.LINK + '\n'
        without = body.replace('Link in the first comment.\n\n', '')
        self.assertEqual(run.check_linkedin(body, '', None), [])
        self.assertTrue(any('Link in the first comment.' in e for e in run.check_linkedin(without, '', None)))

    def test_x_numbers_must_run_in_order(self):
        errors = run.check_x('1/ First post\n3/ Second post ' + run.LINK, '', None)
        self.assertTrue(any('post numbers must run 1/ to 2/ in order; found 1/, 3/' in e for e in errors))
        self.assertEqual(run.check_x('1/ First post\n2/ Second post ' + run.LINK, '', None), [])

    def test_models_stops_when_next_cursor_is_missing(self):
        import codex_client
        client = codex_client.CodexClient.__new__(codex_client.CodexClient)  # no real server
        client._request = lambda method, params: {'data': [{'model': 'm', 'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]}]}
        self.assertEqual(client.models(), {'m': ['low']})

    def test_sources_step_searches_the_web(self):
        campaign = Path(tempfile.mkdtemp()) / 'campaigns' / 'demo'
        campaign.mkdir(parents=True)
        (campaign / '00-task.md').write_text(task_text())
        sources = json.dumps({'output': '## Facts\n- A fact https://example.com (checked 2026-11-03)\n\n## Not found\nNone.\n', 'notes': ''})
        client = FakeClient(['pass'], writer_outputs=[sources])
        run.run_campaign(campaign, client, ask=answers('a'), steps=[run.STEPS[0]], skills_dir=run.SKILLS_DIR, roles=ROLES)
        self.assertEqual(client.web_searches, ['live', 'live'])  # writer and reviewer
        other = Path(tempfile.mkdtemp()) / 'campaigns' / 'demo'
        other.mkdir(parents=True)
        (other / '00-task.md').write_text(task_text())
        plain = FakeClient(['pass'])
        run.run_campaign(other, plain, ask=answers('a'), steps=[dict(TEXT_STEPS[0])], skills_dir=run.SKILLS_DIR, roles=ROLES)
        self.assertTrue(plain.web_searches)
        self.assertTrue(all(value == 'disabled' for value in plain.web_searches))


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


class ReadInputTest(unittest.TestCase):
    def test_outline_drops_the_body(self):
        campaign = Path(tempfile.mkdtemp()) / 'campaign'
        step_dir = campaign / '03-article'
        step_dir.mkdir(parents=True)
        (step_dir / 'APPROVED').write_text('done\n')
        (step_dir / 'output.md').write_text('Meta description: A guide.\n\n# Title\n\n## One\nBody text.\n\n## Two\nMore body.\n')
        self.assertEqual(run.read_input(campaign, '03-article#outline'), 'Meta description: A guide.\n# Title\n## One\n## Two')


class SkillFilesTest(unittest.TestCase):
    def test_every_step_has_skill_files(self):
        run.check_skills(run.SKILLS_DIR, run.STEPS)
        run.load_skill(run.SKILLS_DIR, 'import', 'SKILL.md')


if __name__ == '__main__':
    unittest.main()
