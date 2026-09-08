# Repository Agent Instructions

## Commit ownership

Agents are responsible for leaving completed work committed, not merely present in the working tree.

- Treat each coherent LLM-authored unit of work as a commit boundary. After implementing and validating a unit, commit it automatically without waiting for the user to ask.
- Before editing and again before committing, inspect `git status` and the relevant diffs so pre-existing work is not mistaken for agent-authored work.
- Stage explicit paths (or carefully selected hunks). Do not use broad staging commands that could capture unrelated work.
- Use a concise imperative commit subject that describes the unit. Do not amend, rebase, squash, push, or otherwise rewrite history unless the user explicitly requests it.
- Do not commit unfinished or known-broken agent work. If validation cannot run, commit only when the change is otherwise complete and state what was not validated.

## Human-authored HTML

Human edits to tracked `*.html` files are intentional project work and must not be left behind indefinitely just because the agent did not author them.

- Preserve all pre-existing human HTML edits. Never revert or overwrite them to obtain a clean tree.
- Inspect their diff and include them in the next commit when they belong to the same coherent change as the agent's work.
- If they are unrelated to the agent's unit, make a separate commit for the HTML edits with an accurate message. Do this automatically once the edits form a coherent, reviewable change.
- If an HTML file contains both human and agent edits that cannot be separated safely, commit the complete file and mention that shared ownership in the handoff.
- A clearly incomplete HTML edit (for example, conflict markers, a truncated element, or an explicit work-in-progress note) should remain uncommitted; report it instead of guessing at the intended completion.

## Commit safety

- Run the most relevant available checks before committing. For narrated-tour content, follow the validation commands in `README.md` when the required Megatron checkout is available.
- For changes to `*.html`, `tour.js`, or `tour.css`, run the repository's
  `shot.py` headless-browser check exactly as documented in `README.md`. Headless
  Chrome runs without a GUI or display server in agent environments. Do not
  replace it with a DOM shim; if it fails, report the command's actual error and
  follow the browser-install instructions in `README.md`.
- Never include secrets, credentials, editor artifacts, or unrelated non-HTML changes in a commit.
- At handoff, report each commit created and any changes intentionally left uncommitted.
