# Marketing workflow integration

This module provides marketing gates and prompts. The shared runner registers the
marketing graph, persists claims and stage state, fetches a configured It's a
Plan task, resolves role skills and dispatches isolated model sessions through
trusted interfaces. The local entrypoint fixture exercises these paths with
synthetic services. Follow `PROMPTS.md` and `stage-result.schema.json` for
structured handoffs. No private skills or user content belong in this repository.

The graph fans out after the approved canonical version. Assembly depends on all
channel reviews and asset production reaching a **terminal** state, not
necessarily passing: blocked or unsuitable channels still appear in the review
package. The runner records stage outcomes, scoped invalidations, bounded
attempts and fresh reviewer sessions; it does not automatically publish. Missing
configured role skills block model work. Supplied skill hashes identify the
instructions passed to a session, but do not independently prove model
compliance. Markdown page headings are checked against explicitly configured,
version-bound format rules; semantic claim checking still requires independent
review.

Reddit review can pass only with fresh, draft-bound community context and rule
artifacts authenticated by an independent trusted authority. Missing evidence
blocks the channel, an irrelevant community is unsuitable, and product mentions
require affiliation disclosure. The evidence is rechecked before assembly.
No real Reddit authority is configured by default; fixture callbacks do not
establish current community rules or user permission to post.

## OpenFilm boundary

The opt-in OpenFilm stage accepts a versioned brief, stores an editable project,
validates rendered files and requires a separate subject-bound media review.
`openfilm_status` remains a handoff guard, not file verification. The default
worker does not configure the trusted builder, platform authority, isolated
render supervisor or independent media reviewer, so media remains blocked until
those boundaries are supplied. See [OPENFILM.md](OPENFILM.md) for tested and
untested behavior. Paid generation and publication are not automatic.

## Remaining integration

The default setup still requires private role skills and format rules, trusted
source and Reddit authorities, and an approved runtime and credential-delivery
boundary. Tests use synthetic authorities; they do not verify real source
accuracy, current community rules, container isolation or subscription access.
See [implementation status](../../IMPLEMENTATION_STATUS.md) and
[pilot setup](../../docs/PILOT_SETUP.md) before using real tasks or media.
