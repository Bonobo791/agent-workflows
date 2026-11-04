# Review the code change

Review the change from the base commit to HEAD, against the task, the approved plan and the instructions you reviewed earlier in this session. Read the code you judge.

In order of importance:
1. Correctness: bugs, logic errors, wrong results.
2. Requirements: anything in "Done when" that the change does not deliver.
3. Security problems.
4. Broken or weakened tests: a test removed, skipped, or changed to pass.
5. Fallbacks: defaults that hide failures, silent retries, a second code path.

Mark a finding blocking only for items 1 to 5. Name the file and the line in every finding. Do not ask for style changes the task did not ask for; mark those minor.
