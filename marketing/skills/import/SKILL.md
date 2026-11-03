> Built from no marketplace skill; written for this workflow. The owner may replace this file.

# Task importer (It's a Plan)

Read one It's a Plan issue and write it as a task file for the campaign.

Use the It's a Plan tools to read the issue, found by its key and number (for example MC-32). Only read. Never create, change, move, attach to, or comment on anything.

Read the issue's description. Read every It's a Plan document or issue the description links to. Ignore any /MODA/... link: it is an old reference that no longer exists, so do not list it anywhere.

Output the task in exactly this shape. The first line is `# Task:` followed by the title from the issue. Every section must be present and not empty. Write None. where nothing applies. The values in the example show the form only; every value you write comes from the issue.

```
# Task: Ledgerly CSV export for tax season

## Deliverables
article, social

## Goal
Get existing users to try the export before tax season.

## Audience
Freelancers who do their own taxes.

## Details
Issue: MC-32
Schedule: start 2026-11-04
Schedule: due 2026-11-20
Schedule: do not start before 2026-11-04
Page URL: /blog/tax-year-export
Product: Ledgerly, a bookkeeping app for freelancers.

## Sources
- https://example.com/help/export

## Required headings
None.

## Constraints
None.
```

Rules:
- Copy facts, URLs, dates, numbers and required headings exactly as the issue and its documents state them.
- Deliverables: only what the issue asks for. A website page or blog article is article. Social posts are social. Images and reel only when the issue asks for them. If the issue does not say which deliverables it wants, write article and add this line under Constraints: - Deliverables not stated in the issue; owner to confirm.
- Start dates, due dates and "do not start before" lines go under Details as "Schedule: ...", never under Constraints.
- If the issue asks for social, images or reel without an article, include article and add this line under Constraints: - Article added because the social posts link to it; owner to confirm.
- If the Deliverables include images or reel, write a `## Design system` section after Constraints with the body `None.`, and add this line under Constraints: - Design system not set; owner to choose one from marketing/design-systems/. Otherwise leave the section out.
- Leave out internal process lines: who does a step, approval routes, internal checklists, and notes to the team about how the work runs. Keep only what the campaign's content needs.
- Leave out any "recheck after" date or line. Keep other dates.
- The example is a shape only. Never copy its values unless the issue states them.
- Use no em dashes. Never invent a fact.
- If the tracker cannot be reached, return an empty output and say why in notes.
