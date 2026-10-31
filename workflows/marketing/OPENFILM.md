# OpenFilm asset adapter contract

The opt-in adapter accepts a private run ID and versioned brief, builds a
versioned project and returns references to the project, outputs and terminal
evidence. The runner persists these privately; customer assets, project
contents, credentials and provider keys must not enter public Git history. A
change to the brief or project invalidates dependent assets and review.
Cancellation requests a stop; an uncertain result must be inspected before
retrying. A finite retry limit applies to known failures only; `render_decision`
is a policy helper, not a process manager.

The worker now requires an asset brief JSON bound to the frozen canonical content
and verified source register. Each requested asset carries channel, kind,
dimensions, duration, exact approved text excerpts, registered claim IDs,
media and rights artifact references, captions and alt text. Media and rights
references must exist in the private artifact store. Their presence does not
establish ownership or permission: an independent rights review is still needed.
Brief validation alone does not verify current platform constraints or prove
that the brief lists every material claim; those remain production and review
gates.

Before rendering, an independently trusted platform authority must provide a
brief-bound proof for every asset, citing stored source evidence and confirming
its exact channel, format, dimensions and duration. Proofs are valid for at
most 24 hours and are rechecked after rendering and during final review; a
missing, expired or unverified proof blocks the affected production stage. The
authority must independently authenticate current platform sources and assess
the requested format; a model-written assertion is not sufficient. No actual
platform authority is configured in the distributed worker. Test authorities
are synthetic and do not establish current platform rules.

Platform checks must identify the specific publishing surface and account
context. For example, LinkedIn documents different duration limits for
[sharing a video](https://www.linkedin.com/help/linkedin/answer/a7486279) and
[Page videos](https://www.linkedin.com/help/linkedin/answer/a1311816). Meta's
business help pages returned a sign-in screen during the 2026-10-30 source check;
that response is not usable as evidence of Instagram constraints. These links
are research pointers, not a configured or automatically verified policy.

Official CLI documentation specifies `openfilm look [what] [t | a-b]` for PNGs under `.film/cache/look/`, and `openfilm render [a-b]` for MP4, with `--out <file>` available. Calls operate on the project around the current folder. See https://openfilm.dev/docs/cli/. This contract does not execute those commands: presence, headless operation, isolation, output paths and cancellation on the Coolify host remain unverified. Never use `npx` to fetch software at runtime or call media-provider commands without separate authorization. OpenFilm documents that local browser-drawn graphics and supplied media can be rendered without a service, while generated media can use separately billed providers: https://openfilm.dev/docs/services/.

`assets.verify_asset` checks private-path confinement, output hash, matching brief and project versions, dimensions and, for MP4, ffprobe metadata and duration. It copies the hashed bytes into a private temporary snapshot and asks ffmpeg to decode it; unavailable tools, decoding errors and malformed outputs block. PNG checks also validate its signature, chunk boundaries and checksums. Decoding does not validate visual content, audio quality, accessibility, licensing, safety or rights. A technical pass returns `awaiting_review`, never a final pass. An independent human or implemented independent visual/rights verifier must review the actual output and record evidence before it becomes review-ready. Both ffmpeg and, for MP4, ffprobe must be locally available. The runner must ensure the verified original remains immutable between verification and use; the temporary snapshot does not lock it.

When a video brief supplies WAV or MP3 media, a render with no decodable audio
stream is blocked. A stream's presence does not prove that it contains the
supplied recording, that it is audible or that its timing is correct; those
remain reviewer checks. Audio embedded in supplied video media is not detected
as a separate audio requirement by this check.

For MP4s the technical record lists each detected audio codec, sample rate and
channel count; ffmpeg decodes the primary video and every audio stream. An empty
list means no audio stream was detected, not that silence is acceptable for the
brief. The current brief does not describe required audio, so editorial review
must determine whether audio is needed and whether it is appropriate and licensed.

`ingest_verified_asset` confines ingestion to regular, non-symlink files and
copies the exact bytes to the private content-addressed artifact store before
checking them. A successful result binds the immutable artifact reference to the
brief and project versions and still requires independent review. Rejected input
may leave an unreferenced private artifact.

The worker's opt-in `openfilm_backend` integration reserves a graph attempt
before rendering, binds the attempt to the brief and current graph revision,
requests cancellation on a failed or interrupted supervisor call, and refuses
to complete an unverified outcome. A trusted backend must independently verify
the exact attempt's terminal state and bind its editable project and output
paths to that attempt; its `ready` check must establish the pinned renderer,
network/provider restrictions and isolation. The worker validates a bounded
project ZIP containing `film.html`, ingests and decodes every requested output,
and records the manifest as `awaiting_review`. Confirmed bad outputs are
blocked; uncertain outcomes stay fenced. The distributed worker configures no
project builder or independent visual/rights reviewer, so these are integration
boundaries, not a functioning unattended renderer or completed review flow.
The supervisor interface has `timeout_seconds` (1–600), a read-only `ready()`,
`execute(run, attempt_id, brief_ref, brief, canonical, graph_claim, queue_claim)`,
`cancel(run, attempt_id)`, and `verify_terminal(run, attempt_id, brief_ref,
result)`. `execute` returns exactly `attempt_id`, `status` (`exited` or
`failed`), `root`, `project_ref`, `outputs`, and `terminal_ref`. Each output is
an ordered `{path, sha256, previews, report}` object matching one brief item;
each preview and report has a private `{path, sha256}` reference. The worker
decodes previews and preserves the OpenFilm visual-check report alongside each
asset. A repeated text-cutoff warning blocks the asset and retains the report,
preview and editable project reference for correction. This check does not
establish safe zones or catch every readability problem. A structured failed
`look` result retains its private report and project reference after the
supervisor verifies termination; other render failures may have no report.
`project_ref` names a
private ZIP of the editable project, and `terminal_ref` names private evidence.
A single-asset ZIP has `film.html` at its root; a multi-asset ZIP has one
`projects/<zero-based-index>/film.html` per brief item, in output order.
The terminal verifier must authenticate the exact process and bind those
references and paths to that attempt; a worker or model assertion is not an
acceptable verifier. `cancel` requests a stop, and its return does not prove
termination.

`runner/openfilm_render.mjs` is a fixed render entrypoint for a future isolated
job image with OpenFilm 0.2.6 already installed. It invokes the package's
`look` and `render` modules directly, avoiding the CLI's automatic browser
download. In a host fixture with an empty browser directory, the pinned modules
failed without creating a browser or asset; the CLI path is not used by this
fixture. It requires a successful same-picture check and produces fixed output,
preview and private report files. Stills run a range check for repeated text
cutoff as well as a separate full-size frame export; a local synthetic cropped
text fixture exercises that warning. It has no isolation of its own: the trusted
supervisor must run it with network disabled, without credentials and with only
the intended private project and output mounts. Its real host execution was
tested locally; the job image and container path remain unverified.

`DockerOpenFilmBackend` provides an opt-in Engine implementation with an exact
attempt and both task leases, a fixed entrypoint, only private workspace/output
mounts, a read-only root filesystem, an unprivileged UID, dropped capabilities
and network mode `none`. It requires a separately trusted project builder and
host-policy verifier, plus owner approvals for both `docker_config` and the
derived `openfilm_policy`. The policy hash binds the image configuration, helper
bytes, OpenFilm version and timeout. The verifier must establish that the pinned
image actually contains those bytes, the approved OpenFilm/browser/ffmpeg
versions and the required host isolation. `Dockerfile.openfilm` accepts an
already prepared base image and performs no package installation; it has not
been built or tested against the real daemon here. Synthetic Engine tests are
not evidence that the host enforces container isolation.
The image build checks the pinned package's expected Chromium headless-shell
revision and requires its binary to be executable as the image's non-root user;
an empty browser directory does not pass that check. This does not establish
that Chromium can launch in the target runtime.
The terminal check requires the exact configured OpenFilm home, browser path and
download/update-disabling environment values, with no duplicate assignments;
changing them invalidates the render evidence.

A local prerequisite probe on 2026-10-30 started and removed a temporary,
network-disabled, read-only container from cached Fedora Toolbox 42 image
`sha256:5bc35278f48c99eaf9dbe11000abfc1edea8b55b921f7e376eaf27edecd6f6ab`.
Its non-root shell found none of `node`, `ffmpeg`, `ffprobe`, `chromium` or
`chromium-browser` on `PATH`. This cached image is therefore not a demonstrated
OpenFilm runtime; the probe did not build the job image or verify Coolify.
The same kind of probe against cached Agent Zero image
`sha256:46e4e5fd59568521ba64de19bb8e866dca7df57d98545cfb272777af6e915fae`
found Node, FFmpeg and FFprobe, but neither Chromium command on `PATH` nor a
resolvable `openfilm/package.json` from its working directory. The cached Buzz
image `sha256:cc2a25011152102b4b26166428bf15cc34131c2f916ddd902881788d5d5940d8`
found none of those commands. Both probes were removed. These checks do not
establish that browsers or packages are absent elsewhere in those images.

An additional disposable offline probe copied the local OpenFilm 0.2.6 source,
Playwright Core 1.61.1 and cached Chromium headless shell into private temporary
directories, then mounted those copies with a synthetic project in the cached
Agent Zero image above. Its Node 22.22.0, FFmpeg and browser rendered a 180×320
PNG and a one-second, 180×320 MP4. Local verification decoded both, checked their
hashes and dimensions, and measured the MP4 duration as one second. The process
reported zero effective and bounding capabilities; inspection showed network
mode `none`, a read-only root and UID 1000. The temporary container and files
were removed. This probe used four mounts and a shell wrapper, not the intended
two-mount job image or production supervisor; it does not verify that image or
Coolify. The rootless engine expanded `CapDrop: ["ALL"]` into individual names
and exposed mounts outside `HostConfig.Mounts`; the current strict Docker
terminal verifier rejects that representation rather than asserting equivalent
isolation without a trusted engine-specific verifier.

The installed local checkout at commit `de5a98bd46e2edfe8a98a6ca5343f925f71d53ff`
uses `playwright-core` 1.61.1. A possible starting point for preparing a separate
image is Microsoft's [Playwright 1.61.1 Noble image](https://mcr.microsoft.com/en-us/artifact/mar/playwright/tag/v1.61.1-noble),
whose published multi-platform manifest digest is
`sha256:5b8f294aff9041b7191c34a4bab3ac270157a28774d4b0660e9743297b697e48`.
[Playwright's Docker documentation](https://playwright.dev/docs/docker) says its
images include browsers and system dependencies but not the Playwright package,
and recommends matching browser and package versions. This is a candidate base,
not a prepared OpenFilm image: OpenFilm, FFmpeg availability, browser path,
non-root isolation and rendering must be verified in the resulting image. No
image was pulled or built as part of this research.

An optional `CodexOpenFilmProjectBuilder` creates the editable project in a
separate claim-bound Codex session and verifies its private provenance before
rendering. Its instructions request visible, readable video captions from the
brief; this instruction alone does not prove they were rendered. Supplied PNG,
JPEG, GIF, WebP, WAV, MP3 and MP4 bytes are copied into
reserved media paths in the private project after authoring; unsupported formats
block. The bound rights references must exist, but still require review. The builder
requires its own trusted host-policy verifier and exact
`openfilm_builder_policy` approval. It is not enabled by the distributed worker.

An opt-in `openfilm_reviewer` must provide a fresh `asset_review` session,
configured skills, a bounded review operation and an independently authenticated
access receipt covering the exact rendered artifact references. Its report checks
content and brand fidelity, rights, platform suitability, readability, captions,
safe zones and audio for every asset. The reviewer must check applicable audio or
confirm that none is required. Review findings remain judgments, not mathematical
proof. A missing access verifier blocks a pass; a trusted verifier must establish
that the reviewer accessed the actual media rather than merely saw model-written
descriptions. On an interrupted or invalid review the worker requests cancellation
and keeps the attempt uncertain. A final review package rechecks the asset
references, render termination, reviewer provenance, skill hashes, access receipt
and report. No actual media-access authority is configured by default.

Instagram review depends on both its draft and terminal asset production. A
passed asset review supplies version-bound media references and its independent
review report to the channel reviewer; a blocked asset keeps Instagram blocked.
The known prior graph is migrated when both queue and graph leases are held and
no execution or external action is uncertain. Migration invalidates the old
Instagram review and assembly, preserves unrelated channel work and records the
old and new definition hashes in SQLite. Unknown graph versions do not migrate
automatically.

On the development host, OpenFilm source version 0.2.6 at commit `de5a98bd46e2edfe8a98a6ca5343f925f71d53ff` rendered a synthetic 180×320, one-second MP4 and a PNG preview through its JavaScript modules; the verifier decoded both. This is host-only evidence, not a CLI or container verification. The CLI can attempt a browser download when the browser is absent, so the isolated job must prevent runtime installation.

An additional host test packages a synthetic supplied PNG into the editable
project and checks the rendered pixel, exercising the same private-media path.
The builder's synthetic Docker-engine test checks its session, project and
termination evidence; it does not establish actual host isolation.

Suggested handoff: brief reference/version; project reference/version; output reference/hash; attempt and cancellation state; verifier identity and evidence; content/rights review disposition; current-version comparison. Stale, missing, unavailable, or failed evidence blocks readiness. An OpenFilm asset may be omitted from a review package only with an explicit blocked state; other text work can continue. No automatic publication.
