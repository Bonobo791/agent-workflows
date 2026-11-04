import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'marketing'))
import run  # noqa: E402
from codex_client import CodexClient, CodexError  # noqa: E402

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


PROBLEMS = ['Scam comments impersonate the creator under new uploads within minutes.',
            'Spam links in replies go unnoticed for days.',
            'Held comments pile up with no clear review step.',
            'Duplicate bot accounts copy every new comment.',
            'Viewers stop reading when the top comments are spam.',
            'Moderation takes an hour a day across channels.']
PROBLEMS_TEXT = '\n'.join(f'- {problem} https://site{n % 3}.example.com/problem{n} (checked 2026-11-04)' for n, problem in enumerate(PROBLEMS))
ANGLE_TEXT = f'- Reader problem: {PROBLEMS[0]}\n- Tension: Text.\n- Payoff: Text.\n- Voice: Text.'
VALUE_TEXT = '\n'.join(f'- {problem} → The product holds the comment for review. Proof: https://example.com/proof' for problem in PROBLEMS)
REPO_TEMPLATE = Path(__file__).resolve().parents[1] / 'marketing' / 'brands' / 'TEMPLATE'
DESIGN_TEXT = ('# Design system: demo\n\n## Colors\n- background: #000000\n- text: #FFFFFF\n- accent: #3366FF\n\n'
               '## Typography\nText.\n\n## Layout\nText.\n\n## Logo\nNone.\n\n## Imagery\nText.\n\n## Do not\nText.\n')
VOICE_TEXT = ''.join(f'## {heading}\nFilled in.\n\n' for heading in run.VOICE_SECTIONS)
PAGES_TEXT = '# Pages\n\n- Compare tools | /compare/x/ | when a reader chooses a tool\n'
POSITIONING_TEXT = ('# Positioning\n\n## One line\nA demo product for demo creators. Read more at https://example.com/demo.\n\n'
                    '## Audience\nCreators.\n\n## Reader problems\n' + PROBLEMS_TEXT + '\n\n## Value\n' + VALUE_TEXT +
                    '\n\n## Proof\n- Holds comments for review. https://example.com/proof (checked 2026-11-04)\n\n'
                    '## Alternatives\nText.\n\n## Objections\nText.\n\n## Claims we never make\nNone.\n\n'
                    '## Call to action\nSign up at https://example.com/signup.\n')
BRANDS_TMP = Path(tempfile.mkdtemp()) / 'brands'
run.BRANDS_DIR = BRANDS_TMP
run.CAMPAIGNS_DIR = Path(tempfile.mkdtemp()) / 'campaigns'


CHARACTER_TEXT = '# Character: demo\n\n' + '\n'.join(f'## {heading}\n{body}\n' for heading, body in [
    ('Name', 'Ada'), ('Personality', 'Calm.\nDirect.\nNever preachy.'), ('Avatar id', 'avatar-1'), ('Voice id', 'voice-1'),
    ('Do', 'Speak to the viewer.'), ("Don't", 'Never joke about threats.'), ('Disclosure', 'The presenter is an AI avatar.')])


def write_brand(name, **files):
    folder = BRANDS_TMP / name
    folder.mkdir(parents=True, exist_ok=True)
    texts = {'design-system.md': DESIGN_TEXT, 'voice.md': VOICE_TEXT, 'positioning.md': POSITIONING_TEXT, 'pages.md': PAGES_TEXT,
             'character.md': CHARACTER_TEXT}
    texts.update(files)
    for file_name, text in texts.items():
        (folder / file_name).write_text(text)


write_brand('demo')
write_brand('unfilled', **{name: (REPO_TEMPLATE / name).read_text() for name in ['design-system.md', 'voice.md', 'positioning.md', 'pages.md']})


def full_brief(plan, channel='Not needed.'):
    texts = {'Channel ideas': channel, 'Visual ideas': 'Not needed.', 'Angle': ANGLE_TEXT, 'Reader problems': PROBLEMS_TEXT}
    body = ''.join(f"## {name}\n{texts.get(name, 'Text.')}\n\n" for name in run.BRIEF_SECTIONS if name not in ('Article plan', 'Facts'))
    return f'## Article plan\n{plan}\n\n## Facts\n- A fact https://example.com (checked 2026-11-03)\n\n' + body


def article_text(meta_chars=155, words=6):
    return 'Meta description: ' + 'x' * meta_chars + '\n# Title\n' + ' '.join(['word'] * (words - 1))


def task_text(deliverables='article', without=None, brand='demo'):
    parts = [
        ('Deliverables', deliverables),
        ('Goal', 'Get existing users to try the export before the tax season.'),
        ('Audience', 'Freelancers who do their own taxes.'),
        ('Details', 'Product: Ledgerly, a bookkeeping app for freelancers.'),
        ('Sources', 'None.'),
        ('Required headings', 'None.'),
        ('Constraints', 'None.'),
        ('Brand', brand),
    ]
    body = ''.join(f'## {name}\n{text}\n\n' for name, text in parts if name != without)
    return '# Task: announce the new export feature\n\n' + body


class FakeClient:
    """Stands in for CodexClient. `verdicts` is the reviewer's answers, in order."""

    def __init__(self, verdicts, writer_text=None, models=MODELS, writer_outputs=(), searches=()):
        self.verdicts = list(verdicts)
        self.writer_text = writer_text
        self.writer_outputs = list(writer_outputs)  # JSON texts for the writer's calls, in order
        self.searches = list(searches)  # web search queries each turn reports
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
            if verdict == 'minor':
                verdict, findings = 'pass', [{'severity': 'minor', 'issue': 'wordy', 'fix': 'trim'}]
            else:
                findings = [] if verdict == 'pass' else [{'severity': 'blocking', 'issue': 'too short', 'fix': 'make it longer'}]
            text = json.dumps({'verdict': verdict, 'summary': 'Checked.', 'findings': findings})
        elif self.writer_outputs:
            text = self.writer_outputs.pop(0)
        elif self.writer_text is not None:
            text = self.writer_text
        else:
            text = json.dumps({'output': f'draft {len(self.prompts)}', 'notes': ''})
        return {'thread_id': thread_id, 'text': text, 'usage': USAGE, 'searches': list(self.searches)}


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

    def test_two_revises_go_to_the_owner_after_one_rewrite(self):
        client = FakeClient(['revise', 'revise', 'pass', 'pass'])
        self.go(client, answers('a', 'a'))
        self.assertEqual(len(client.prompts), 6)  # writer, reviewer, writer, reviewer, owner; then the second step
        self.assertIsNone(client.thread_params[0])  # call 1 starts a new writer thread
        self.assertEqual(client.thread_params[2], 'thread-1')  # call 3, the one rewrite, continues it

    def test_owner_reject_resets_the_loop(self):
        client = FakeClient(['pass', 'revise', 'revise', 'revise', 'pass'])
        self.go(client, answers('r', 'shorter', '', 'a', 'a'))
        self.assertEqual(len(client.prompts), 10)

    def test_failed_checks_count_as_a_round(self):
        (self.campaign / '00-task.md').write_text(task_text('article, social'))
        texts = ['brief', 'article', 'a' * 300, 'a' * 300]
        client = FakeClient(['pass', 'pass'], writer_outputs=[json.dumps({'output': text, 'notes': ''}) for text in texts])
        self.go(client, answers('a', 'a', 'a'))
        self.assertEqual(len(client.prompts), 6)  # brief and article with reviews; two X writer calls, no reviewer for X
        self.assertIn('write x', client.prompts[4])
        self.assertIn('the limit is 280', client.prompts[5])  # the one automatic rewrite

    def test_owner_notes_take_several_lines_until_an_empty_line(self):
        client = FakeClient(['pass'] * 4)
        self.go(client, answers('r', 'first note', 'second note', '', 'a', 'a'))
        self.assertIn('first note\nsecond note', client.prompts[2])

    def test_owner_notes_reach_the_writer(self):
        client = FakeClient(['pass', 'pass', 'pass'])
        self.go(client, answers('r', 'add the price', '', 'a', 'a'))
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
        self.go(client, answers('r', 'add the price', '', 'a', 'a'))
        self.assertIn('add the price', client.prompts[2])
        self.assertEqual(client.thread_params[2], 'thread-1')

    def test_reject_after_resume_uses_the_saved_thread(self):
        with self.assertRaises(SystemExit):
            self.go(FakeClient(['pass']), answers('q'))
        client = FakeClient(['pass', 'pass'])
        self.go(client, answers('r', 'shorter', '', 'a', 'a'))
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
        with mock.patch.object(run, 'BRANDS_DIR', Path(__file__).resolve().parents[1] / 'marketing' / 'brands'):
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
            return f'## Post\nHook. Link in bio\n\n## Hashtags\n#one #two #three\n\n## Image text\n{words}\n'
        self.assertEqual(run.check_instagram(instagram('one two three four five six seven eight'), '', None), [])
        self.assertTrue(run.check_instagram(instagram('one two three four five six seven eight nine'), '', None))

    def test_linkedin_link_goes_in_first_comment(self):
        bad = '## Post\nHook line.\n\nBody with a link ' + run.LINK + ' #a #b #c\n\n## First comment\n' + run.LINK + '\n'
        good = '## Post\nHook line.\n\nBody text. #a #b #c\n\nLink in the first comment.\n\n## First comment\nRead the guide: ' + run.LINK + '\n'
        self.assertTrue(run.check_linkedin(bad, '', None))
        self.assertEqual(run.check_linkedin(good, '', None), [])

    def test_article_required_headings_in_order(self):
        task = task_text().replace('## Required headings\nNone.', '## Required headings\n- Pricing\n- Setup')
        brief = full_brief('- Target length: 14 words')
        opening = 'If you export often, use the export button.\n\n'
        good = META_LINE + '\n\n# Export guide\n\n' + opening + '## Pricing\nText.\n\n## Setup\nText.\n'
        bad = META_LINE + '\n\n# Export guide\n\n' + opening + '## Setup\nText.\n\n## Pricing\nText.\n'
        self.assertEqual(run.check_article(good, task, brief), [])
        self.assertTrue(run.check_article(bad, task, brief))

    def test_sources_facts_need_a_source(self):
        bad = '## Facts\n- Ledgerly exports CSV files\n\n## Not found\nNone.\n'
        self.assertTrue(any('no source' in error for error in run.check_sources(bad, '', None, ['q'] * 5)))


def sources_text(urls=5, sites=3, task_fact=True):
    lines = [f'- Fact {n} https://site{n % sites}.example.com/page{n} (checked 2026-11-03)' for n in range(urls)]
    if task_fact:
        lines.append('- Plans start in December (from task)')
    return '## Facts\n' + '\n'.join(lines) + '\n\n## Not found\nNone.\n'


class SourcesTest(unittest.TestCase):
    def test_enough_searches_urls_and_sites_pass(self):
        self.assertEqual(run.check_sources(sources_text(), '', None, ['q'] * 5), [])

    def test_fewer_than_three_searches_fail(self):
        errors = run.check_sources(sources_text(), '', None, ['q'] * 2)
        self.assertEqual(len(errors), 1)
        self.assertIn('ran 2 web searches', errors[0])

    def test_task_facts_do_not_count_as_url_facts(self):
        errors = run.check_sources(sources_text(urls=2), '', None, ['q'] * 5)
        self.assertIn('found 2', ' '.join(errors))

    def test_urls_must_come_from_three_sites(self):
        errors = run.check_sources(sources_text(sites=2), '', None, ['q'] * 5)
        self.assertIn('2 different sites', ' '.join(errors))

    def test_www_prefix_is_the_same_site(self):
        facts = ['- A https://www.a.com/x (checked 2026-11-03)', '- B https://a.com/y (checked 2026-11-03)',
                 '- C https://b.com/z (checked 2026-11-03)', '- D https://b.com/w (checked 2026-11-03)',
                 '- E https://www.b.com/v (checked 2026-11-03)']
        output = '## Facts\n' + '\n'.join(facts) + '\n\n## Not found\nNone.\n'
        self.assertIn('2 different sites', ' '.join(run.check_sources(output, '', None, ['q'] * 5)))


class WebSearchItemTest(unittest.TestCase):
    def client_replying(self, items):
        client = CodexClient.__new__(CodexClient)  # no app-server process: the replies are scripted
        client.next_id, client.loaded = 0, set()
        client._request = lambda method, params: {'thread': {'id': 't'}}
        client._send = lambda message: None
        replies = iter([{'method': 'item/completed', 'params': {'item': item}} for item in items]
                       + [{'method': 'thread/tokenUsage/updated', 'params': {'threadId': 't', 'tokenUsage': {'total': USAGE}}},
                          {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}])
        client._recv = lambda: next(replies)
        return client

    def test_search_without_query_fails_loudly(self):
        client = self.client_replying([{'type': 'webSearch', 'id': 'w1'}])
        with self.assertRaisesRegex(CodexError, 'webSearch item has no query field'):
            client.run_turn('p', 'm', 'low', '/tmp', thread_id='t')

    def test_search_query_is_returned(self):
        client = self.client_replying([{'type': 'webSearch', 'id': 'w1', 'query': 'ledgerly csv export'}])
        result = client.run_turn('p', 'm', 'low', '/tmp', thread_id='t')
        self.assertEqual(result['searches'], ['ledgerly csv export'])


class VoiceAndAngleTest(unittest.TestCase):
    def test_brief_needs_an_angle(self):
        brief = full_brief('- Target length: 800 words').replace('## Angle\n' + ANGLE_TEXT + '\n\n', '')
        self.assertTrue(any('Angle' in error for error in run.check_brief(brief, task_text(), None)))

    def test_hook_with_a_hedge_word_fails(self):
        def linkedin(first_line):
            return f'## Post\n{first_line}\n\nLink in the first comment.\n#one #two #three\n\n## First comment\n{run.LINK}\n'
        bad = run.check_linkedin(linkedin('YouTube Studio may be enough'), task_text(), None)
        self.assertIn('the first line uses "may"; open on the reader\'s problem instead', bad)
        self.assertEqual(run.check_linkedin(linkedin('You wake up to 300 spam comments'), task_text(), None), [])
        self.assertEqual(run.check_linkedin(linkedin('Maybe you need a filter'), task_text(), None), [])

    def test_x_hook_ignores_the_post_number(self):
        output = f'1/ You wake up to 300 spam comments.\n\n2/ Moderaty may help {run.LINK}'
        errors = run.check_x(output, task_text(), None)
        self.assertEqual([error for error in errors if 'uses' in error], ['the first line uses "may"; open on the reader\'s problem instead'])

    def test_voice_file_needs_every_section_filled(self):
        template = (REPO_TEMPLATE / 'voice.md').read_text()
        self.assertEqual(len(run.check_voice(template)), len(run.VOICE_SECTIONS))
        filled = ''.join(f'## {heading}\nFilled in.\n\n' for heading in run.VOICE_SECTIONS)
        self.assertEqual(run.check_voice(filled), [])

    def test_brand_name_must_not_be_none(self):
        self.assertIn('not None', ' '.join(run.check_brand('None.')))


    def test_brief_step_runs_on_the_strategist(self):
        brief = next(step for step in run.STEPS if step['id'] == '02-brief')
        self.assertEqual(run.ROLES[brief['role']], {'model': 'gpt-6.1-sol', 'effort': 'high'})
        article = next(step for step in run.STEPS if step['id'] == '03-article')
        self.assertEqual(run.ROLES[article['role']], {'model': 'gpt-6.1-sol', 'effort': 'medium'})

    def test_missing_strategist_effort_stops_before_any_turn(self):
        brief = next(step for step in run.STEPS if step['id'] == '02-brief')
        with self.assertRaisesRegex(run.WorkflowError, 'strategist effort high is not supported by gpt-6.1-sol'):
            run.check_models(FakeClient([], models={'gpt-6.1-sol': ['medium']}), run.ROLES, [brief])


class ImageReelInputsTest(unittest.TestCase):
    def test_images_and_reel_read_the_voice_and_angle_first(self):
        for inputs in (run.IMAGE_INPUTS, run.REEL_INPUTS):
            self.assertEqual(inputs[:3], ['design-system', 'voice', 'positioning#Value'])


class WordingTest(unittest.TestCase):
    def test_each_banned_phrase_is_an_error(self):
        for phrase in run.BANNED_PHRASES:
            for variant in (phrase, phrase.upper()):
                with self.subTest(phrase=variant):
                    sentence = f'Studio shows {variant} on every page.'
                    self.assertIn(f'remove "{phrase}" from: "{sentence}"', run.wording_errors(sentence))

    def test_x_not_y_contrast_is_an_error(self):
        sentence = 'Studio holds comments, not replies.'
        self.assertEqual(run.wording_errors(sentence), [f'rewrite this contrast as a direct statement: "{sentence}"'])

    def test_not_x_its_y_contrast_is_an_error(self):
        for sentence in ("Not just a filter, it's a review queue.", "Not just a filter, it\u2019s a review queue."):
            with self.subTest(sentence=sentence):
                self.assertEqual(run.wording_errors(sentence), [f'rewrite this contrast as a direct statement: "{sentence}"'])

    def test_plain_sentence_passes(self):
        self.assertEqual(run.wording_errors('Studio holds comments with links.'), [])

    def test_instagram_caption_with_a_banned_phrase_fails(self):
        output = '## Post\nWe sent the reader the brief first.\nLink in bio\n\n## Hashtags\n#one #two #three\n\n## Image text\nSave hours\n'
        self.assertTrue(any('remove "the brief"' in e for e in run.check_instagram(output, task_text(), None)))


def linkedin_post(post_text):
    return f'## Post\n{post_text}\n\nLink in the first comment.\n#one #two #three\n\n## First comment\n{run.LINK}\n'


class SocialLimitsTest(unittest.TestCase):
    def post_length(self, count):
        return run.linkedin_length(run.sections(linkedin_post('x' * count))['Post'])

    def test_linkedin_length_limit(self):
        at_limit = run.LINKEDIN_LIMIT - self.post_length(1) + 1
        self.assertEqual(self.post_length(at_limit), run.LINKEDIN_LIMIT)
        self.assertFalse(any('characters without hashtags' in e for e in run.check_linkedin(linkedin_post('x' * at_limit), task_text(), None)))
        over = linkedin_post('x' * (at_limit + 1))
        self.assertEqual(self.post_length(at_limit + 1), run.LINKEDIN_LIMIT + 1)
        self.assertIn(f'the Post is {run.LINKEDIN_LIMIT + 1} characters without hashtags; the limit is {run.LINKEDIN_LIMIT}',
                      run.check_linkedin(over, task_text(), None))

    def test_social_banned_meta_words(self):
        for word in run.SOCIAL_BANNED:
            outputs = {
                'check_linkedin': linkedin_post(f'You wake up to spam, {word} the rest.'),
                'check_x': f'1/ You wake up to spam, {word} the rest.',
                'check_instagram': f'## Post\nYou wake up to spam, {word} the rest.\nLink in bio\n\n## Hashtags\n#one #two #three\n\n## Image text\nSave hours\n',
            }
            for name, output in outputs.items():
                with self.subTest(word=word, check=name):
                    errors = getattr(run, name)(output, task_text(), None)
                    self.assertIn(f'the {"Post" if name == "check_linkedin" else "thread" if name == "check_x" else "post"} uses "{word}"; that word is not allowed in a social post',
                                  errors)

    def test_x_max_three_posts(self):
        four = f'1/ One.\n\n2/ Two.\n\n3/ Three.\n\n4/ Four {run.LINK}'
        self.assertIn('the thread has 4 posts; the limit is 3', run.check_x(four, task_text(), None))
        three = f'1/ One.\n\n2/ Two.\n\n3/ Three {run.LINK}'
        self.assertFalse(any('posts;' in e for e in run.check_x(three, task_text(), None)))

    def test_brief_channel_idea_is_200_characters_at_most(self):
        prefix = '- LinkedIn: '
        at_limit = prefix + 'x' * (run.CHANNEL_LIMIT - len(prefix))
        others = '\n- X: ok\n- Instagram: ok'
        ok_brief = full_brief('- Target length: 800 words', channel=at_limit + others)
        self.assertFalse(any('Channel idea is' in e for e in run.check_brief(ok_brief, task_text('article, social'), None)))
        long_brief = full_brief('- Target length: 800 words', channel=at_limit + 'x' + others)
        self.assertIn('the LinkedIn Channel idea is 201 characters; the limit is 200',
                      run.check_brief(long_brief, task_text('article, social'), None))


def article_with(body):
    return META_LINE + '\n# Export guide\n\n' + body + '\n'


class ArticleRulesTest(unittest.TestCase):
    def article_errors(self, body):
        return run.check_article(article_with(body), task_text(), full_brief('- Target length: 6 words'))

    def test_sentence_openings_limit(self):
        four = self.article_errors('If you export, use the button. I\'d compare A. I\'d compare B. I\'d compare C. I\'d compare D.')
        self.assertIn('4 sentences start with "I\'d compare"; vary the openings (limit 3)', four)
        three = self.article_errors('If you export, use the button. I\'d compare A. I\'d compare B. I\'d compare C.')
        self.assertFalse(any('start with' in e for e in three))

    def test_repeated_phrase_is_reported_once(self):
        body = 'If you export, read on.\n\nOne export file holds every invoice. Next. One export file holds every invoice. Last. One export file holds every invoice.'
        self.assertIn('this phrase appears 3 times; say it once: "one export file holds every invoice"', self.article_errors(body))

    def test_repeated_source_links_are_not_phrases(self):
        body = 'If you export, read on.\n\nSee https://support.google.com/youtube/answer/1 for details. Then see https://support.google.com/youtube/answer/1 again. And https://support.google.com/youtube/answer/1 once more.'
        self.assertFalse(any('appears' in e for e in self.article_errors(body)))

    def test_opening_answers_in_three_sentences_with_if_or_when(self):
        self.assertIn('the opening paragraph must answer the question in 3 sentences or fewer',
                      self.article_errors('If you export. Then open it. Then check it. Then send it.'))
        self.assertIn('the opening paragraph must give the conditional answer, with "if" or "when"',
                      self.article_errors('Use the export button. It is quick.'))
        good = self.article_errors('If you export often, use the button. Otherwise, the menu works.')
        self.assertFalse(any('opening paragraph' in e for e in good))

    def test_each_new_banned_phrase_is_an_error(self):
        for phrase in ['in this comparison', 'unassessed', 'the cited', 'cited documentation', 'this article', 'fictional', 'editorial', 'left unassessed']:
            with self.subTest(phrase=phrase):
                self.assertIn(f'remove "{phrase}" from: "Studio is {phrase} today."', run.wording_errors(f'Studio is {phrase} today.'))


class BriefAddsTest(unittest.TestCase):
    def test_every_h2_needs_an_adds_line(self):
        plan = '- Target length: 800 words\n- H2: One\n- H2: Two\n  - Adds: the second thing'
        self.assertIn('H2 "One" needs an "- Adds:" line', run.check_brief(full_brief(plan), task_text(), None))
        good = '- Target length: 800 words\n- H2: One\n  - Adds: the first thing\n- H2: Two\n  - Adds: the second thing'
        self.assertFalse(any('Adds' in e for e in run.check_brief(full_brief(good), task_text(), None)))


class MinorFindingsTest(unittest.TestCase):
    def run_with(self, verdicts):
        campaign = Path(tempfile.mkdtemp()) / 'campaigns' / 'demo'
        campaign.mkdir(parents=True)
        (campaign / '00-task.md').write_text(task_text())
        client = FakeClient(verdicts)
        run.run_campaign(campaign, client, ask=answers('a'), steps=[dict(TEXT_STEPS[0])], skills_dir=run.SKILLS_DIR, roles=ROLES)
        return client

    def test_minor_findings_get_one_rewrite(self):
        client = self.run_with(['minor', 'pass'])
        self.assertEqual([model for model, _ in client.calls], ['w', 'r', 'w', 'r'])  # writer, reviewer, writer, reviewer, then the owner

    def test_hooks_are_left_out_of_the_final_package(self):
        campaign = Path(tempfile.mkdtemp()) / 'campaigns' / 'demo'
        (campaign / '04-linkedin').mkdir(parents=True)
        (campaign / '04-linkedin' / 'output.md').write_text('## Hooks\nOne\nTwo\n\n## Post\nThe post.\n\n## First comment\nComment.\n')
        final = run.build_final(campaign, [{'id': '04-linkedin'}])
        copied = (final / '04-linkedin.md').read_text()
        self.assertNotIn('## Hooks', copied)
        self.assertIn('## Post', copied)

    def test_second_minor_findings_go_to_the_owner(self):
        client = self.run_with(['minor', 'minor'])
        self.assertEqual([model for model, _ in client.calls], ['w', 'r', 'w', 'r'])


class BrandKitTest(unittest.TestCase):
    def test_brief_angle_problem_must_be_in_list(self):
        brief = full_brief('- Target length: 800 words').replace(f'- Reader problem: {PROBLEMS[0]}', '- Reader problem: Something else entirely.')
        self.assertTrue(any('must be one of these lines, quoted exactly' in e for e in run.check_brief(brief, task_text(), None)))
        self.assertFalse(any('Reader problem' in e for e in run.check_brief(full_brief('- Target length: 800 words'), task_text(), None)))

    def test_brief_channel_ideas_use_different_problems(self):
        social = task_text('article, social')
        target = '- Target length: 800 words'
        same = f'- LinkedIn: {PROBLEMS[0]} Relief: x.\n- X: {PROBLEMS[0]} Relief: y.\n- Instagram: {PROBLEMS[2]} Relief: z.'
        self.assertIn('the Channel ideas must use different Value problems', run.check_brief(full_brief(target, same), social, None))
        unlisted = f'- LinkedIn: {PROBLEMS[0]} Relief: x.\n- X: Nothing from the list. Relief: y.\n- Instagram: {PROBLEMS[2]} Relief: z.'
        self.assertTrue(any(e.startswith('the X Channel idea must start with one of these Value lines')
                            for e in run.check_brief(full_brief(target, unlisted), social, None)))

    def test_task_needs_brand(self):
        self.assertTrue(any('Brand' in error for error in run.check_task(task_text(without='Brand'))))

    def test_brand_kit_files_must_be_filled(self):
        self.assertEqual(run.check_brand('demo'), [])
        errors = run.check_brand('unfilled')
        for prefix in ['design-system.md:', 'voice.md:', 'positioning.md:', 'pages.md:']:
            self.assertTrue(any(error.startswith(prefix) for error in errors), prefix)

    def test_positioning_needs_six_problems_from_three_sites(self):
        self.assertEqual(run.check_positioning(POSITIONING_TEXT), [])
        five = POSITIONING_TEXT.replace(PROBLEMS_TEXT, '\n'.join(PROBLEMS_TEXT.splitlines()[:5]))
        self.assertTrue(any('at least 6 lines with a URL; found 5' in e for e in run.check_positioning(five)))
        two = POSITIONING_TEXT.replace('site2.example.com', 'site0.example.com')
        self.assertTrue(any('cite 2 different sites' in e for e in run.check_positioning(two)))

    def test_brand_command_writes_approved_date(self):
        draft = (REPO_TEMPLATE / 'positioning.md').read_text().replace(
            'Template note: what the product is, for whom, and the result, in 25 words or fewer. Include the product URL.',
            'A demo product for creators, at https://example.com/demo.')
        write_brand('kitbrand', **{'positioning.md': draft})
        client = FakeClient(['pass'], writer_outputs=[json.dumps({'output': POSITIONING_TEXT, 'notes': ''})],
                            models={'gpt-6.1-sol': ['medium', 'high'], 'gpt-6-luna': ['xhigh']})
        run.brand_kit(client, 'kitbrand', False, answers('a'))
        saved = (BRANDS_TMP / 'kitbrand' / 'positioning.md').read_text()
        self.assertTrue(saved.startswith(f'Approved: {date.today().isoformat()}\n'))
        self.assertIn('## Reader problems', saved)

    def test_approved_brand_is_not_redrafted_without_refresh(self):
        write_brand('approvedbrand', **{'positioning.md': 'Approved: 2026-11-04\n\n' + POSITIONING_TEXT})
        client = FakeClient([])
        with self.assertRaisesRegex(run.WorkflowError, 'approved on 2026-11-04; run brand approvedbrand --refresh'):
            run.brand_kit(client, 'approvedbrand', False, answers())
        self.assertEqual(client.prompts, [])

    def test_social_inputs_use_positioning_value(self):
        for spec in ['positioning#Reader problems', 'positioning#Value', 'positioning#Claims we never make']:
            self.assertIn(spec, run.SOCIAL_INPUTS)

    def test_article_links_only_to_pages_md(self):
        body = 'If you export, use the button.\n\nRead [the guide](/compare/x/) first. Then [see](/not-listed/).'
        article = META_LINE + '\n# Title\n\n' + body + '\n'
        errors = run.check_article(article, task_text(), full_brief('- Target length: 6 words'))
        self.assertTrue(any('the link /not-listed/ is not in pages.md' in e for e in errors))
        self.assertFalse(any('/compare/x/' in e for e in errors))



class ProcessLinesTest(unittest.TestCase):
    def test_task_rejects_process_lines(self):
        samples = ['Issue MC-15 is tracked here.', 'Due 2026-11-11 for review.', 'Do not start before the pilot.',
                   'Check shared capacity first.', 'Check the prerequisite.', 'Publication requires approval.',
                   'Parent C01 covers it.', 'Accountable lead: Ada.', 'Tracker owner: Ada.', 'Canonical placement: the blog.',
                   'Recheck after launch.', 'Re-check after launch.', 'Due date for the review is Monday.',
                   'Start date is November 4.', 'Earliest start is Monday.', 'Follow the step contract.', 'Research is permitted on this page.']
        for line in samples:
            with self.subTest(line=line):
                errors = run.check_task(task_text().replace('## Constraints\nNone.', f'## Constraints\n{line}'))
                self.assertIn(f'remove this internal process line from the task: "{line}"', errors)

    def test_schedule_line_is_allowed(self):
        task = task_text().replace('## Constraints\nNone.', '## Constraints\nNone.').replace('## Details\nProduct:', '## Details\nSchedule: do not start before 2026-11-11\nProduct:')
        self.assertFalse(any('process line' in e for e in run.check_task(task)))

    def test_sources_and_brief_reject_process_facts(self):
        sources = sources_text().replace('- Fact 0 https://site0', '- Publication requires approval https://site0')
        self.assertIn('this is a process note, not a fact; remove it: "Publication requires approval https://site0.example.com/page0 (checked 2026-11-03)"',
                      run.check_sources(sources, '', None, ['q'] * 5))
        brief = full_brief('- Target length: 800 words').replace('A fact https://example.com', 'Shared capacity https://example.com')
        self.assertTrue(any('this is a process note, not a fact' in e for e in run.check_brief(brief, task_text(), None)))


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.skills = self.tmp / 'skills'
        (self.skills / 'brief').mkdir(parents=True)
        (self.skills / 'brief' / 'SKILL.md').write_text('write brief')
        (self.skills / 'brief' / 'REVIEW.md').write_text('review brief')
        self.campaign = self.tmp / 'campaigns' / 'demo'
        self.campaign.mkdir(parents=True)
        (self.campaign / '00-task.md').write_text(task_text())
        self.step = dict(TEXT_STEPS[0])

    def run_one(self, client, ask):
        return run.run_campaign(self.campaign, client, ask=ask, steps=[dict(self.step)], skills_dir=self.skills, roles=ROLES)

    def test_approved_step_records_fingerprint(self):
        self.run_one(FakeClient(['pass']), answers('a'))
        text = (self.campaign / '01-brief' / 'APPROVED').read_text()
        self.assertIn(f'fingerprint: {run.fingerprint(self.step, self.skills)}', text)

    def test_run_stops_on_stale_approval(self):
        self.run_one(FakeClient(['pass']), answers('a'))
        (self.skills / 'brief' / 'SKILL.md').write_text('changed rules')
        with self.assertRaisesRegex(run.WorkflowError, 'approved under older rules: 01-brief'):
            self.run_one(FakeClient([]), answers())
        with self.assertRaisesRegex(run.WorkflowError, 'rm -r campaigns/demo/01-brief campaigns/demo/final'):
            self.run_one(FakeClient([]), answers())

    def test_approval_without_fingerprint_is_stale(self):
        self.run_one(FakeClient(['pass']), answers('a'))
        (self.campaign / '01-brief' / 'APPROVED').write_text('2026-11-03 10:00\n')
        with self.assertRaisesRegex(run.WorkflowError, 'approved under older rules: 01-brief'):
            self.run_one(FakeClient([]), answers())


def facts_output(lines):
    return '## Facts\n' + '\n'.join(lines) + '\n\n## Not found\nNone.\n'


class SourceFactRulesTest(unittest.TestCase):
    def fact_errors(self, lines):
        return run.check_sources(facts_output(lines), '', None, ['q'] * 5)

    def test_fact_with_two_sentences_fails(self):
        errors = self.fact_errors(['- Ledgerly exports CSV files. It is free https://example.com/export (checked 2026-11-03)'])
        self.assertTrue(any('this fact has more than one sentence' in e for e in errors))

    def test_one_sentence_with_url_and_checked_date_passes(self):
        errors = self.fact_errors(['- Ledgerly exports CSV files https://example.com/v1.2.3/export (checked 2026-11-03)'])
        self.assertFalse(any('more than one sentence' in e for e in errors))

    def test_opinion_words_fail(self):
        for word in ['should', 'therefore', 'can support', 'recommend']:
            with self.subTest(word=word):
                fact = f'Moderaty {word} hold comments https://example.com/help (checked 2026-11-03)'
                errors = self.fact_errors([f'- {fact}'])
                self.assertIn(f'this fact uses "{word}", which is an opinion, not a fact: "{fact}"', errors)

    def test_forum_citations_fail(self):
        for url in ['https://www.reddit.com/r/NewTubers/comments/abc', 'https://forums.example.com/t/1', 'https://example.com/forum/topic']:
            with self.subTest(url=url):
                errors = self.fact_errors([f'- Creators complain about spam {url} (checked 2026-11-03)'])
                self.assertTrue(any('this fact cites a forum' in e for e in errors))

    def test_primary_source_passes(self):
        errors = self.fact_errors(['- Moderaty holds comments for review https://example.com/help/review (checked 2026-11-03)'])
        self.assertFalse(any(word in ' '.join(errors) for word in ['opinion', 'forum', 'more than one sentence']))


MC15_LOOK_STDOUT = ('✗ film.html has 3 problems:\n'
                    '  film.html:9: style is not an attribute of a track — a <section> takes hidden, muted and locked (and id, class, title, data-*, aria-*, which say nothing to the film).\n'
                    '  film.html:10: <iframe src="scenes/card.html#t=0,3">: position would move the clip — a picture is placed by left, top, width, height and rotate in its style.\n'
                    '  film.html:10: <iframe src="scenes/card.html#t=0,3">: display would move the clip — a picture is placed by left, top, width, height and rotate in its style.\n')


class FilmLookTest(unittest.TestCase):
    def setUp(self):
        self.campaign = Path(tempfile.mkdtemp()) / 'campaigns' / 'demo'
        self.campaign.mkdir(parents=True)
        (self.campaign / '00-task.md').write_text(task_text('article, social, images', brand='demo'))
        brief = self.campaign / '02-brief'
        brief.mkdir()
        (brief / 'APPROVED').write_text('done\n')
        (brief / 'output.md').write_text('## Core message\nOne idea.\n\n## Angle\nA reader problem.\n\n## Facts\n- Fact https://example.com (checked 2026-11-03)\n\n## Visual ideas\nA card.\n')
        for step_id in ('04-linkedin', '05-x', '06-instagram'):
            step_dir = self.campaign / step_id
            step_dir.mkdir()
            (step_dir / 'APPROVED').write_text('done\n')
            (step_dir / 'output.md').write_text('Text.\n')
        self.step = next(s for s in run.STEPS if s['id'] == '07-images')

    def projects(self):
        files = [{'path': 'film.html', 'content': '<!doctype html><html><body><section></section></body></html>'},
                 {'path': 'scenes/card.html', 'content': '<!doctype html><html><body>card</body></html>'}]
        return json.dumps({'projects': [{'name': name, 'look_time': 2.8, 'files': files} for name in ['linkedin', 'x', 'instagram']], 'notes': ''})

    def fake_tools(self, look_failures, look_stdout=MC15_LOOK_STDOUT):
        state = {'looks': 0}

        def fake(args, cwd):
            if args[:2] == ['openfilm', 'look']:
                state['looks'] += 1
                if state['looks'] <= look_failures:
                    raise run.ToolFailed(f'openfilm look failed in {cwd} (exit 1):\nstdout:\n{look_stdout}\nstderr:\n', 1, look_stdout, '')
                cache = Path(cwd) / '.film' / 'cache' / 'look'
                cache.mkdir(parents=True, exist_ok=True)
                (cache / 'still.png').write_bytes(b'png')
                return
            if args[0] == 'ffmpeg':
                Path(args[-1]).write_bytes(b'png')
                return
            raise AssertionError(f'unexpected tool: {args}')
        return fake

    def run_images(self, client, look_failures, look_stdout=MC15_LOOK_STDOUT):
        with mock.patch.object(run, 'run_tool', self.fake_tools(look_failures, look_stdout)):
            return run.run_step(client, ROLES, run.SKILLS_DIR, self.campaign, self.step, answers('a'))

    def test_look_problems_become_check_errors(self):
        client = FakeClient(['pass'], writer_outputs=[self.projects(), self.projects()])
        self.run_images(client, look_failures=1)
        self.assertEqual(len(client.prompts), 3)  # writer, one rewrite with the problems, reviewer
        revision = client.prompts[1]
        for problem in ['film.html:9: style is not an attribute of a track',
                        'film.html:10: <iframe src="scenes/card.html#t=0,3">: position would move the clip',
                        'film.html:10: <iframe src="scenes/card.html#t=0,3">: display would move the clip']:
            self.assertIn(f'[blocking] {problem}', revision)

    def test_look_still_failing_after_rewrite_stops(self):
        client = FakeClient([], writer_outputs=[self.projects(), self.projects()])
        with self.assertRaisesRegex(run.WorkflowError, 'film.html has 3 problems') as caught:
            self.run_images(client, look_failures=99)
        self.assertEqual(len(client.prompts), 2)  # no reviewer call after the second failure
        self.assertIn('stderr:', str(caught.exception))

    def test_single_look_problem_becomes_check_error(self):
        client = FakeClient(['pass'], writer_outputs=[self.projects(), self.projects()])
        self.run_images(client, look_failures=1, look_stdout='✗ film.html:10: position would move the clip')
        self.assertEqual(len(client.prompts), 3)  # writer, one rewrite with one problem, reviewer
        self.assertEqual(client.prompts[1].count('[blocking]'), 1)
        self.assertIn('[blocking] film.html:10: position would move the clip', client.prompts[1])

    def test_skills_ban_clip_position(self):
        line = "A clip's style places it only with left, top, width, height and rotate. Never put position, display, inset, right, bottom, margin, transform, translate, scale, z-index, visibility, animation or transition in a clip's style."
        for step in ['images', 'reel']:
            with self.subTest(step=step):
                self.assertIn(line, (run.SKILLS_DIR / step / 'SKILL.md').read_text())

    def test_other_look_failure_stops_at_once(self):
        client = FakeClient([], writer_outputs=[self.projects(), self.projects()])
        with self.assertRaises(run.WorkflowError):
            self.run_images(client, look_failures=99, look_stdout='segmentation fault')
        self.assertEqual(len(client.prompts), 1)

    def test_video_steps_load_openfilm_manual(self):
        images = next(step for step in run.STEPS if step['id'] == '07-images')
        self.assertEqual(images['writer_skills'], ['openfilm-manual', 'quote-cards-and-text-graphics', 'platform-specs-and-validation'])
        self.assertEqual(images['review_skills'], ['openfilm-manual', 'quote-cards-and-text-graphics', 'accessibility-review'])
        self.assertTrue((run.LIBRARY_DIR / 'openfilm-manual' / 'SKILL.md').is_file())

    def test_reel_skill_list(self):
        reel = next(step for step in run.STEPS if step['id'] == '08-reel')
        self.assertEqual(reel['writer_skills'], ['openfilm-manual', 'motion-graphics', 'short-form-video-script', 'hook-writer', 'reels-script', 'instagram-reels-publishing'])
        self.assertEqual(reel['review_skills'], ['openfilm-manual', 'short-form-video-script', 'reels-script', 'accessibility-review'])


class RecheckTest(unittest.TestCase):
    def setUp(self):
        self.campaign = Path(tempfile.mkdtemp()) / 'campaigns' / 'demo'
        self.campaign.mkdir(parents=True)
        (self.campaign / '00-task.md').write_text(task_text('article'))
        sources = self.campaign / '01-sources'
        sources.mkdir()
        (sources / 'APPROVED').write_text('done\n')
        (sources / 'output.md').write_text(sources_text())
        (sources / 'searches.txt').write_text('query\n' * 5)
        self.brief_step = dict(next(step for step in run.STEPS if step['id'] == '02-brief'), role='writer')

    def run_brief(self, client, ask):
        return run.run_campaign(self.campaign, client, ask=ask, steps=[self.brief_step], skills_dir=run.SKILLS_DIR, roles=ROLES)

    def approve_brief(self):
        client = FakeClient(['pass'], writer_outputs=[json.dumps({'output': full_brief('- Target length: 800 words'), 'notes': ''})])
        self.run_brief(client, answers('a'))

    def test_run_rechecks_approved_steps(self):
        self.approve_brief()
        with mock.patch.object(run, 'PROCESS_RE', re.compile(r'\ba fact\b', re.I)):
            with self.assertRaises(run.WorkflowError) as caught:
                self.run_brief(FakeClient([]), answers())
        message = str(caught.exception)
        self.assertIn('these approved steps fail the current rules: 02-brief: '
                      'this is a process note, not a fact; remove it: "A fact https://example.com (checked 2026-11-03)"', message)
        self.assertIn('rm -r campaigns/demo/02-brief campaigns/demo/final', message)

    def test_recheck_passes_unaffected_steps(self):
        self.approve_brief()
        client = FakeClient([])
        with mock.patch.object(run, 'PROCESS_RE', re.compile(r'\bzzz\b')):
            final = self.run_brief(client, answers())
        self.assertTrue((final / 'index.md').is_file())
        self.assertEqual(client.prompts, [])  # nothing was run again, so no Codex call


class ReviewRulesInPromptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.skills = self.tmp / 'skills'
        (self.skills / 'brief').mkdir(parents=True)
        (self.skills / 'brief' / 'SKILL.md').write_text('write brief')
        (self.skills / 'brief' / 'REVIEW.md').write_text('review brief')
        self.campaign = self.tmp / 'campaigns' / 'demo'
        self.campaign.mkdir(parents=True)
        (self.campaign / '00-task.md').write_text(task_text())

    def test_writer_prompt_includes_review_rules(self):
        prompt = run.writer_prompt(self.skills, self.campaign, dict(TEXT_STEPS[0]))
        self.assertIn(run.REVIEW_LINE, prompt)
        self.assertIn('review brief', prompt)
        self.assertLess(prompt.index('write brief'), prompt.index(run.REVIEW_LINE))

    def test_revision_prompt_says_fix_everywhere(self):
        prompt = run.revision_prompt(dict(TEXT_STEPS[0]), {'verdict': 'revise', 'summary': 'Checked.', 'findings': []}, '')
        self.assertIn('Fix each finding everywhere it occurs in the draft', prompt)

    def test_rather_than_is_a_contrast(self):
        sentence = 'Studio holds comments rather than replies.'
        article = META_LINE + '\n# Title\n\nIf you export, use the button.\n\n' + sentence + '\n'
        checks = [
            run.check_article(article, task_text(), full_brief('- Target length: 6 words')),
            run.check_linkedin(linkedin_post(sentence), task_text(), None),
            run.check_x(sentence + ' ' + run.LINK, task_text(), None),
            run.check_instagram(f'## Post\n{sentence}\nLink in bio\n\n## Hashtags\n#one #two #three\n\n## Image text\nSave hours\n', task_text(), None),
        ]
        for errors in checks:
            self.assertIn(f'rewrite this contrast as a direct statement: "{sentence}"', errors)

    def test_notes_skip_leading_blank_lines(self):
        step_dir = Path(tempfile.mkdtemp())
        (step_dir / 'output.md').write_text('Draft.\n')
        review = {'verdict': 'revise', 'summary': 'Checked.', 'findings': []}
        notes = run.decide({'id': '01-x'}, step_dir, review, answers('r', '', 'first note', 'second note', ''))
        self.assertEqual(notes, 'first note\nsecond note')


CHARACTER = {'name': 'Ada', 'avatar_id': 'avatar-1', 'voice_id': 'voice-1', 'disclosure': 'The presenter is an AI avatar.'}
SCRIPT_LINES = ['First line.', 'Second line.', 'Third line.']


class FakeHeyGen:
    SRT = '1\n00:00:01,200 --> 00:00:03,400\nHello, there.\n\n2\n00:00:03,500 --> 00:00:05,000\nBye.\n'

    def __init__(self, durations=(2.0, 2.0, 2.0, 2.0), failing=None, missing_subtitle=None):
        self.durations, self.failing, self.missing_subtitle = list(durations), failing, missing_subtitle
        self.created, self.videos = [], {}

    def request(self, method, path, body=None, query=None):
        if method == 'POST' and path == '/v3/videos':
            self.created.append(body)
            video_id = f'v{len(self.created)}'
            duration = self.durations[len(self.created) - 1] if len(self.created) <= len(self.durations) else 2.0
            self.videos[video_id] = (body['script'], duration)
            return {'data': {'video_id': video_id, 'status': 'waiting'}}
        if method == 'GET' and path.startswith('/v3/videos/'):
            video_id = path.rsplit('/', 1)[1]
            text, duration = self.videos[video_id]
            if text == self.failing:
                return {'data': {'video_id': video_id, 'status': 'failed', 'failure_message': 'avatar unavailable'}}
            subtitle = None if text == self.missing_subtitle else f'https://cdn.example/{video_id}.srt'
            return {'data': {'video_id': video_id, 'status': 'completed', 'video_url': f'https://cdn.example/{video_id}.mp4',
                             'duration': duration, 'subtitle_url': subtitle}}
        raise AssertionError((method, path))

    def download(self, url):
        return b'mp4 bytes' if url.endswith('.mp4') else self.SRT.encode()


class HeyGenReelTest(unittest.TestCase):
    def make_campaign(self, lines=SCRIPT_LINES, brand='demo'):
        campaign = Path(tempfile.mkdtemp()) / 'campaigns' / 'demo'
        (campaign / '08-reel-script').mkdir(parents=True)
        (campaign / '08-reel-script' / 'script.json').write_text(json.dumps(
            {'presenter': 'Ada', 'lines': [{'id': f'p{n}', 'text': text} for n, text in enumerate(lines, 1)], 'notes': ''}))
        (campaign / '00-task.md').write_text(task_text('article, social, images, reel', brand=brand))
        step_dir = campaign / '08-reel'
        step_dir.mkdir()
        return campaign, step_dir

    def heygen(self, fake):
        return mock.patch.multiple(run, heygen_request=fake.request, heygen_download=fake.download, HEYGEN_POLL_SECONDS=0)

    def test_reel_needs_character_and_key(self):
        write_brand('nochar')
        (BRANDS_TMP / 'nochar' / 'character.md').unlink()
        write_brand('noavatar', **{'character.md': CHARACTER_TEXT.replace('## Avatar id\navatar-1\n', '## Avatar id\n\n')})
        script = dict(next(step for step in run.STEPS if step['id'] == '08-reel-script'), inputs=[], review_inputs=[])
        reel = dict(next(step for step in run.STEPS if step['id'] == '08-reel'), inputs=[], review_inputs=[])
        cases = [('nochar', 'nochar/character.md is missing'),
                 ('noavatar', 'character.md: section "## Avatar id" is missing or empty'),
                 ('demo', 'HEYGEN_API_KEY is not set')]
        for brand, expected in cases:
            with self.subTest(brand=brand):
                campaign, _ = self.make_campaign(brand=brand)
                env = {'HEYGEN_API_KEY': 'test-key'} if brand != 'demo' else {}
                with mock.patch.dict(os.environ, env, clear=False):
                    os.environ.pop('HEYGEN_API_KEY', None) if brand == 'demo' else None
                    with self.assertRaisesRegex(run.WorkflowError, expected):
                        run.run_campaign(campaign, FakeClient([]), ask=answers(), steps=[script, reel], skills_dir=run.SKILLS_DIR, roles=ROLES)

    def test_script_checks_line_length_and_total(self):
        def script(*lines):
            return '# Reel script\n\nPresenter: Ada\n\n' + '\n'.join(f'- p{n}: {text}' for n, text in enumerate(lines, 1)) + '\n\n## Notes\n\nNone.\n'
        long_line = script(' '.join(['word'] * 26))
        self.assertIn('line p1 has 26 words; the limit is 25', run.check_reel_script(long_line, '', None))
        twenty = ' '.join(['word'] * 20)
        self.assertIn('the script has 80 words; the limit for a 30 s reel is 75', run.check_reel_script(script(twenty, twenty, twenty, twenty), '', None))
        self.assertEqual(run.check_reel_script(script(' '.join(['word'] * 25), twenty, twenty), '', None), [])

    def test_no_render_before_script_approval(self):
        campaign, _ = self.make_campaign()
        script_text = json.dumps({'presenter': 'Ada', 'lines': [{'id': 'p1', 'text': 'First line.'}], 'notes': ''})
        script = dict(next(step for step in run.STEPS if step['id'] == '08-reel-script'), inputs=[], review_inputs=[])
        reel = dict(next(step for step in run.STEPS if step['id'] == '08-reel'), inputs=[], review_inputs=[])
        fake = FakeHeyGen()
        with mock.patch.dict(os.environ, {'HEYGEN_API_KEY': 'test-key'}), mock.patch.object(run, 'render_presenter') as render, self.heygen(fake):
            with self.assertRaises(SystemExit):
                run.run_campaign(campaign, FakeClient(['pass'], writer_outputs=[script_text]), ask=answers('q'),
                                 steps=[script, reel], skills_dir=run.SKILLS_DIR, roles=ROLES)
        self.assertEqual(render.call_count, 0)
        self.assertEqual(fake.created, [])

    def test_render_cache_skips_unchanged_lines(self):
        campaign, step_dir = self.make_campaign()
        fake = FakeHeyGen()
        with self.heygen(fake):
            run.render_presenter(campaign, step_dir, CHARACTER)
            self.assertEqual(len(fake.created), 3)
            run.render_presenter(campaign, step_dir, CHARACTER)
        self.assertEqual(len(fake.created), 3)  # the second pass reuses every render

    def test_presenter_at_values_are_cumulative(self):
        campaign, step_dir = self.make_campaign()
        with self.heygen(FakeHeyGen(durations=(3.0, 4.5, 2.0))):
            run.render_presenter(campaign, step_dir, CHARACTER)
        presenter = json.loads((step_dir / 'presenter.json').read_text())
        self.assertEqual([clip['at'] for clip in presenter], [0.0, 3.0, 7.5])
        self.assertEqual([clip['id'] for clip in presenter], ['p1', 'p2', 'p3'])
        self.assertTrue((step_dir / 'assets' / 'p2.mp4').is_file())
        self.assertTrue((step_dir / 'assets' / 'p2.vtt').is_file())

    def test_failed_render_stops_with_response(self):
        campaign, step_dir = self.make_campaign()
        with self.heygen(FakeHeyGen(failing='Second line.')):
            with self.assertRaisesRegex(run.HeyGenError, 'HeyGen render of p2 failed') as caught:
                run.render_presenter(campaign, step_dir, CHARACTER)
        self.assertIn('avatar unavailable', str(caught.exception))

    def test_missing_subtitle_names_the_clip(self):
        campaign, step_dir = self.make_campaign()
        with self.heygen(FakeHeyGen(missing_subtitle='Second line.')):
            with self.assertRaisesRegex(run.WorkflowError, 'clip p2'):
                run.render_presenter(campaign, step_dir, CHARACTER)

    def test_srt_becomes_webvtt_with_dots(self):
        vtt = run.srt_to_vtt(FakeHeyGen.SRT)
        self.assertTrue(vtt.startswith('WEBVTT\n\n'))
        self.assertIn('00:00:01.200 --> 00:00:03.400\nHello, there.', vtt)
        self.assertNotIn(',', vtt.split('\n')[2])
        self.assertNotIn('\n1\n', vtt)

    def test_presenter_on_screen_in_first_two_seconds(self):
        presenter = [{'id': 'p1', 'at': 0.0}]
        late = [{'path': 'film.html', 'content': '<video src="assets/p1.mp4" at="3">'}]
        self.assertTrue(any('must be on screen in the first 2 seconds' in e for e in run.presenter_film_errors(late, presenter)))
        early = [{'path': 'film.html', 'content': '<video src="assets/p1.mp4" at="0">'}]
        self.assertEqual(run.presenter_film_errors(early, presenter), [])

    def film_tool(self):
        def fake(args, cwd):
            if args[:2] == ['openfilm', 'render']:
                Path(args[args.index('--out') + 1]).write_bytes(b'mp4')
                return
            if args[0] == 'ffmpeg':
                target = Path(args[-1])
                if target.name.endswith('%03d.png'):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    for number in range(1, 4):
                        (target.parent / f'{number:03d}.png').write_bytes(b'png')
                    return
                target.write_bytes(b'png')
                return
            raise AssertionError(f'unexpected tool: {args}')
        return fake

    def film(self, at_values):
        clips = ''.join(f'<video src="assets/p{n}.mp4" at="{at}"></video>' for n, at in enumerate(at_values, 1))
        html = f'<!doctype html><html><head><meta name="viewport" content="width=1080, height=1920"></head><body><section>{clips}</section></body></html>'
        return json.dumps({'files': [{'path': 'film.html', 'content': html}], 'notes': 'Film notes.'})

    def reel_step(self):
        return dict(next(step for step in run.STEPS if step['id'] == '08-reel'), inputs=[], review_inputs=[])

    def test_reel_film_flow_reaches_the_owner(self):
        campaign, step_dir = self.make_campaign()
        fake = FakeHeyGen(durations=(2.0, 2.0, 2.0))
        client = FakeClient(['pass'], writer_outputs=[self.film((0.0, 2.0, 4.0))])
        with self.heygen(fake), mock.patch.object(run, 'run_tool', self.film_tool()):
            run.run_step(client, ROLES, run.SKILLS_DIR, campaign, self.reel_step(), answers('a'))
        self.assertTrue((step_dir / 'APPROVED').is_file())
        self.assertTrue((step_dir / 'reel.mp4').is_file())
        self.assertIn('The presenter is an AI avatar.', (step_dir / 'output.md').read_text())

    def test_film_with_the_wrong_presenter_time_goes_back_once(self):
        campaign, step_dir = self.make_campaign()
        fake = FakeHeyGen(durations=(2.0, 2.0, 2.0))
        client = FakeClient(['pass'], writer_outputs=[self.film((0.0, 3.0, 4.0)), self.film((0.0, 2.0, 4.0))])
        with self.heygen(fake), mock.patch.object(run, 'run_tool', self.film_tool()):
            run.run_step(client, ROLES, run.SKILLS_DIR, campaign, self.reel_step(), answers('a'))
        self.assertEqual(len(client.prompts), 3)  # writer, one rewrite with the timing problem, reviewer
        self.assertIn('[blocking] assets/p2.mp4 must start at 2.0 s', client.prompts[1])
        self.assertEqual(len(fake.created), 3)  # the rewrite reused every render

    def test_final_package_has_the_ai_label_line(self):
        campaign = Path(tempfile.mkdtemp()) / 'campaigns' / 'demo'
        (campaign / '08-reel').mkdir(parents=True)
        (campaign / '08-reel' / 'output.md').write_text('# Reel\n')
        final = run.build_final(campaign, [{'id': '08-reel'}])
        self.assertIn('Before posting the reel, turn on "Label as made with AI" in Instagram.', (final / 'index.md').read_text())

    def test_heygen_lists_page_through_items(self):
        pages = [{'data': [{'id': 'a'}], 'has_more': True, 'next_token': 't1'}, {'data': [{'id': 'b'}], 'has_more': False}]
        with mock.patch.object(run, 'heygen_request', side_effect=pages) as request:
            items = run.heygen_list('/v3/avatars/looks', {'limit': 50})
        self.assertEqual([item['id'] for item in items], ['a', 'b'])
        self.assertEqual(request.call_args_list[1].kwargs['query'], {'limit': 50, 'token': 't1'})


class SearchLogTest(unittest.TestCase):
    def test_each_web_search_is_logged_and_returned(self):
        client = FakeClient([], searches=['ledgerly csv export', 'instagram alt text rules'])
        tmp = Path(tempfile.mkdtemp())
        with self.assertLogs('marketing', level='INFO') as logs:
            _, _, searches = run.call_codex(client, {'model': 'm', 'effort': 'low'}, 'prompt', tmp, None, tmp / 'writer.json', 'writer', web=True)
        self.assertEqual(searches, ['ledgerly csv export', 'instagram alt text rules'])
        self.assertIn('writer: web search: ledgerly csv export', '\n'.join(logs.output))


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
    def test_design_system_is_checked_for_images(self):
        self.assertEqual(run.check_task(task_text('article, social, images')), [])
        write_brand('nodesign', **{'design-system.md': 'None.\n'})
        self.assertTrue(run.check_task(task_text('article, social, images', brand='nodesign')))

    def test_design_system_is_not_checked_without_images_or_reel(self):
        self.assertEqual(run.check_task(task_text('article, social', brand='nosuchbrand')), [])

    def test_brand_folder_must_exist(self):
        errors = run.check_task(task_text('article, social, images', brand='nosuchbrand'))
        self.assertTrue(any('brand folder not found' in error for error in errors))

    def test_check_design_system(self):
        template = (REPO_TEMPLATE / 'design-system.md').read_text()
        self.assertEqual(run.check_design_system(template), [])
        self.assertTrue(run.check_design_system(template.replace('- accent: #3366FF\n', '')))

    def test_images_get_the_design_system_first(self):
        campaign = Path(tempfile.mkdtemp()) / 'campaigns' / 'demo'
        campaign.mkdir(parents=True)
        (campaign / '00-task.md').write_text(task_text('article, social, images', brand='demo'))
        brief = campaign / '02-brief'
        brief.mkdir()
        (brief / 'APPROVED').write_text('done\n')
        (brief / 'output.md').write_text('## Core message\nOne idea.\n\n## Angle\nA reader problem.\n\n## Facts\n- Fact https://example.com (checked 2026-11-03)\n\n## Visual ideas\nA card.\n')
        for step_id in ('04-linkedin', '05-x', '06-instagram'):
            step_dir = campaign / step_id
            step_dir.mkdir()
            (step_dir / 'APPROVED').write_text('done\n')
            (step_dir / 'output.md').write_text('Text.\n')
        step = next(s for s in run.STEPS if s['id'] == '07-images')
        prompt = run.writer_prompt(run.SKILLS_DIR, campaign, step)
        self.assertLess(prompt.index('--- BEGIN design-system ---'), prompt.index('--- BEGIN voice ---'))


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
        for step_id, text in (('02-brief', '## Angle\nA reader problem.\n## Keywords\nkeyword\n## Reader problems\n- A problem https://example.com (checked 2026-11-04)\n## Facts\n- A fact https://example.com (checked 2026-11-03)\n## Channel ideas\nIdea.\n'),
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
        self.assertTrue(any('needs one line' in e for e in run.check_brief(full_brief('- Content type: guide.'), task_text(), None)))
        self.assertTrue(any('needs one line' in e for e in run.check_brief(full_brief('- Target length: 800 words\n- Target length: 900 words'), task_text(), None)))
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
        all_three = f'- LinkedIn: {PROBLEMS[0]} Relief: one idea.\n- X: {PROBLEMS[1]} Relief: one idea.\n- Instagram: {PROBLEMS[2]} Relief: one idea.'
        self.assertEqual(run.check_brief(full_brief(target, all_three), social, None), [])
        missing = f'- LinkedIn: {PROBLEMS[0]} Relief: one idea.\n- X: {PROBLEMS[1]} Relief: one idea.'
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
        sources = json.dumps({'output': sources_text(), 'notes': ''})
        client = FakeClient(['pass'], writer_outputs=[sources], searches=['q'] * 5)
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


class ImportFilterTest(unittest.TestCase):
    def test_recheck_after_lines_stop_the_import(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / 'skills' / 'import').mkdir(parents=True)
        (tmp / 'skills' / 'import' / 'SKILL.md').write_text('import the issue')
        campaign = tmp / 'campaigns' / 'mc-32'
        campaign.mkdir(parents=True)
        output = task_text() + 'Recheck after: 2027-01-05\n'
        client = FakeClient([], writer_text=json.dumps({'output': output, 'notes': ''}))
        with self.assertRaisesRegex(run.WorkflowError, '"recheck after" lines are not allowed; the workflow researches when it runs'):
            run.import_issue(client, campaign, 'MC-32', skills_dir=tmp / 'skills', roles=ROLES)
        self.assertFalse((campaign / '00-task.md').exists())


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
