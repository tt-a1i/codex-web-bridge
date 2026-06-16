---
name: review-gate
description: Run a structured second-pass review gate before coding, shipping, merging, responding to PR/code-review comments, or publishing evaluation/reporting conclusions. Use when the user asks for /review-gate, second-pass review, Pro or Extended Pro review, plan hardening, implementation review, ship readiness review, PR comment resolution, CodeRabbit or reviewer cleanup validation, eval methodology review, reporting methodology review, or an external review that must be reconciled against local repo evidence before acting.
---

# Review Gate

Use this skill to turn an external or local second-pass review into a scoped, scrubbed, evidence-backed gate. The reviewer can inform the decision, but local repo evidence decides what gets fixed, deferred, dismissed, or escalated to the user.

## Non-Negotiables

- Treat external reviewer output as advisory. Verify every material claim against local files, diffs, PR state, tests, and repo conventions.
- Do not transmit a context packet with `BLOCK` scrub findings. Remove or summarize the sensitive material, then rerun the scrub gate.
- Before sending private or sensitive repo context to an external reviewer, confirm the user has authorized that exact destination and data class.
- For Extended Pro or ChatGPT Pro interactions, use the Codex in-app browser through the Browser Use skill when available. Do not substitute Computer Use, shell-launched Playwright, macOS `open`, or an unrelated browser path for Pro submission.
- Ask the user before implementing product, architecture, data-policy, pricing, security-posture, or scope-expanding tradeoffs raised by a reviewer.
- Preserve traceability: summarize the sent prompt, the reviewer verdict, the reconciliation decisions, and the verification commands.

If the approved reviewer backend is unavailable after normal troubleshooting, run only a local dry-run review or report the blocker. Do not silently downgrade to an unapproved backend.

## Review Types

Choose one review type before building context:

- `plan-hardening`: Review strategy, phase order, missing decisions, unclear assumptions, and feasibility before implementation.
- `implementation-review`: Review code diffs, tests, regressions, edge cases, and ship readiness.
- `pr-comment-resolution`: Review whether PR comments, CodeRabbit findings, or reviewer threads were resolved correctly.
- `eval-methodology`: Review metrics, datasets, comparability, reporting validity, recommendation criteria, and confounders.

## Workflow

1. Define the review contract.
   - Write the exact decision being gated.
   - State the review type, intended backend, repo path, branch/base branch, and what is out of scope.
   - State whether the reviewer is allowed to block shipping or only provide advisory feedback.

2. Build the context packet.
   - Prefer the bundled builder when working in a Git repo:

```bash
python3 /path/to/review-gate/scripts/build_context_packet.py \
  --repo "$PWD" \
  --mode implementation-review \
  --decision "Decide whether this diff is ready to ship" \
  --output /tmp/review-gate-context.md
```

   - Add missing human context manually when needed: product goal, issue links, PR review threads, commands already run, known failures, and open questions.
   - Keep the packet scoped. Include enough evidence for reasoning; do not paste unrelated repo areas.
   - Read `references/prompt-templates.md` when composing the final reviewer prompt.

3. Run the scrub gate.

```bash
python3 /path/to/review-gate/scripts/scrub_context.py \
  /tmp/review-gate-context.md \
  --fail-on block
```

   - `PASS`: safe to proceed from the scanner's perspective, subject to user authorization.
   - `WARN`: review findings and decide whether to summarize, redact, or ask the user.
   - `BLOCK`: do not send externally until fixed and rescanned.

4. Submit to the reviewer backend.
   - For Extended Pro, first read and follow the Browser Use skill, initialize the in-app browser runtime, reuse a relevant Pro thread when it preserves useful context, or start a clean thread when the workstream is unrelated or stale.
   - Verify the visible model/backend when possible.
   - Ask the reviewer to answer using the output contract from `references/prompt-templates.md`.
   - If no approved external backend is available, run a local-only review and label it as such.

5. Wait and capture the full response.
   - Do not abandon a slow Pro response merely because it takes 10-15 minutes.
   - If login, auth, browser interruption, or model access blocks the review, report the specific blocker and wait for user direction.

6. Reconcile locally.
   - Read `references/reconciliation.md` for non-trivial responses.
   - Classify each actionable reviewer item as `FIX`, `DEFER`, `DISMISS`, or `QUESTION`.
   - For each classification, attach local evidence: file path, command output summary, PR/review link, test result, or explicit missing evidence.
   - Implement `FIX` items only when clearly in scope and not blocked by user approval.

7. Report the gate result.

Use this shape:

```text
Review gate: PASSED | BLOCKED | LOCAL-ONLY | INCOMPLETE
Reviewer backend: [Extended Pro | local-only | other approved backend]
Review type: [plan-hardening | implementation-review | pr-comment-resolution | eval-methodology]

Reviewer verdict:
- [SIGNED OFF | BLOCKED | unclear]

Reconciled actions:
- FIX: [action] Evidence: [file/command/link]
- DEFER: [reason] Evidence: [file/command/link]
- DISMISS: [reason] Evidence: [file/command/link]
- QUESTION: [direct user question] Evidence: [file/command/link]

Local verification:
- [commands run and outcomes]

Traceability:
- Context packet summary: [what was sent, with notable redactions]
- Response summary: [reviewer claims in brief]
```

`PASSED` means no blocking reviewer item remains after local reconciliation. `BLOCKED` means at least one required change or unresolved question should stop the stated decision. `LOCAL-ONLY` means no external reviewer was used. `INCOMPLETE` means the review could not finish because of access, auth, browser, context, or user-decision blockers.

## Resources

- `scripts/build_context_packet.py`: Generate a bounded Markdown packet from Git state, diffs, selected evidence files, and review metadata.
- `scripts/scrub_context.py`: Scan a packet for obvious secrets and sensitive transmission risks before external submission.
- `references/prompt-templates.md`: Reviewer prompt contracts for the four supported review types.
- `references/reconciliation.md`: Evidence rules and classification guidance for reviewer feedback.
