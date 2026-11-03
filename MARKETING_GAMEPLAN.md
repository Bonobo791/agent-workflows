# Marketing workflow — simple game plan

## What you want

```
task → brief → SEO article → social copy (per channel) → images → Reel video → final package → you
```

Every step runs the same loop:

1. **Writer** (Codex + that step's skill) produces the output.
2. **Reviewer** (Codex, fresh thread + that step's review skill) reviews it.
3. **You** approve, or reject with notes (the writer reruns with your notes and the review).
4. On approval, the next step starts.

At the end, everything lands in one folder for your final review. You decide what to do with it. Nothing is published.

## Why it got expensive

The repo has ~12,500 lines of runner code, ~19,400 lines of tests (1,360 tests), a 285 KB QA log, and 349 commits for a workflow that has never run on a real campaign. Most of it serves things you didn't ask for:

| Built | Needed for your goal? |
| --- | --- |
| Durable graph, claims, leases, fencing, checkpoint/restart (`runner/graph*.py`, `queue.py`, `checkpoint.py`, `lifecycle.py`, `local_scope.py` …) | No. A folder per campaign is enough state. |
| Docker/Podman/systemd isolation, controllers, supervisors | No. |
| Prometheus, Grafana, Alertmanager, Opik, OTel audit delivery (`observability/`) | No. |
| OAuth, credential delivery, GitHub PR authority, engineering workflow | No (engineering is out of scope for now). |
| "Trusted source authority", "Reddit authority", "platform authority", evidence hashing | No. These were never configured, so stages block forever by design. |
| Unix-socket control service, owner-decision transactions | No. A terminal prompt is the approval. |

The cost spiral came from AI review bots (CodeAnt, Sonar, CodeRabbit) and QA passes raising safety findings, each fix adding new infrastructure, which drew more findings.

## What to keep

- `tools/codex_harness/codex_client.py` (~90 lines). It already drives the real `codex app-server`, takes image inputs, and enforces a JSON output schema. This is the engine.
- `workflows/marketing/PROMPTS.md`: use as starting material for the skills, then trim.
- OpenFilm (`containers/openfilm`, `runner/openfilm_render.mjs`): the harness has already rendered video with it. Use it for images and the Reel.

Everything else goes to a `legacy` tag/branch. History is kept, but it leaves `main`.

## Target layout (~300 lines of code total)

```
marketing/
  run.py               # the whole workflow: step loop + approval prompt
  codex_client.py      # moved from tools/codex_harness
  steps.yaml           # ordered steps: name, writer skill, reviewer skill, inputs
  skills/
    brief/        SKILL.md   review.md
    seo-article/  SKILL.md   review.md
    linkedin/     SKILL.md   review.md
    x/            SKILL.md   review.md
    instagram/    SKILL.md   review.md
    images/       SKILL.md   review.md   # writes OpenFilm scene specs; run.py renders
    reel/         SKILL.md   review.md   # writes OpenFilm film spec; run.py renders
campaigns/<slug>/      # gitignored, one folder per campaign
  00-task.md
  01-brief/      output.md  review.md  APPROVED
  02-article/    output.md  review.md  APPROVED
  ...
  final/         everything approved, plus index.md
```

**State = files.** Resuming means `run.py` finds the first step folder without `APPROVED`. No database.

**Approval = terminal prompt:** `[a]pprove / [r]eject with notes / [q]uit`. Reject passes your notes plus the review back to the writer and loops.

**Reviewer verdict** uses a small JSON schema: `pass | revise` plus a list of findings. A `revise` verdict reruns the writer once automatically, then always shows you the result. That cap bounds the cost.

**Images and video:** Codex writes the OpenFilm project, `run.py` calls `openfilm render`, and the reviewer gets the rendered frames as images (the harness already does this).

## Build order

1. **Archive.** Tag the current `main` as `legacy-2026-11`, then remove `runner/`, `observability/`, `deploy/`, `docs/`, `tests/`, the QA/handoff/status docs, compose and Dockerfiles from `main`. Leave `workflows/engineering/` untouched on the legacy tag.
2. **Steps 1–2.** `run.py` with the brief and SEO-article steps plus the approval loop. Run it on one real task end to end.
3. **Social copy.** Add a step per channel. Each step is a few lines in `steps.yaml` plus two skill files.
4. **Images + Reel.** Add the OpenFilm render call and image-input review.
5. **Final package.** Copy the approved outputs to `final/` and write `index.md` listing every asset.

Tests: one smoke test per step against a fake Codex client. No other tests.

## Decisions needed from you

1. **Task source:** a pasted/markdown task file (simplest), or a fetch from It's a Plan by issue key (one API call)?
2. **Channels:** which ones? The old code had LinkedIn, X, Instagram and Reddit.
3. **Images/video:** OpenFilm only (already working), or a generative image/video tool?
4. **Skills:** do you already have the skill files for each step, or should they be drafted from `PROMPTS.md`?
