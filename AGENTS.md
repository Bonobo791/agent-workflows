# AGENTS.md

Rules for any agent working in this repo. The main code is the marketing pipeline in `marketing/`. The engineering pipeline in `engineering/` is separate and has its own rules.

## Repo and tools

- `claude --help` is read from a file, not a pipe: a piped help text is cut at 16 KB. Every required flag must appear in it.

- Commands run from the repo root. Tests: `python3 -m unittest discover -s tests` (currently 464, all passing).
- Campaigns live in `campaigns/<name>/` and are not tracked by git. Do not add them to commits.
- Brand folders live in `marketing/brands/<brand>/` (`voice.md`, `positioning.md`, `pages.md`, `design-system.md`, `character.md`). Folder names are lowercase. A task's `## Brand` line must match the folder name exactly.
- No secrets in the repo. `ELEVENLABS_API_KEY` comes from the environment only.
- Do not commit or push unless asked. Work on a new branch from `main`. Do not reuse a branch whose PR is merged.

## Marketing pipeline: commands

- `python3 marketing/run.py new <name> <task-file>`: campaign from a task file.
- `python3 marketing/run.py new <name> --issue MC-37`: campaign imported from an It's a Plan issue. This needs the It's a Plan connector to be connected in the interactive Claude Code session. Headless `claude -p` never connects to it.
- `python3 marketing/run.py new <name> --issue MC-37 --issue-file issue.json`: same, from a saved copy of the issue as It's a Plan returns it. The importer gets the issue body only; linked documents are not included.
- `python3 marketing/run.py run <name>`: runs the steps. It stops at each step for an owner decision: `a` approve, `r` reject with notes, `q` quit.
- `python3 marketing/run.py run <name> --from 08-reel-script`: resumes at that step. Earlier steps must be approved; their stale approvals are not checked, so they are not re-run.
- `python3 marketing/run.py run <name> --to 08-reel-package`: stops after that step, so the audio and video steps (08-reel) do not run. Combine with `--from` to run a range.
- `python3 marketing/run.py status <name>`: step states and token and cost totals.
- `python3 marketing/run.py brand <brand>`: drafts `positioning.md`.
- `python3 marketing/run.py elevenlabs voices [search]`: lists ElevenLabs voices (id, name, category), to fill in `character.md`.

## Steps, roles and models

Roles are set in `ROLES` in `marketing/run.py`:

| Role | Model | Effort | Budget per call |
|---|---|---|---|
| writer | claude-haiku-5-5 | xhigh | $1 |
| script | claude-sonnet-5-5 | high | $4 |
| importer | claude-sonnet-5-5 | medium | $5 |
| strategist | claude-sonnet-5-5 | high | $3 |
| reviewer | claude-sonnet-5-5 | medium | $4 |
| creator | claude-sonnet-5-5 | xhigh | $6 |

Steps (`STEPS` in `marketing/run.py`). A step's writer skills run in order; its reviewer loads every skill the step uses.

- 01-sources (writer): content-research-and-sourcing. Research only, no voice.
- 02-brief (strategist): content-brief. No voice.
- 03-article (writer): on-page-seo, internal-linking, then moderaty-voice last. Second review against plan-seo-geo-growth (private skill, read from `~/.config/agent-workflows/private/skills/`).
- 04-linkedin (writer): hook-writer, linkedin-post-writer, then moderaty-voice last.
- 05-x (writer): hook-writer, thread-writer, then moderaty-voice last.
- 06-instagram (writer): hook-writer, caption-writer, hashtag-strategy, instagram-seo, then moderaty-voice last.
- 07-images (creator): openfilm-manual, quote-cards-and-text-graphics, then moderaty-voice last.
- 08-reel-script (script): video-hooks, short-form-video-script, reels-script, voiceover-script, then moderaty-voice last. One continuous script in one take: no scenes, parts or separate clips. Audio tags are the only brackets: at most 4, from a short list.
- 08-reel-package (script): video-titles, video-captions, video-metadata, then moderaty-voice last. The reel's titles, first-frame text, Short description, TikTok caption and hashtags, and pinned comment. The YouTube Short and the TikTok post take their text from it; the pinned comment is posted by hand.
- 08-reel (creator): openfilm-manual, motion-graphics, instagram-reels-publishing, then moderaty-voice last. Its first scene shows the package's On-screen text. Writing skills stay with 08-reel-script and 08-reel-package. Before the film, the run makes the voiceover with ElevenLabs (`eleven_v4`) into `08-reel/voiceover.mp3` and stops until the owner's avatar clip is at `08-reel/assets/presenter.mp4`; the clip must match the voiceover's length within 0.3 s.
- 01-positioning (brand command only): content-research-and-sourcing.

The reviewer of every copy step also loads moderaty-voice. Steps 01-sources and 02-brief do not get the voice.

## Writer loop

- The first writer skill drafts. Each later skill is a pass on the same writer session, and the pass returns the complete deliverable in the same JSON shape.
- Checks run between passes. Failures go into the next pass as "Checked now".
- A pass can return `unchanged: true` with an empty deliverable, which keeps the draft. Every writer call carries the `unchanged` field, so the schema stays the same and the cache is kept.
- `passes.json` in the step folder lets an interrupted run continue at its next skill. A changed prompt, skill list or session starts a fresh draft.
- `passes.md` logs what each pass changed. Pass notes are logged, not published.
- The deliverable is materialized once, after the last pass. Reviewers see only the final draft.
- Image and reel steps re-emit the whole OpenFilm project on every pass, so they cost more.

## Reviewer

- One reviewer call per step, first review loads every skill the step uses. Re-reviews send only the new draft.
- Automatic checks run first. A failing check goes back to the writer without a reviewer call.
- One automatic rewrite per draft (`MAX_AUTO_REVISIONS = 1`), then the owner decides.

## Sessions and cost

- One Claude Code session per role per campaign, saved in `campaigns/<name>/sessions.json`. Every pass and revision resumes that session.
- `total_cost_usd` is the session's running total. Each call records its own share as `cost_usd`; `session_cost_usd` holds the running total. Records written before this change hold the running total in `cost_usd`, so their totals are too high.

## Approvals

- An `APPROVED` file in a step folder holds the date and a fingerprint. The fingerprint covers the step's skills, library skill text (including references), the check code and the input text.
- A changed skill, input or check makes the step stale. The run refuses until the owner re-approves, or the owner decides to re-stamp the fingerprint. Do not delete approved steps or re-stamp them without the owner's decision.
- Publishing needs its own approval. Posts that link to the article wait for `## Article URL`.

## Copy rules (every copy step)

Enforced by `COPY_RULE` and `COPY_REVIEW_RULE` in `marketing/run.py`, sent to every copy writer and reviewer:

- All copy and scripts are conversational unless the task's `## Register` section says otherwise. Each copy step has a register check (sentence length, stiff words, and "you" or "I"), and the reviewer judges tone. An owner's register replaces both.
- Copy never has a disclaimer, caveat, hedge, or legal wording. Never add one, ever.
- Copy never states what the product cannot do or cannot guarantee.
- Copy never repeats a never-claim or any rule from the instructions.
- Copy never mentions the brief, the facts, the voice file, the checks, or the instructions.
- Reviewers block all of the above. A reviewer's fix removes or rewrites the copy in plain, positive terms; it never asks for a disclaimer, caveat or hedge to be added.
- No AI disclosure or avatar disclosure in copy, scripts, notes, or the character file. The Instagram "Label as made with AI" setting is done on the platform by the owner. Never state it in copy, scripts or the final package.
- No em dashes in copy.

Inputs by writers and reviewers:

- Social writers get voice, reader problems, value, and the brief's angle, keywords, channel ideas and facts. They do not get "positioning#Claims we never make".
- Reviewers of social steps get the claims list, so they can check claims against it.
- The 03-article writer does not get "positioning#Objections" (its answers carried limit sentences). The 03-article writer does get Proof, Alternatives and the brief.
- The reel-script and reel-package writers do not get the claims list either. Their reviewers do.

## Moderaty voice

- Skill: `marketing/skill-library/moderaty-voice/` (`SKILL.md` plus `references/meme-formats.md`, the joke bank). The bank is sent in the same block as its skill. It is not a separate pass.
- `brands/moderaty/voice.md` is the brand's voice only: personality, tone (funny), sentence style, words we use and never use, one example post. It holds no legal, honesty or fact directives.
- The voice skill contains no facts list, no disclaimers, no copyright or real-person rules, and no "watch out" limit notes. Product facts come from the brief and positioning.
- The brand is always written "Moderaty", in captions, scripts and subtitles. The reel script never says "Link in bio".
- Presenter rules in `character.md`: no disclosure section and no sample lines. The moderaty-voice skill supplies the voice. Never joke about threats, doxxing, self-harm, emergencies or real people.
- Stop-slop is not used in any step. It was removed because it flattened the voice. The clickbait-video skill's anti-AI-tell polish pass (its Stage 6) is not used either.

## Brand and task files

- Task files must have these sections: Deliverables, Goal, Audience, Details, Sources, Required headings, Constraints, Brand. The check is `check_task` in `marketing/run.py`.
- An optional `## Register` section sets another register for copy: one line per deliverable (article, social, images, reel, or all), such as `- article: formal`. Without it, copy is conversational.
- Start and due dates go in a `Schedule:` line. Do not put other process lines in the task (issue numbers, due dates, approval routes, "recheck after").
- Sources are URLs. No forum sites.
- Images and reel deliverables need a design system.
- Positioning file sections: One line, Audience, Reader problems, Value, Proof, Alternatives, Objections, Claims we never make, Call to action. The file checks these in order.

## Exclusions

- Do not use the voice or likeness of pluviobyte, the author of the rnskill avatar skill, in brand content.
- `seo-geo-planning` does not exist here. Do not add a skill with that name without checking.
- Do not add an avatar disclosure, an AI label, or a legal notice to any copy or script.
- Do not add the clickbait-video skill's AI and sponsorship disclosure items, its thumbnail and chapter steps, or its "disclosures set" checklist line. The pipeline publishes the title, description, caption and hashtags only after the owner approves 09-publish; the pinned comment is posted by hand.

## Skill copies

- Each `marketing/skill-library/<name>/SKILL.md` starts with a provenance line that says where it came from and when. Keep it.
- Library skills do not get reference files except where listed: moderaty-voice, and video-hooks, video-titles, video-captions and video-metadata. Those four hold copies of the clickbait-video skill's references, with the lines that conflict with the copy rules removed or changed; `marketing/skill-library/ATTRIBUTION-clickbait-video.md` keeps its MIT notice.
- The reference-file preface in `LIBRARY_PREFACE` says reference files follow the skill's `SKILL.md`.

## Tests and changes

- Every behaviour change needs a test in `tests/test_run.py`. Run the full suite before reporting done.
- Fix a bug at the root, once, and add a regression test that fails without the fix.
- Report exactly what changed and what was not run. A live campaign run costs money and needs owner approval.
