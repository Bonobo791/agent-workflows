# Marketing workflow

One task becomes a sourced brief and an SEO article, then the social posts, images and reel that its Deliverables ask for.
Every step is written by Codex, checked by Python, reviewed by a separate Codex thread, and then waits for your approval.
Nothing is published. The approved files end up in `campaigns/<name>/final/`.

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
```

Each step folder has `usage.jsonl` (the tokens of each Codex call) and `writer-thread.txt` (the writer's Codex thread, which a revision continues). `status` adds the tokens up.

To follow a run from another terminal, run `tail -f campaigns/<name>/run.log`.

Campaigns made before the sources step was added use the old folder numbers and cannot be resumed. Start them again.

## Change the steps or the skills

- Skills: `marketing/skills/<step>/SKILL.md` (writer) and `REVIEW.md` (reviewer). Replace them with your own.
- Steps, their order, what each one needs and its automatic checks: the `STEPS` list and the `CHECKS` table in `marketing/run.py`.

## Tests

```sh
python3 -m unittest discover -s tests
```

The old runner, including the engineering workflow, is kept under the Git tag `legacy-2026-11`.
