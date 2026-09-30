---
description: Work on one GitHub issue end-to-end (plan → code → tests → PR)
argument-hint: <issue-number>
allowed-tools: Bash(gh issue view:*), Bash(gh issue list:*), Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(pytest:*)
---

We are working on GitHub issue #$ARGUMENTS. Follow CLAUDE.md. Talk to me in Turkish.

1. **Understand.** Run `gh issue view $ARGUMENTS --comments`. Read the relevant parts
   of `docs/` and the existing code the issue touches. If the issue depends on an
   unfinished issue, stop and tell me.
2. **Plan.** Summarize the issue in 2–3 sentences, then give a numbered plan:
   which files change, what each step does, what tests will prove it works.
   **Stop and wait for my approval.**
3. **Branch.** `git checkout main && git pull`, then create `issue-$ARGUMENTS-<short-slug>`.
4. **Implement.** Carry out the whole plan with complete code and tests, running
   `pytest -q` as you go until everything passes.
5. **Finish.** Run the full test suite, show `git diff --stat`, propose commit
   message(s). Ask before committing, pushing, and opening the PR
   (`gh pr create`, body ending with `Closes #$ARGUMENTS`).
6. **Neden notları.** Write a short Turkish "neden" section I can paste into my
   notebook: the 3–5 key decisions in this issue and why each was made.
