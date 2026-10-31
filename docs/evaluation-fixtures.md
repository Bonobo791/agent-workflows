# Evaluation fixtures

Run `python -m unittest tests.test_evaluation_fixtures` for credential-free,
synthetic checks of the source register, independent reviewer identities, Reddit
context, current brief binding, an actual decoded image, stale project versions,
exact-commit CI and review dispositions. These cases exercise existing gates;
they do not prove semantic truth, platform compliance or real source authority.
`tests.test_page_structure` evaluates explicitly configured Markdown heading
rules and rejects headings hidden in comments or code blocks. Missing rules or
the optional parser make the structural check unavailable and block approval;
this check does not evaluate semantic completeness or visual rendering.

The broader fixtures exercise evidence that cannot be represented by a single
static record:

- `tests.test_stage_entrypoint` runs the marketing flow with synthetic Plan,
  GitHub, model and source authorities, including source coverage review,
  correction, package approval and stale derivative invalidation. One case
  verifies the missing-asset block; another uses a decoded synthetic image and
  synthetic platform and asset-review authorities to exercise all five channels.
  The latter also checks that a paused quota, missing role skill and unavailable
  runtime authorization return explicit states before a brief attempt starts.
- `tests.test_stage_dispatch` covers the initial synthetic subscription-limit
  response and the separate quota and stage resume controls. It does not verify
  a real account limit or termination of a live model process.
- `tests.test_openfilm_stage` tests actual decoded artifact references, reviewer
  access evidence, missing and stale inputs, cancellation and retry limits.
  `tests.test_openfilm_integration` optionally renders synthetic media with a
  locally installed OpenFilm package. The opt-in
  `tests.test_openfilm_podman_integration` also renders a synthetic still and
  vertical MP4 without credentials in the configured isolated image, then checks
  the resulting files and previews. It does not establish platform compliance.
- `tests.test_engineering_entrypoint` exercises supervisor-observed acceptance
  commands, separate QA, exact-commit checks, review dispositions, approved PR
  actions and a second review using synthetic integrations.
- `tests.test_audit_export` checks replay, redaction and exporter failure for
  both workflows. A loopback HTTP receiver checks OTLP partial acknowledgement
  and retry without contacting Opik. Unavailable telemetry is not a passing
  evaluation or zero activity.
- `promtool test rules observability/alerts_test.yml` checks the Prometheus
  alert delays, recovery, and stale-versus-missing snapshot behavior. It does
  not test a deployed collector or notification routing.

These are local evaluation fixtures, not a deployment test, a live model-quality
benchmark or a substitute for human review of actual content and media.
