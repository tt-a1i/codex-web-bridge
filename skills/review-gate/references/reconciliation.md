# Reconciliation Rules

The reviewer is not the authority. The local agent must reconcile every material claim against current repo state before acting.

## Classifications

- `FIX`: The reviewer identified a real, in-scope issue and the local evidence confirms it.
- `DEFER`: The issue may be valid, but it is outside the current decision, needs a separate design pass, or is better handled later.
- `DISMISS`: The claim is incorrect, already handled, contradicted by local evidence, or too speculative to block the decision.
- `QUESTION`: The claim depends on product, architecture, data-policy, security, or scope judgment that the user must decide.

## Evidence Requirements

Every reconciled item must include at least one evidence reference:

- File path and line when a code/doc fact proves the point.
- Command plus outcome when a test, build, lint, typecheck, or repro proves the point.
- PR/review/issue link when the fact lives in live review state.
- Explicit missing evidence when the item remains unresolved.

Do not write "seems fine" or "probably okay" without evidence. If a reviewer claim cannot be verified cheaply, classify it as `QUESTION` or `DEFER`, not `DISMISS`.

## Fix Rules

Implement a `FIX` only when all are true:

- The change is inside the user's stated scope.
- The local evidence confirms the issue.
- The fix does not require product or architecture approval.
- The fix can be verified with a focused command, test, or inspection.

After fixing, rerun the relevant verification and update the action line with the new result.

## Dismissal Rules

Dismiss a reviewer item only with concrete contrary evidence. Useful dismissal patterns:

- "DISMISS: Already covered by [file:line], which handles [case]."
- "DISMISS: Test [command] covers this path and passes."
- "DISMISS: The reviewer assumed [X], but current diff only touches [Y]."

Avoid dismissing because the fix is inconvenient. Use `DEFER` or `QUESTION` instead.

## Final Gate Rule

The gate is `PASSED` only when:

- No `FIX` item remains unimplemented.
- No blocking `QUESTION` remains unanswered.
- No required verification is missing.
- The scrub gate had no unresolved `BLOCK` finding for externally transmitted context.
