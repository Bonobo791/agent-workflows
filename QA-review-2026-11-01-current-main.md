# Current main QA review — 2026-11-01

Reviewed remote main `af7dbdacba3e506bbaf81b8a8812759bdc00190c` in an isolated checkout against `52640f0551068949731c55bd7948831b8938dd9c`, covering merged PRs #32–#35. No open PRs were present when checked. The shared workspace and remote branches were not changed by this review.

## Confirmed findings

1. **Mandatory SEO/GEO skill is not selected by the worker.** At `runner/worker.py:42-48`, `build_control` copies the caller's role lists unchanged. A focused probe configured `canonical_draft: [writer]` and `canonical_review: [editor]` and observed those exact lists, with no `plan-seo-geo-growth` or error. This does not implement the user's requirement that the skill work through its files to update the content, and contradicts the historical claim at `QA-review-10-31-26.md:1702` that the worker automatically adds the skill. The complete package is available when explicitly configured, but that is a separate behavior.
2. **A symlinked skill directory substitutes a sibling package.** At `runner/skills.py:56-59`, only the final `SKILL.md` is checked for being a symlink; resolving its parent can follow a symlink. A temporary fixture with `selected -> other` loaded `other/SKILL.md` when `selected` was requested. The actual content is hashed, but the configured skill identity is not enforced and sibling-package files can be included in the prompt. The existing regression covers an entry-file symlink, not a symlinked skill directory.

## Existing acceptance defects, predating this diff

- `runner/graph_runner.py:114-124` accepts `awaiting_review` as a stage outcome, while `runner/graph.py:342` requires a correction result to be `passed`. A retained real acceptance run has a completed correction response of `awaiting_review` and an uncertain graph attempt, preventing the approved draft from advancing. These files are unchanged from the review base.
- `runner/source_fetch.py` tries only the first public DNS address. In the earlier real acceptance run, several IPv6 connections timed out while other public IPv6/IPv4 addresses connected; an isolated address-fallback experiment passed its source-fetch tests and fetched all three approved pages. That experiment is not merged and is not a validation of current main.

## Further investigation

**Systemd preflight cleanup — open, not yet reproduced on a real host.** In the reviewed snapshot, `runner/local_preflight.py:36-40,87-89` could treat an inactive, not-found unit as absent without checking for a pending systemd job. A mock showed the preflight state database being removed under those observations; it did not establish that the failed `systemd-run` path can leave a real start queued. A future investigation should use a disposable user scope to establish whether that ordering is possible and whether the retained state is removed before the start resolves. Until then, treat this as a suspected race, not a confirmed systemd defect. No host test was run for this note.

## Validation and hosted checks

- Full current-main suite: `python3 -m unittest discover -s tests` — **1,360 run, 115 skipped, OK** in 361.216 seconds. This includes skipped opt-in tests; it is not production acceptance.
- Current installed `plan-seo-geo-growth` package resolved as 12 files, including 9 references, 88,507 prompt bytes, package SHA-256 `7499117b1300fc79ec2b16ddce971e3d8b3914301b1c596214d36636743f0fee`.
- PR #32 final head `0345da9`: SonarCloud and Semgrep passed; CodeAnt Quality Gates and SCR failed; CodeRabbit skipped.
- PR #33 final head `a33e853`: SonarCloud and CodeAnt Quality Gates/SCR failed; Semgrep and CodeRabbit passed.
- PR #34 final head `5593193`: SonarCloud failed; Semgrep and CodeAnt Quality Gates/SCR passed; CodeRabbit skipped.
- PR #35 final head `d3348c9`: SonarCloud, Semgrep and CodeAnt Quality Gates/SCR passed; CodeRabbit skipped. CodeAnt reported coverage data unavailable.

Hosted results apply to the listed PR heads, not automatically to merge commit `af7dbda`. No real campaign, publication, deployment, or production monitoring acceptance was performed. Review findings need fixes and verification before the affected paths are considered ready.
