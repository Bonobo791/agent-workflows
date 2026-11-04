# Marketing workflow

One task becomes a sourced brief and an SEO article, then the social posts, images and reel that its Deliverables ask for.
Every step is written by Codex, checked by Python, reviewed by a separate Codex thread, and then waits for your approval.
Nothing is published unless the task has `## Publish to` and you approve step 09 (see "Posting after approval"). The approved files end up in `campaigns/<name>/final/`.

## Requirements

- Python 3.10 or newer
- `codex` 0.166.0 or newer, installed and signed in
- For `--issue`: the It's a Plan connection in Codex
- `openfilm` 0.2.6 and `ffmpeg` with libx264, on `PATH` (needed from step 07)

## Steps

1. `01-sources`: finds the facts on the web, from primary sources only
2. `02-brief`: the brief, with the facts copied from step 01
3. `03-article`: the SEO article
4. `04-linkedin`, `05-x`, `06-instagram`: the social posts (if Deliverables has `social`)
5. `07-images`: three still images (if Deliverables has `images`)
6. `08-reel`: a vertical video of 15 to 30 seconds (if Deliverables has `reel`)
7. `09-publish`: shows every post exactly as it will go out, and queues them when you approve (only if the task has `## Publish to`)

`article` is always made. `images` and `reel` need `social` in Deliverables.

## Use

```sh
python3 marketing/run.py new mc-32 --issue MC-32         # create a campaign from an It's a Plan issue
python3 marketing/run.py new spring-launch my-task.md    # or from a task file
python3 marketing/run.py run mc-32                       # run; stops at every step for your decision
python3 marketing/run.py status mc-32                    # where it is, and the tokens each step used
```

At each step you see the output and the review, then type:

- `a` to approve and continue
- `r` to reject and type what should change
- `q` to quit; run the same command later to come back to the same step

A step that fails the automatic checks goes back to the writer without a reviewer call. If the reviewer says revise, the writer rewrites automatically, up to 2 times, before the step comes to you.

## Task file

Every task has these sections, in this order. Each one must be present and not empty; `None.` is allowed. `## Design system` is needed only when Deliverables include images or reel.

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

## Design system
<a file name from marketing/design-systems without .md, such as moderaty, or None. when Deliverables have no images or reel>
```

Make a new design system by copying `marketing/design-systems/TEMPLATE.md`.

Deliverables is a comma-separated list of `article`, `social`, `images` and `reel`. See `marketing/examples/sample-task.md`.

`--issue` writes this file from the It's a Plan issue, including the required headings from the SOP that the issue links to. Check the file before you run the campaign.

## Models

Each step uses one of three roles. The roles are set in `ROLES` at the top of `marketing/run.py`.

| Role | Used for | Model | Effort |
| --- | --- | --- | --- |
| writer | steps 01 to 06, and the import | `gpt-6-luna` | `max` |
| reviewer | the review of every step | `gpt-6-sol` | `medium` |
| creator | images and reel | `gpt-6-sol` | `high` |

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
campaigns/<name>/09-publish/plan.json, APPROVED, publish.jsonl, media/   (only with ## Publish to)
```

Each step folder has `usage.jsonl` (the tokens of each Codex call) and `writer-thread.txt` (the writer's Codex thread, which a revision continues). `status` adds the tokens up.

To follow a run from another terminal, run `tail -f campaigns/<name>/run.log`.

Campaigns made before the sources step was added use the old folder numbers and cannot be resumed. Start them again.

## Change the steps or the skills

- Skills: `marketing/skills/<step>/SKILL.md` (writer) and `REVIEW.md` (reviewer). Replace them with your own.
- Steps, their order, what each one needs and its automatic checks: the `STEPS` list and the `CHECKS` table in `marketing/run.py`.

## Posting after approval

A task with `## Publish to` gets one more step, `09-publish`. Without that section nothing changes: the campaign stops at `final/`.

When every content step is approved, step 09 shows every post exactly as it will go out. Type `a` to queue them, or `q` to quit. A timer then sends the queued posts through each platform's own API, retries the failures, and writes every attempt to `campaigns/<name>/09-publish/publish.jsonl`.

### The task sections

```
## Publish to
linkedin, x, instagram, youtube

## Article URL
https://moderaty.com/blog/demo-article

## Schedule
- linkedin: 2026-11-10T09:30Z
- x: 2026-11-10T14:00Z
```

- `## Publish to` names the platforms to post on: `linkedin`, `x`, `instagram` or `youtube`.
- `## Article URL` is the live `https://` address of the article. You publish the article on your website yourself and write its address here. Every post gets this link, and the timer checks that it answers before any post goes out.
- `## Schedule` is optional, one line per platform, in UTC. A platform without a line goes out on the first timer run after approval.

### What each platform posts

| Platform | Needs in Deliverables | Text | Media |
| --- | --- | --- | --- |
| `linkedin` | `social` | the Post, then a first comment with the article link | `linkedin.png` if `images` |
| `x` | `social` | the thread, one post per `N/` part; each post replies to the one before | `x.png` on the first post, if `images` |
| `instagram` | `social` and `images` | the caption, a blank line, then the hashtags | `instagram.png`, converted to JPEG |
| `youtube` | `reel` | the article title; the meta description, a blank line, the article link | `reel.mp4`, uploaded as a Short |

TikTok, Instagram Reels and publishing the article to the website are not built yet.

### The config file

The tokens go in `~/.config/agent-workflows/publish.json`, outside the repository. Make it readable by you only:

```sh
chmod 600 ~/.config/agent-workflows/publish.json
```

```json
{
  "linkedin": {"organization_id": "<page id, digits only>", "access_token": "<token>", "version": "<YYYYMM>"},
  "x": {"username": "<your X handle>", "api_key": "<key>", "api_secret": "<secret>",
        "access_token": "<token>", "access_token_secret": "<secret>"},
  "instagram": {"user_id": "<Instagram user id>", "access_token": "<token>", "api_version": "v26.0",
                "media_dir": "<folder your web server serves publicly>", "media_base_url": "https://<your domain>/<folder>"},
  "youtube": {"client_id": "<id>", "client_secret": "<secret>", "refresh_token": "<token>", "category_id": "28"}
}
```

If any other user can read the file, every publish run stops. The program never logs in: you paste the tokens in. When a token expires, that platform's posts stop; renew the token, then retry the item (see below).

### Set up each platform once

- **LinkedIn**: an app with the Community Management API and the `w_organization_social` permission, and an access token for the page.
- **X**: a developer app with read and write access, pay-per-use credits, and the OAuth 1.0a access token and secret of your account.
- **Instagram**: a professional account, Business Login for Instagram with `instagram_business_content_publish`, and a public web folder on your own server for the images. Instagram fetches each image from `media_base_url`, so the folder must be reachable from the internet.
- **YouTube**: a Google Cloud project with the YouTube Data API, the `youtube.upload` scope, and a refresh token for your channel.

### Run it from cron

```
*/5 * * * * cd /path/to/agent-workflows && /usr/bin/python3 marketing/run.py publish --due >> campaigns/publish.log 2>&1
```

Only one run at a time: a second run prints a line and exits.

### What the states mean

| State | Meaning |
| --- | --- |
| `pending` | waiting to be sent |
| `waiting` | will be tried again at the time shown (after a temporary error) |
| `posted` | live; the link is shown |
| `blocked` | waits for an item it depends on, which failed or is unknown |
| `failed` | stopped; the message says what to fix |
| `unknown` | a publish request was sent but no clear answer came back. The timer never sends it again. Check the account; if the post is not there, retry it |

Clear a failed or unknown item with:

```sh
python3 marketing/run.py publish --retry mc-32 x-2
```

The next `publish --due` sends it. `python3 marketing/run.py status mc-32` shows every item's state.

To change a post, quit at the gate, change the step before it (edit its `output.md`, or delete its `APPROVED` file to redo it), and run again. Once approved, the plan is frozen. If `publish.jsonl` exists but `APPROVED` does not, the gate refuses to rebuild the plan, because posts may already be live.

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
