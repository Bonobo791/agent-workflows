# Write the implementation instructions

Turn the approved plan into phased instructions. Another agent follows them one phase at a time, and commits after each phase.

## Format (a program checks it)
- Start with a `## Rules` section: no fallbacks, standard library only unless the task says otherwise, do only what the phase says, never push.
- Then one `## Phase <n>: <title>` section per phase, numbered 1, 2, 3 with no gap.
- Each phase section has exactly one line `Commit message: <text>`. The text is at most 72 characters, in the imperative, and different from every other phase's message.
- Each phase has, in this order: what to change and in which file; which tests to write or change and what each one proves; the line `Check: run the check commands; they must pass.`; and the commit message line.

## Size and order
- Each phase is small enough that its commit is one change a reviewer can read at once.
- After each phase the check commands must pass.
- Order the phases so each one builds on the commits before it.

## Content
- Name the files and the functions. Do not describe code you did not read.
- Add nothing the approved plan does not contain.
