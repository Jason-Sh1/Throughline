"""Versioned prompt files. `load("synthesize_v1")` returns (text, version).

Prompts are never inlined in code (CLAUDE.md). A change to a prompt means a new
file with the next version number, so cached cards made with the old prompt
are regenerated (the version is part of the content hash) and eval results
stay comparable across versions.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent

EXTRACT = "extract_v1"
SYNTHESIZE = "synthesize_v1"


def load(name: str) -> tuple[str, str]:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8"), name
