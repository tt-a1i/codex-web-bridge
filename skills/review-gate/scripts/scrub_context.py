#!/usr/bin/env python3
"""Scan a review-gate context packet for transmission risks."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    severity: str
    name: str
    pattern: re.Pattern[str]
    message: str


RULES = [
    Rule(
        "BLOCK",
        "private-key-block",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
        "Private key material must not be sent.",
    ),
    Rule(
        "BLOCK",
        "openai-api-key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
        "OpenAI-style API key detected.",
    ),
    Rule(
        "BLOCK",
        "github-token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
        "GitHub token detected.",
    ),
    Rule(
        "BLOCK",
        "npm-token",
        re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"),
        "npm token detected.",
    ),
    Rule(
        "BLOCK",
        "slack-token",
        re.compile(r"\bxox(?:b|p|o|a|r)-[A-Za-z0-9-]{20,}\b"),
        "Slack token detected.",
    ),
    Rule(
        "BLOCK",
        "google-api-key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
        "Google API key detected.",
    ),
    Rule(
        "BLOCK",
        "aws-secret-assignment",
        re.compile(r"(?i)\baws(.{0,20})?(secret|access).{0,20}=\s*['\"]?[A-Za-z0-9/+=]{30,}"),
        "AWS secret-like assignment detected.",
    ),
    Rule(
        "BLOCK",
        "bearer-token",
        re.compile(r"(?i)\bAuthorization:\s*Bearer\s+[A-Za-z0-9._~+/=-]{24,}"),
        "Bearer authorization token detected.",
    ),
    Rule(
        "BLOCK",
        "secret-assignment",
        re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|client[_-]?secret)\s*[:=]\s*['\"]?[^\s'\"`]{16,}"),
        "Secret-like assignment detected.",
    ),
    Rule(
        "WARN",
        "signed-or-sensitive-url",
        re.compile(r"https?://[^\s)>'\"]+[?&](token|key|signature|sig|secret|X-Amz-Signature)=", re.IGNORECASE),
        "URL contains a sensitive query parameter.",
    ),
    Rule(
        "WARN",
        "email-address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "Email address detected; confirm it is safe to share.",
    ),
    Rule(
        "WARN",
        "internal-hostname",
        re.compile(r"https?://[A-Za-z0-9.-]*(?:internal|corp|intranet|localhost|127\.0\.0\.1)[A-Za-z0-9.:-]*[^\s)>'\"]*", re.IGNORECASE),
        "Internal or local URL detected; confirm it is safe to share.",
    ),
]


def mask(value: str) -> str:
    compact = value.strip()
    if len(compact) <= 12:
        return "[redacted]"
    return compact[:6] + "..." + compact[-4:]


def read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8", errors="replace")


def scan(text: str) -> list[tuple[Rule, int, str]]:
    findings: list[tuple[Rule, int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule in RULES:
            match = rule.pattern.search(line)
            if match:
                findings.append((rule, line_no, mask(match.group(0))))
    return findings


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", help="Packet path, or '-' for stdin.")
    parser.add_argument(
        "--fail-on",
        choices=("never", "warn", "block"),
        default="block",
        help="Exit non-zero on findings at or above this severity.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    text = read_input(args.packet)
    findings = scan(text)
    blocks = [item for item in findings if item[0].severity == "BLOCK"]
    warns = [item for item in findings if item[0].severity == "WARN"]

    if blocks:
        status = "BLOCK"
    elif warns:
        status = "WARN"
    else:
        status = "PASS"

    print(f"Scrub status: {status}")
    print(f"Findings: {len(findings)} total, {len(blocks)} block, {len(warns)} warn")

    for rule, line_no, excerpt in findings:
        print(f"- {rule.severity} {rule.name} line {line_no}: {rule.message} Match: {excerpt}")

    if args.fail_on == "block" and blocks:
        return 2
    if args.fail_on == "warn" and findings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
