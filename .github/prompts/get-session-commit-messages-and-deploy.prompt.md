---
description: "Commit the session's uncommitted work as ordered logical commits, then push (permission assumed)"
agent: "agent"
---
Review the **entire current conversation**, group the work done this session that is **not yet committed** into
logically ordered commits, then **stage, commit, and push them yourself**. Invoking this prompt is the user's explicit
permission to run `git add` / `git rm` / `git mv` / `git commit` / `git push` for this session's work — no further
confirmation needed.

## Steps

1. **Inventory the session's work.** Scan the whole conversation for every file created, edited, renamed, or
   deleted, and note the purpose of each so related changes can be grouped.
2. **Reconcile against git — don't trust the conversation alone.** Run `git --no-pager status`,
   `git --no-pager log --oneline -20`, and `git --no-pager diff <path>` where in doubt. Include only files that are
   genuinely still uncommitted; drop anything already committed or reverted. A not-yet-staged rename shows as the old
   path deleted plus the new path untracked — commit those two together.
3. **Exclude non-check-in artifacts.** Skip scratch/generated files (anything under `temp/`, ad-hoc logs, one-off
   outputs) and anything the user said earlier not to commit. If a file's intent is ambiguous, **leave it
   uncommitted** and flag it in the report — never guess it in.
4. **Group into coherent, dependency-ordered commits.** One logical change per commit; prerequisite changes (shared
   code) before their consumers. Keep a change and the tests it needs to pass in the same commit.
5. **Match the repo's commit-message style.** Mirror the prevailing convention in `git log` (prefix/scope, tense,
   casing). Subject near 50 characters; body wrapped at 120. Plain ASCII, double-quoted, escaping any `"`, `` ` ``,
   `$`, or `\`.
6. **Run the quality gate first.** Run `make qa` (plus `make build-docs-strict` if anything under `docs/` changed). If
   either fails, stop and report — do not commit.

## Committing

For each group, in order:

- **Check the index is clean first.** `git --no-pager diff --cached --name-only` must be empty (or match only this
  group). Anything already staged rides along into the next commit, so unstage strays with `git restore --staged`.
- **Stage every path explicitly** — `git add <path>` for created/modified files, `git rm <path>` for deletions (and
  the old side of a rename). Never use `git add .` or `git add -A`.
- **Commit with a single `git commit -m "..."`** — subject, a blank line, then the body, all inside the same quotes.
  Pre-commit hooks run on commit. If an auto-fixing hook (`ruff-format`, `end-of-file-fixer`, `trailing-whitespace`)
  modifies files, re-stage the same paths and retry once. If a hook fails for any other reason, stop and report.

## Pushing

- Run `git fetch`, then push the current branch to its upstream with a plain `git push`.
- If the remote has commits you don't have (behind or diverged), **stop and report** — don't rebase, merge, or pull
  automatically.

## Never

- Force-push, use `--no-verify`, amend or rewrite commits that are already pushed, or push to any branch other than
  the current branch's upstream.
- Commit files outside this session's work, even if they show up as modified.

## Report

Reply with a short table of the commits made (short hash + subject + files), the push result (`old..new  branch`),
and any files deliberately left uncommitted with the reason. If nothing session-related remains uncommitted, say so
in one line and push nothing.
