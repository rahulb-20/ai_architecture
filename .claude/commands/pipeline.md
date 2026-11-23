---
description: Run a change through the architect, developer and tester agents and open a pull request
argument-hint: <task description>
---

Run this task through the three-agent pipeline: **$ARGUMENTS**

You are the orchestrator. You do not design, write code or write tests yourself. Run the stages in order, one agent at a time, each in the foreground.

1. **Prepare.** `git checkout main && git pull`. Confirm the working tree is clean. Pick a short kebab-case slug for the task.
2. **Architect.** Launch the `ai-architect` agent with the task and the slug. It returns a design brief path and a branch name. Read the brief. If it says the task is already done or should not be done, stop and report that.
3. **Developer.** Launch the `developer` agent with the brief path and the branch name only.
4. **Tester.** Launch the `tester` agent with the brief path and the branch name only. Do not pass it the developer's report, so its judgement is independent.
5. **Rework.** If the verdict is CHANGES_REQUESTED, send the tester's findings to the `developer` agent, then run the `tester` again. At most two rework rounds. If the verdict is still not APPROVE, continue to step 6 and open the pull request as a draft.
6. **Verify yourself.** On the branch, run the four checks from `CLAUDE.md` once more and read `git diff main...<branch> --stat`. Do not trust a reported pass you have not reproduced.
7. **Pull request.** Push the branch and open a pull request to `main` (`gh pr create`, or `gh api repos/<owner>/<repo>/pulls` where GraphQL is not available). The description has: the problem in two sentences, a link to the design brief, the acceptance-criteria table from the tester, the tester's verdict and open findings, and the check results.
8. **Stop.** Do not merge. Wait for CI, then report to the user: the pull request link, the verdict, CI result, how many rework rounds were needed, and anything the agents flagged as out of scope.

Merge only if the user tells you to in this conversation.
