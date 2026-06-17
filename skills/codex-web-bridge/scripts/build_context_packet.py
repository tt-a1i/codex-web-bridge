#!/usr/bin/env python3
"""Build a bounded Markdown context packet for codex-web-bridge."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
import subprocess
import sys
from typing import Iterable


BRIDGE_PURPOSES = {
    "planning",
    "review",
    "debugging",
    "architecture",
    "implementation",
    "research",
    "custom",
}

LEGACY_PURPOSES = {
    "plan-hardening": "planning",
    "implementation-review": "review",
    "pr-comment-resolution": "review",
    "eval-methodology": "review",
}

TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".css",
    ".go",
    ".h",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


def run(cmd: list[str], cwd: Path, timeout: int = 10) -> str:
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"[command failed: {' '.join(cmd)}: {exc}]"

    output = completed.stdout.strip()
    error = completed.stderr.strip()
    if completed.returncode != 0:
        details = error or output or f"exit {completed.returncode}"
        return f"[command failed: {' '.join(cmd)}: {details}]"
    return output


def git_ok(repo: Path) -> bool:
    return not run(["git", "rev-parse", "--is-inside-work-tree"], repo).startswith(
        "[command failed:"
    )


def repo_root(repo: Path) -> Path:
    root = run(["git", "rev-parse", "--show-toplevel"], repo)
    if root.startswith("[command failed:"):
        return repo.resolve()
    return Path(root).resolve()


def guess_base(repo: Path) -> str:
    upstream = run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        repo,
    )
    if upstream and not upstream.startswith("[command failed:"):
        return upstream

    for candidate in ("origin/main", "origin/master", "main", "master"):
        ok = run(["git", "rev-parse", "--verify", "--quiet", candidate], repo)
        if ok and not ok.startswith("[command failed:"):
            return candidate
    return ""


def truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit].rstrip() + f"\n\n[truncated: {omitted} chars omitted]"


def code_block(label: str, body: str) -> str:
    safe = body.strip() or "[none]"
    return f"### {label}\n\n```text\n{safe}\n```\n"


def existing_paths(repo: Path, paths: Iterable[str]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = (repo / raw).resolve()
        try:
            path.relative_to(repo)
        except ValueError:
            continue
        if path.exists() and path.is_file() and path not in seen:
            result.append(path)
            seen.add(path)
    return result


def discover_constraint_files(repo: Path) -> list[str]:
    candidates = [
        "AGENTS.md",
        "CLAUDE.md",
        "CONTEXT.md",
        "README.md",
        "DESIGN.md",
        "CONTRIBUTING.md",
        "docs/adr",
        "docs/ADRs",
    ]
    found: list[str] = []
    for candidate in candidates:
        path = repo / candidate
        if path.is_file():
            found.append(candidate)
        elif path.is_dir():
            for child in sorted(path.glob("*.md"))[:8]:
                found.append(str(child.relative_to(repo)))
    return found


def read_text_excerpt(path: Path, max_chars: int) -> str:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return "[skipped non-text-like file]"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"[failed to read: {exc}]"
    if b"\x00" in data:
        return "[skipped binary-looking file]"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    return truncate(text, max_chars)


def build_packet(args: argparse.Namespace) -> str:
    repo = repo_root(Path(args.repo).expanduser().resolve())
    is_git = git_ok(repo)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    base = args.base or (guess_base(repo) if is_git else "")
    repo_label = str(repo) if args.include_repo_path else repo.name
    purpose_arg = args.purpose or args.mode or "custom"
    purpose = LEGACY_PURPOSES.get(purpose_arg, purpose_arg)
    question = args.question or args.decision

    sections: list[str] = [
        "# Codex Web Bridge Packet",
        "",
        "## Bridge Request",
        "",
        f"- Provider: `{args.provider}`",
        f"- Purpose: `{purpose}`",
        f"- Question: {question}",
        f"- Repo: `{repo_label}`",
        f"- Generated: `{now}`",
        f"- Base: `{base or '[not detected]'}`",
        f"- Scope: {args.scope or '[fill in before sending]'}",
        f"- Out of scope: {args.out_of_scope or '[fill in before sending]'}",
        f"- Desired response: {args.desired_response or '[answer the question directly and call out uncertainty]'}",
        "",
        "## Local State",
        "",
    ]

    if is_git:
        branch = run(["git", "branch", "--show-current"], repo)
        head = run(["git", "rev-parse", "--short", "HEAD"], repo)
        status = run(["git", "status", "--short", "--branch"], repo)
        sections.extend(
            [
                f"- Branch: `{branch or '[detached]'}`",
                f"- HEAD: `{head}`",
                "",
                code_block("Git status", status),
            ]
        )
    else:
        sections.extend(["- Not a Git repository.", ""])

    constraints = discover_constraint_files(repo)
    sections.extend(
        [
            "## Candidate Repo Constraints",
            "",
            *(f"- `{item}`" for item in constraints),
            "" if constraints else "- [none detected]",
            "",
        ]
    )

    if is_git:
        if base:
            sections.append(code_block(f"Diff stat ({base}...HEAD)", run(["git", "diff", "--stat", f"{base}...HEAD"], repo)))
            sections.append(code_block(f"Changed files ({base}...HEAD)", run(["git", "diff", "--name-status", f"{base}...HEAD"], repo)))
            base_diff = run(
                ["git", "diff", "--find-renames", "--no-ext-diff", "--unified=40", f"{base}...HEAD"],
                repo,
                timeout=20,
            )
            sections.append(code_block(f"Bounded diff ({base}...HEAD)", truncate(base_diff, args.max_diff_chars)))

        staged = run(["git", "diff", "--cached", "--stat"], repo)
        unstaged = run(["git", "diff", "--stat"], repo)
        sections.append(code_block("Staged diff stat", staged))
        sections.append(code_block("Unstaged diff stat", unstaged))

        working_diff = run(
            ["git", "diff", "--cached", "--find-renames", "--no-ext-diff", "--unified=40"],
            repo,
            timeout=20,
        )
        unstaged_diff = run(
            ["git", "diff", "--find-renames", "--no-ext-diff", "--unified=40"],
            repo,
            timeout=20,
        )
        sections.append(code_block("Bounded staged diff", truncate(working_diff, args.max_diff_chars)))
        sections.append(code_block("Bounded unstaged diff", truncate(unstaged_diff, args.max_diff_chars)))

        untracked = run(["git", "ls-files", "--others", "--exclude-standard"], repo)
        sections.append(code_block("Untracked files", untracked))
        untracked_paths = [line.strip() for line in untracked.splitlines() if line.strip()]
        if untracked_paths:
            sections.extend(["## Bounded Untracked File Excerpts", ""])
            for path in existing_paths(repo, untracked_paths[: args.max_untracked_files]):
                rel = path.relative_to(repo)
                excerpt = read_text_excerpt(path, args.max_file_chars)
                sections.append(f"### `{rel}`\n\n```text\n{excerpt}\n```\n")
            if len(untracked_paths) > args.max_untracked_files:
                sections.append(
                    f"[truncated: {len(untracked_paths) - args.max_untracked_files} untracked files not excerpted]\n"
                )

    evidence_paths = existing_paths(repo, args.evidence_file)
    if evidence_paths:
        sections.extend(["## Selected Evidence Files", ""])
        for path in evidence_paths:
            rel = path.relative_to(repo)
            excerpt = read_text_excerpt(path, args.max_file_chars)
            sections.append(f"### `{rel}`\n\n```text\n{excerpt}\n```\n")

    sections.extend(
        [
            "## Verification Already Run",
            "",
            args.verification or "[fill in commands and outcomes before sending]",
            "",
            "## Known Failures / Extra Context",
            "",
            args.open_questions or "[fill in before sending]",
            "",
            "## Transmission Notes",
            "",
            "- Run `scrub_context.py` before sending externally.",
            "- Remove unrelated private data, secrets, customer data, and local-only machine details.",
            "- The target web model should answer; Codex will only transport and return the response unless the user asks Codex to continue.",
            "",
        ]
    )
    return "\n".join(sections)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository path to inspect.")
    parser.add_argument("--provider", default="chatgpt", help="Target provider label, such as chatgpt, claude, grok, gemini, or other.")
    parser.add_argument("--purpose", choices=sorted(BRIDGE_PURPOSES), help="Why this packet is being sent.")
    parser.add_argument("--question", default="", help="Exact question for the target web model.")
    parser.add_argument(
        "--mode",
        choices=sorted(BRIDGE_PURPOSES | set(LEGACY_PURPOSES)),
        help="Deprecated alias for --purpose. Legacy review-gate modes are mapped to bridge purposes.",
    )
    parser.add_argument("--decision", default="", help="Deprecated alias for --question.")
    parser.add_argument("--base", default="", help="Base branch/ref. Auto-detected when omitted.")
    parser.add_argument("--scope", default="", help="In-scope context for this bridge request.")
    parser.add_argument("--out-of-scope", default="", help="Explicitly excluded areas.")
    parser.add_argument("--desired-response", default="", help="Requested answer format or level of detail.")
    parser.add_argument(
        "--evidence-file",
        action="append",
        default=[],
        help="Repo-relative file to include as bounded evidence. Repeatable.",
    )
    parser.add_argument("--verification", default="", help="Commands already run and outcomes.")
    parser.add_argument("--open-questions", default="", help="Known failures or open questions.")
    parser.add_argument("--max-diff-chars", type=int, default=60000)
    parser.add_argument("--max-file-chars", type=int, default=12000)
    parser.add_argument("--max-untracked-files", type=int, default=20)
    parser.add_argument(
        "--include-repo-path",
        action="store_true",
        help="Include the local absolute repo path in the packet. Omitted by default to reduce leakage.",
    )
    parser.add_argument("--output", default="", help="Write packet to this path instead of stdout.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not (args.question or args.decision):
        raise SystemExit("error: --question is required")
    packet = build_packet(args)
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(packet, encoding="utf-8")
        print(output)
    else:
        sys.stdout.write(packet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
