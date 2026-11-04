# Plan the change

Write the plan for the task in 00-task.md. The owner approves the plan before any code is written, so the plan must say exactly what will change.

## Rules
- Read the code first. List the files you read at the top of Approach.
- One approach, not options. If two approaches are possible, pick the one with fewer changed files and say why in Approach.
- No fallbacks in the design: no default that hides a failure, no silent retry, no second path for the same job.
- Standard library only, unless the task names a dependency.
- Never fill a gap with a guess. If the task does not settle something, write it under Open questions.

## Sections
Use exactly these `##` sections, in this order.

- Summary: what will change and why, in three or four sentences.
- Approach: the design. The first lines list the files you read. Then the approach in short paragraphs.
- Files to change: one line per file, `path: what changes`. Include new files and test files.
- Tests: which tests are added or changed, and what each one proves.
- Risks: what could break, including callers and stored data.
- Open questions: anything the task does not settle. Write `None.` only when nothing is open.
