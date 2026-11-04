# Build one phase

Do only the phase you are asked to do, in the worktree (your working directory), on the task's branch.

## Steps
1. Read the phase and the files it names before you edit anything.
2. Make the change the phase describes, and write the tests it names.
3. Run every check command from the task before you commit. Run the same commands the task lists.
4. Commit once, with exactly the commit message you were given. Stage only the files of this phase.
5. Report.

## Rules
- Work only on the current phase. Do not start the next one.
- Never push. Never change git config. Never amend a commit that has been reviewed.
- Never skip, disable or weaken a test to make a check pass.
- No fallbacks: no default that hides a failure, no retry, no second code path.
- If the phase cannot be done as written, say so in your report and do not commit.
- Your working directory is a git worktree of the task's repository, on the task's branch. Work only there, even though the task names the main checkout.
- Run each check command exactly as the task writes it, as its own Bash call.

## Report
Answer through the output field, in three short parts: the files changed, the tests added or changed, and the result of each check command.
