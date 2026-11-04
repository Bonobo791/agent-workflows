# Marketing workflow

A brand's positioning, voice, design system and pages are prepared once. Then each task becomes a sourced brief and an SEO article, and the social posts, images and reel that its Deliverables ask for.
Every step is written by Codex, checked by Python, reviewed by a separate Codex thread, and then waits for your approval.
Nothing is published. The approved files end up in `campaigns/<name>/final/`.

## Requirements

- Python 3.10 or newer
- `codex` 0.166.0 or newer, installed and signed in
- For `--issue`: the It's a Plan connection in Codex
- `openfilm` 0.2.6 and `ffmpeg` with libx264, on `PATH` (needed from step 07)

## Steps

0. The brand kit, once per brand: `brand <brand>` drafts `positioning.md` from its One line with one web-search pass. Every campaign checks the brand kit before any Codex call.
1. `01-sources`: finds the facts the campaign needs beyond the brand kit, from primary sources only: at least 3 searches, and at least 3 facts with URLs from 3 different sites
2. `02-brief`: the brief. Its Angle is one reader problem from `positioning.md`, and each Channel idea starts with a different Value line from it
3. `03-article`: the SEO article. Each H2 delivers only its Adds line from the brief. It links only to the pages listed in `pages.md`
4. `04-linkedin`, `05-x`, `06-instagram`: the social posts (if Deliverables has `social`). Each draft has a `## Hooks` section with three hooks from three different reader problems. The checks and the final package leave `## Hooks` out
5. `07-images`: three still images (if Deliverables has `images`)
6. `08-reel-script` and `08-reel`: a 30-second vertical video with a HeyGen presenter (if Deliverables has `reel`). You approve the script first; only then are the presenter clips rendered and the film made

`article` is always made. `images` and `reel` need `social` in Deliverables.

## Use

```sh
python3 marketing/run.py brand moderaty                  # once per brand: draft positioning.md (see Brand kit)
python3 marketing/run.py new mc-32 --issue MC-32         # create a campaign from an It's a Plan issue
python3 marketing/run.py new spring-launch my-task.md    # or from a task file
python3 marketing/run.py run mc-32                       # run; stops at every step for your decision
python3 marketing/run.py status mc-32                    # where it is, and the tokens each step used
```

At each step you see the output and the review, then type:

- `a` to approve and continue
- `r` to reject and type what should change
- `q` to quit; run the same command later to come back to the same step

A step that fails the automatic checks goes back to the writer without a reviewer call. If the reviewer says revise, or a pass has minor findings, the writer rewrites automatically once, then the reviewer checks again before the step comes to you. A failed check uses the same one rewrite.

## What the checks enforce

Python checks each draft before a reviewer sees it. A failed check goes back to the writer:

- Structure and length: the required sections and headings, the article's length against the brief's target, a meta description of 150 to 160 characters, an opening paragraph of three sentences or fewer with "if" or "when", a thread of at most three X posts, a LinkedIn post of 1,000 characters or fewer, and an Instagram caption that ends "Link in bio".
- Wording: banned phrases such as "the brief", "supplied facts", "based on the facts" and "fictional"; "X, not Y" and "not X, it's Y" contrasts; hedge words in a first line; more than three sentences that start with the same two words; a six-word phrase used three times.
- Sources and brand: at least 3 searches and 3 URL facts from 3 sites, and every fact with a URL or "(from task)". Links only to pages in `pages.md`.

The reviewers judge what Python cannot: whether each fact is on its page, whether the hook names a real reader problem, whether a post has one idea and a position, and whether the copy sounds like the brand's voice.

## Task file

Every task has these sections, in this order. Each one must be present and not empty; `None.` is allowed.

```
# Task: <title>

## Deliverables
article, social, images, reel

## Goal
<what the campaign must achieve>

## Audience
<who it is for>

## Details
<product, feature, dates, the main page>

## Sources
<links or notes to start from, or None.>

## Required headings
<None., or one "- " line per heading, in order>

## Constraints
<rules the content must follow, or None.>

## Brand
<a folder name in marketing/brands/, such as moderaty>
```

Deliverables is a comma-separated list of `article`, `social`, `images` and `reel`. See `marketing/examples/sample-task.md`.

`--issue` writes this file from the It's a Plan issue, including the required headings from the SOP that the issue links to. Check the file before you run the campaign.

## Brand kit

Each brand has a folder, `marketing/brands/<brand>/`, with five files. Every campaign reads the first four; `character.md` is read only for reels:

- `design-system.md`: colours, fonts and layout for images and reel.
- `voice.md`: how the brand sounds.
- `positioning.md`: what the product sells, to whom, the reader problems, the proof and the alternatives.
- `pages.md`: the pages the article may link to.
- `character.md`: the reel presenter's name, personality, HeyGen avatar and voice ids, what they do and don't do on camera, and the disclosure line. Fill the ids in with `python3 marketing/run.py heygen avatars` and `python3 marketing/run.py heygen voices`, which need `HEYGEN_API_KEY`.

Make a brand by copying `marketing/brands/TEMPLATE/` to `marketing/brands/<brand>/` and filling in every file. `character.md` is needed only for reels. Replace each `Template note:` line with your text. A run stops before any Codex call if a file is missing or still has a template note.

Write `## One line` in `positioning.md` first: what the product is, for whom, the result, and the product URL. Then run:

```sh
python3 marketing/run.py brand <brand>            # one web-search pass drafts the rest of positioning.md
python3 marketing/run.py brand <brand> --refresh  # research again, and show the differences from the approved file
```

The draft goes to the a/r/q prompt. On approval, `positioning.md` is saved with `Approved: <date>` at the top. A campaign warns when that date is more than 90 days old.

## Models

Each step uses one of four roles. The roles are set in `ROLES` at the top of `marketing/run.py`.

| Role | Used for | Model | Effort |
| --- | --- | --- | --- |
| writer | steps 01, 03 to 06, and the import | `gpt-6.1-sol` | `medium` |
| strategist | the brief (step 02) | `gpt-6.1-sol` | `high` |
| reviewer | the review of every step | `gpt-6.1-sol` | `high` |
| creator | images and reel | `gpt-6.1-sol` | `high` |

Before any step runs, the program checks that each model and effort is in your Codex model list. If one is not, it stops with an `error:` message.

## Files of a campaign

```
campaigns/<name>/00-task.md
campaigns/<name>/import.json                         (only with --issue)
campaigns/<name>/codex-stderr.log                    Codex's stderr for every run and import
campaigns/<name>/run.log                             every step, Codex call and decision (also printed in the terminal)
campaigns/<name>/01-sources/output.md, review.md, APPROVED, usage.jsonl, writer-thread.txt, history/
campaigns/<name>/02-brief/...
...
campaigns/<name>/final/index.md
```

Each step folder has `usage.jsonl` (the tokens of each Codex call) and `writer-thread.txt` (the writer's Codex thread, which a revision continues). `status` adds the tokens up.

To follow a run from another terminal, run `tail -f campaigns/<name>/run.log`.

The brand command keeps its own folder, `campaigns/brand-<brand>/`, with the same files.

A campaign made before the brand kit has no `## Brand` section, so add one and its brand folder before running it.

## Change the steps or the skills

- Skills: `marketing/skills/<step>/SKILL.md` (writer) and `REVIEW.md` (reviewer). Replace them with your own. `marketing/skill-library/` holds full copies of the plugin skills that the writers and reviewers also read, each with its provenance line.
- Brand kit: the templates are in `marketing/brands/TEMPLATE/`. Each brand's folder is read by every campaign that names it in its `## Brand` section.
- Steps, their order, what each one needs and its automatic checks: the `STEPS` list and the `CHECKS` table in `marketing/run.py`.

## Engineering workflow

Builds a change in a git repository, step by step. You write a task file. The workflow plans it, writes phased instructions, builds them one phase at a time with the check commands run after every phase, reviews the code, and opens a **draft** pull request.
Every step is written by Claude Code, checked by Python, reviewed by a second session, and then waits for your approval. Nothing is merged, released or deployed.

```
task file -> 01-plan -> 02-instructions -> 03-build -> 04-pr -> draft pull request
```

### Requirements

- Python 3.10 or newer
- Claude Code 2.1.318 or newer, installed and signed in
- `git`
- `gh`, signed in with `gh auth login`. It is used only to open the draft pull request.

### Task file

Start from `engineering/examples/sample-task.md`. Its sections, in this order:

- `## Repository`: the absolute path of a local git checkout
- `## Base branch`: an existing branch, for example `main`
- `## Branch`: the new branch the build creates
- `## Goal`, `## Details`, `## Done when` (one `- ` line per criterion)
- `## Check commands`: one `- ` line per command. Python runs them from the repository root after every phase.
- `## Constraints`: the rules the change must follow, or `None.`

The target repository must ignore the files its check commands create (for example `__pycache__/` in its `.gitignore`), because step 03 stops on any uncommitted or untracked file.

### Use

```sh
python3 engineering/run.py new status-cost engineering/examples/sample-task.md
python3 engineering/run.py run status-cost     # runs; stops at every step for your decision
python3 engineering/run.py status status-cost  # where the build is, and its token usage
```

At each step: `a` approves, `r` rejects with notes, `q` quits. Run the same `run` command later to come back to the same decision.

### Files of a build

`builds/<name>/` (gitignored):

- `00-task.md`: the task. `base.txt`: the base commit. `sessions.json`: the ids of the three sessions.
- `worktree/`: a git worktree of the target repository, on the new branch. Every agent works here.
- `run.log` (the log) and `claude-stderr.log`.
- `01-plan/`, `02-instructions/`, `03-build/`, `04-pr/`: `output.md`, `review.md`, `calls/` (each prompt and reply), `usage.jsonl`, `history/` (earlier versions), and `APPROVED` once you approve.
- `02-instructions/phases.json`, `03-build/phase-N.done`, `03-build/checks-N.txt` (the output of each check run).

### Sessions and caching

Each build has three Claude Code sessions: `planner` (steps 01 and 02), `builder` (phases, fixes and the pull request) and `reviewer` (every review). Each one is resumed with `--resume`, so it keeps the files it has read. A session's calls all use the same flags and the same JSON schema, and the stable part of each prompt comes first, so the cache is reused.

`status` shows, for each step and each session: the non-cached input, the cache reads and their share of the input, the output and the cost. It also shows the size of the context on each session's last call, so you can see a session grow.

Models, effort and the budget per call are in `ROLES` in `engineering/run.py`. Defaults: planner Opus (high), builder Haiku (high), reviewer Opus (medium); budgets $5, $5 and $3 per call.

Each agent may run only the Bash commands its rules list, one command per call. A refused call is listed at the owner gate of its step.

The only outward action is `git push` plus a draft pull request, and it runs only after you approve step 04.

## Tests

```sh
python3 -m unittest discover -s tests
```

The old runner, including the old engineering runner under runner/, is kept under the Git tag legacy-2026-11. engineering/run.py replaces it; the tag stays as it is.
