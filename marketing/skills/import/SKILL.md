> Built from no marketplace skill; written for this workflow. The owner may replace this file.

# Task importer (It's a Plan)

Read one It's a Plan issue and write it as a task file for the campaign.

Use the It's a Plan tools to read the issue, found by its key and number (for example MC-32). Only read. Never create, change, move, attach to, or comment on anything.

Read the issue's description. Read every It's a Plan document or issue the description links to. Ignore any /MODA/... link: it is an old reference that no longer exists, so do not list it anywhere.

Output the task in exactly this shape. The first line is `# Task:` followed by the title from the issue. Every section must be present and not empty. Write None. where nothing applies.

```
# Task: <title from the issue>

## Deliverables
<comma-separated: article, social, images, reel>

## Goal
<one or two sentences>

## Audience
<who it is for>

## Details
Issue: <key and number, for example MC-32>
Schedule: <start dates, due dates and "do not start before" lines from the issue, one per line, or None.>
Page URL: <the page the article is for, or None.>
<other facts from the issue, one per line>

## Sources
<one "- " line per source URL the issue names, or None.>

## Required headings
<None. or one "- " line per heading, in the order the SOP gives them>

## Constraints
<rules the issue sets, one per line, or None.>

## Design system
None.
```

Rules:
- Copy facts, URLs, dates, numbers and required headings exactly as the issue and its documents state them.
- Deliverables: only what the issue asks for. A website page or blog article is article. Social posts are social. Images and reel only when the issue asks for them. If the issue does not say which deliverables it wants, write article and add this line under Constraints: - Deliverables not stated in the issue; owner to confirm.
- Start dates, due dates and "do not start before" lines go under Details as "Schedule: ...", never under Constraints.
- If the issue asks for social, images or reel without an article, include article and add this line under Constraints: - Article added because the social posts link to it; owner to confirm.
- If the Deliverables include images or reel, add this line under Constraints: - Design system not set; owner to choose one from marketing/design-systems/.
- Use no em dashes. Never invent a fact.
- If the tracker cannot be reached, return an empty output and say why in notes.
