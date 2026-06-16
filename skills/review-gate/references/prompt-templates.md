# Prompt Templates

Use these templates after building and scrubbing the context packet. Keep the final prompt scoped to the decision being gated.

## Output Contract

Ask the reviewer to respond in this shape:

```text
Verdict: SIGNED OFF | BLOCKED

Required changes:
- ...

Risks:
- ...

Tests or verification:
- ...

Reasoning notes:
- ...
```

`SIGNED OFF` means the reviewer sees no blocking issue for the stated decision. `BLOCKED` means the reviewer found at least one required change or unresolved question that should stop the decision.

## Plan Hardening

```text
You are reviewing this plan before implementation.

Decision to review:
[exact decision]

Context packet:
[scrubbed branch, PR/issue links, plan docs, repo constraints, known failures, open questions]

Please answer using the required output contract:
- Verdict: SIGNED OFF or BLOCKED
- Required changes
- Risks
- Tests or verification
- Reasoning notes

Focus on whether the plan reaches the stated workflow goal, which decisions are missing, which assumptions are weak, and where scope should be reduced before coding.
```

## Implementation Review

```text
You are reviewing this implementation for ship readiness.

Decision to review:
[exact decision]

Context packet:
[scrubbed branch, PR/issue links, diff summary, changed files, key snippets, tests run, known failures, repo patterns]

Please answer using the required output contract:
- Verdict: SIGNED OFF or BLOCKED
- Required changes
- Risks
- Tests or verification
- Reasoning notes

Focus on correctness, edge cases, regressions, missing tests, hidden coupling, and whether the diff is shippable for the stated scope.
```

## PR / Code-Review Comment Resolution

```text
You are reviewing whether PR/code-review comments were resolved correctly.

Decision to review:
[exact decision]

Context packet:
[scrubbed PR URL, branch, review comments, responses/fixes, relevant diffs, tests run, unresolved questions]

Please answer using the required output contract:
- Verdict: SIGNED OFF or BLOCKED
- Required changes
- Risks
- Tests or verification
- Reasoning notes

Focus on whether any response or fix is weak, incomplete, inconsistent with the repo, likely to fail review, or only superficially addresses the comment.
```

## Eval / Reporting Methodology

```text
You are reviewing this evaluation or reporting methodology.

Decision to review:
[exact decision]

Context packet:
[scrubbed metrics, datasets or reports, methodology, assumptions, comparison criteria, known limitations, relevant code or queries]

Please answer using the required output contract:
- Verdict: SIGNED OFF or BLOCKED
- Required changes
- Risks
- Tests or verification
- Reasoning notes

Focus on whether the metrics are comparable enough to support the recommendation, which confounders remain, what evidence is missing, and whether the report overclaims.
```
