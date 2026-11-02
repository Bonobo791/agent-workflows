# Codex marketing harness

Runs real marketing stages through the Codex app server (`codex app-server --listen stdio://`).
Synthetic files in `data/synthetic/` are input data only; the stage prompts come from
`workflows/marketing/PROMPTS.md` and each result is checked against `stage-result.schema.json`.

    python3 tools/codex_harness/run_task.py brief
    python3 tools/codex_harness/run_task.py canonical_review

Outputs go to `tools/codex_harness/runs/<UTC time>/<stage>/result.json` (gitignored).
Not covered: OpenFilm rendering, asset review, and assembly, which need Docker and media tools.
