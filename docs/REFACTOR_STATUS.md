# Docker removal and runner cleanup: status

Branch: `refactor/remove-docker-pr` (uncommitted; do not commit until the suite shows only the pre-existing failures).

## Done
- PR actions use the host transports only (`runner/local_push.py`, `runner/local_mutation.py`, `runner/local_pr_authority.py`); push helpers live in `runner/git_push.py`.
- Engineering commands use the local command backend only.
- Docker runtime modules deleted: controller, supervisor, attach, config, push, mutation, command.
- Docker-table checks removed from checkpoint, graph, lifecycle, dispatch and workspace code.
- Podman compose file and its doc paragraph removed.
- Model output: the pipeline sets `input_versions`; asset briefs get `content_ref`, `social_drafts` and the canonical heading from stored artifacts.
- Policy hashes no longer include source-file bytes (`openfilm_builder`, `local_asset_review`, `platform_reviewer`, `source_snapshots`).
- `lease_heartbeat` helper in `runner/auto_worker.py`, used by the live test.
- `from None` raises chain their cause (`from exc`) or drop a no-op suffix.
- Stopped-scope check accepts `failed` (`STOPPED_STATES` in `runner/local_scope.py`).

## Remaining
1. **Done:** synthetic pipeline tests now run on the local runtime (`tests/test_stage_entrypoint.py`, fake in `tests/fixtures/fake_marketing_codex.py`). Review the three changed assertions listed in the session notes.
2. **Commit gate:** the suite should show only the 7 failures that already fail on the original commit (`tests/test_service.py` ×6, `tests/test_integration_review.py` ×1).
3. **Engineering admission test** (`tests/test_engineering_entrypoint.py`) is skipped with a tracking note. Rewrite it on the local runtime and remove the skip.
4. **Swallowed `False` verifiers** (about 36, mostly `runner/service.py`, `runner/local_scope.py`). Each needs a reason-carrying variant, because callers test the boolean.
5. **`runner/local_scope.py` simplification:** replace the cgroup-identity, nonce, receipt and audit-trigger machinery with start, record, stop and check against `STOPPED_STATES`. Fail fast at `start_local_process` when there is no user systemd manager.
6. **Systemd audit:** check every other comparison against systemd properties (`ActiveState`, `LoadState`, `Result`, `SubState`) in `local_scope.py`.
7. **Lease heartbeat:** make `AutoWorker.keepalive` use `lease_heartbeat` where it can, keeping its checkpoint handling.
8. **One pipeline:** drive the live test through `AutoWorker` (or a shared run-to-gate function the worker also calls), and remove its test-only skills.
9. **Renames:** about 58 "isolat" and about 300 "docker" mentions in `runner/`, including error messages and comments. Rename by what the code does now.
10. **Docs:** update `docs/OPENFILM.md`, `docs/README.md` and `docs/container-runtime-decision.md` for the removed container path.
11. **Host run:** run `tests.test_local_marketing_graph_live` on the host (command in the plan file) and record the result here.
