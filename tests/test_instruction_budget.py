"""Size limits for the instruction files Claude Code loads in this repo.

An instruction file that keeps growing eats the context every session starts
with, so the limits are enforced here instead of by memory. When one of these
fails, move detail out of the file (history, examples and version numbers do
not belong in it) or split the topic. Never raise a cap to pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Characters, not bytes.
CLAUDE_MD_CAP = 20_000
RULE_FILE_CAP = 14_000


def _over_budget(root: Path) -> list[str]:
    """Return one line per instruction file under ``root`` that is past its cap."""
    checks = [(root / "CLAUDE.md", CLAUDE_MD_CAP)]
    checks += [
        (path, RULE_FILE_CAP)
        for path in sorted((root / ".claude" / "rules").glob("**/*.md"))
    ]
    problems = []
    for path, cap in checks:
        if not path.is_file():
            continue
        size = len(path.read_text(encoding="utf-8"))
        if size > cap:
            problems.append(f"{path.relative_to(root).as_posix()}: {size} > {cap}")
    return problems


def test_instruction_files_are_within_budget() -> None:
    """CLAUDE.md stays within 20k characters and each rule file within 14k."""
    # CLAUDE.md is gitignored, so a fresh clone (CI included) has nothing to
    # measure. Say so instead of passing silently.
    if not (REPO / "CLAUDE.md").is_file() and not (REPO / ".claude" / "rules").is_dir():
        pytest.skip("no instruction files in this checkout")
    assert _over_budget(REPO) == []


def test_the_check_fails_past_a_cap(tmp_path: Path) -> None:
    """A file at its cap passes and one character more fails, for both kinds."""
    rules = tmp_path / ".claude" / "rules"
    rules.mkdir(parents=True)
    claude_md = tmp_path / "CLAUDE.md"
    rule = rules / "topic.md"

    claude_md.write_text("x" * CLAUDE_MD_CAP, encoding="utf-8")
    rule.write_text("x" * RULE_FILE_CAP, encoding="utf-8")
    assert _over_budget(tmp_path) == []

    claude_md.write_text("x" * (CLAUDE_MD_CAP + 1), encoding="utf-8")
    rule.write_text("x" * (RULE_FILE_CAP + 1), encoding="utf-8")
    assert _over_budget(tmp_path) == [
        f"CLAUDE.md: {CLAUDE_MD_CAP + 1} > {CLAUDE_MD_CAP}",
        f".claude/rules/topic.md: {RULE_FILE_CAP + 1} > {RULE_FILE_CAP}",
    ]
