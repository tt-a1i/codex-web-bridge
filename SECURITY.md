# Security

`codex-web-bridge` has two very different trust modes:

- Bridge Mode packages local context and sends it through a browser or manual
  handoff. The web model cannot call local tools.
- MCP Connector Mode lets a web MCP host call local workspace tools. Treat it
  as a local network service with access to the roots you allow.

## Safer Defaults

- The connector defaults to `trust_level=readonly`.
- Allowed roots must be explicit project paths, not broad directories such as
  `/` or `~`.
- Public connector use should use the OAuth owner approval flow.
- Non-loopback hosts require an owner token.
- `trust_level=execute` requires an owner token.
- Workspace paths are containment-checked and reject absolute paths, `..`, and
  final symlink escapes.
- Audit and session state omit file bodies, patch bodies, shell commands, shell
  output, and pull request bodies.
- Apps metadata keeps compact counts and statuses instead of duplicating
  sensitive content.

## Hard Rules

- Do not run a no-auth connector on a public tunnel except for a short readonly
  smoke test.
- Do not use `trust_level=execute` with a connector URL you do not control.
- Do not expose broad roots such as a home directory, secrets directory, or
  monorepo parent that includes unrelated private projects.
- Prefer `write_mode=handoff` for first-time ChatGPT use. It lets the model save
  review notes and edit plans without changing source files.
- Prefer `shell_mode=safe` or `shell_mode=off` unless the host genuinely needs
  arbitrary bounded Bash.

## Mode Boundaries

`trust_level` is the upper bound:

- `readonly`: read/search/list/git diff/status, patch preview, state views.
- `review`: readonly plus review notes and edit plans under connector state.
- `execute`: review plus source mutation, worktree, PR, publish, and shell tools
  when the narrower modes allow them.

Additional modes narrow that upper bound:

- `tool_mode=minimal|standard|full`: controls visible tool surface.
- `write_mode=off|handoff|workspace`: controls source mutation and Git/PR
  mutation tools.
- `shell_mode=off|safe|full`: controls whether shell is hidden, allowlisted, or
  the full bounded Bash tool.

These guards reduce risk, but they are not an operating-system sandbox. For
strong isolation, run the connector inside a VM, container, or separate user
account with only the intended project mounted.

## Reporting

Please report security issues privately to the repository owner. Include the
connector version, relevant config shape with secrets removed, reproduction
steps, and the expected impact.
