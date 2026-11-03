# Marketing workflow

One task becomes a brief, an SEO article, LinkedIn, X and Instagram posts, three images and an Instagram Reel.
Every step is written by Codex, reviewed by a separate Codex thread, and then waits for your approval.
Nothing is published. The approved files end up in `campaigns/<name>/final/`.

## Requirements

- Python 3.10 or newer
- `codex`, installed and signed in
- `openfilm` 0.2.6 and `ffmpeg` with libx264, on `PATH` (needed from step 06)

## Use

```sh
python3 marketing/run.py new spring-launch my-task.md   # create a campaign from a task file
python3 marketing/run.py run spring-launch              # run; stops at every step for your decision
python3 marketing/run.py status spring-launch           # see where it is
```

At each step you see the output and the review, then type:

- `a` to approve and continue
- `r` to reject and type what should change
- `q` to quit; run the same command later to come back to the same step

## Change the steps or the skills

- Skills: `marketing/skills/<step>/SKILL.md` (writer) and `REVIEW.md` (reviewer). Replace them with your own.
- Steps and their order: the `STEPS` list at the top of `marketing/run.py`.

## Files of a campaign

```
campaigns/<name>/00-task.md
campaigns/<name>/01-brief/output.md, review.md, APPROVED, history/
...
campaigns/<name>/final/index.md
```

## Tests

```sh
python3 -m unittest discover -s tests
```

The old runner, including the engineering workflow, is kept under the Git tag `legacy-2026-11`.
