# Issue tracker

Work for this repo is tracked on the GitHub Project board **Miare board**
(`github.com/users/knoguchi/projects/3`), drained by `/super-board run`.

Columns: Backlog · Ready · Building · QA · Review · Blocked · Done.
Labels route a card: `qa` skips Building · `bug`, `feature`, or no label is built first.

## Ticket format

```markdown
## Problem
<1–3 sentences: what is wrong or missing, from the user's side. No implementation plan.>

## Context
- **Where:** <page>, `<route>`
  - a. <step>
  - b. <step>
- **Who:** <who sees it>

![<page> · desktop](https://github.com/knoguchi/miare/raw/<sha>/<path>.png)

## Fix
<the intended change in 1–3 lines>

## Acceptance Criteria
- [ ] <checkable outcome a test can assert>
- [ ] <checkable outcome>

## Risk
🟢 **Low** · <one line>

## Blocked by
- #<N> — <why>          ← or exactly: - None.
```

| Rule | Value |
|---|---|
| Title | `<emoji> [<kind>] <scope>: <what>` · ≤ 70 chars · no ticket number · e.g. `🐛 [bug] counterpoint: beam search drops the final measure` |
| Context | lettered steps under Where, ONE per line · NEVER arrows · screenshot embedded for UI, NEVER linked |
| Bugs | add `## Evidence`: the folded 12-row table (writing-standard.md § 3) · a missing row says `n/a — why` |
| Acceptance Criteria | 2–5 `- [ ]` lines · each checkable · NEVER "works well", "is fast" |
| Risk | 🟢 Low · 🟡 Medium · 🔴 High + one line |
| Blocked by | `- #N — why` bullets, or a bare `- None.` · NEVER "None — but #N first" |
| Labels | `money`, `auth`, `schema` route the merge to a human (merge_policy) · `schema` = destructive only (DROP/TRUNCATE/RENAME); additive migrations need no label |
| Human-only step | a `needs-you: <command>` line in the PR body → 🙋 Blocked until done |
| Size | one card = one PR under 400 changed lines (`merge_policy.auto_max_lines`) · bigger → split with `/to-tickets` |
