# Private Codex session state

The selected design is a new, application-owned persistent `CODEX_HOME` for each task and resumable writer/implementer role. A replacement would mount the same home in place; it would not copy an existing user's home or guess a subset of undocumented Codex files. Each reviewer gets a fresh separate home. This is local application-owned state; do not assume OpenAI stores the task's resumable thread for the application. [OpenAI's app-server guide](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server) describes restarting with a refreshed OAuth access token and using `thread/resume` with the saved thread ID. [Codex's environment reference](https://developers.openai.com/codex/config-file/environment-variables) describes `CODEX_HOME` and `CODEX_SQLITE_HOME` as local state roots.

`SessionStore` prepares new private homes and binds them to the run, role, session ID, configuration digest, skill digest and exclusive assignment. A second assignment cannot claim an active home. Release requires a trusted termination verifier; reviewers cannot resume. Backup requires an idle home and a separate trusted credential-boundary verifier. Deletion uses a trusted callback and fences uncertain outcomes. Known credential/configuration filenames, links and non-regular files are rejected. These checks and synthetic fixtures do **not** prove that arbitrary state content is free of secrets or that a real Codex version can resume from a replacement container.

The Docker supervisor now checks the exact registry binding, mounts the matching private home at `/codex-home`, and implements `can_resume` for matching task, role, session, skill/model/command and runtime-policy fingerprints. It uses the documented ChatGPT-plan provider command and excludes inherited shell environment. A synthetic Engine/app-server fixture exercises writer replacement and a separate reviewer home. No token is put in Docker configuration. The credential-delivery interface and home verifier have no production implementation, so configured runtime preflight remains closed. No actual credentials, legacy Codex homes, host volumes or access permissions have been read or configured. The pinned app-server version, secure delivery and separation of credentials, mount policy, and host/container lifecycle validation remain unverified. The verifier callbacks are trust boundaries, not production verifiers supplied by this project.

The private worker now connects exact-task discovery, stage dispatch, independent reviewer execution and assembly in synthetic tests. `WORKFLOW_EXECUTION_MODE` still rejects live execution. Synthetic state/process tests do not establish real Codex thread persistence, OAuth entitlement or provider storage behavior.

The selected app-server integration remains developmental: [OpenAI's app-server documentation](https://developers.openai.com/codex/app-server) describes it as experimental and not supported for production workloads. A private pilot or successful fixture test does not establish production support or host safety.

The requested role-wide model preference is GPT-6.1 Terra with medium reasoning. The executor requires a provider model ID discovered from `model/list`; it does not assume the internal model alias or fall back to another model. The provider catalog is not an entitlement check. The role-preference fixtures alone do not establish a successful provider execution.

For the local signed-in runtime, a separate opt-in test exercises an idle
checkpoint and resumes the persisted graph session in a replacement worker
process. The worker uses a new incarnation and obtains fresh queue and graph
claims; a second process cannot start while the original holds the state lock.
The task and dispatcher in this test are synthetic. This does not establish
recovery of an interrupted turn or ambiguous external action. See the planned
restart procedure in [docs/PILOT_SETUP.md](docs/PILOT_SETUP.md).
