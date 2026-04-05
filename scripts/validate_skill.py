#!/usr/bin/env python3
"""Validate a public Codex skill bundle."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ALLOWED_PROPERTIES = {"name", "description"}
MAX_FRONTMATTER_LENGTH = 1024
MAX_SKILL_NAME_LENGTH = 64


def validate_skill(skill_path: str) -> tuple[bool, str]:
    skill_dir = Path(skill_path)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    content = skill_md.read_text()
    if not content.startswith("---"):
        return False, "No YAML frontmatter found"

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    raw_frontmatter = match.group(1)
    if len(raw_frontmatter) > MAX_FRONTMATTER_LENGTH:
        return False, (
            f"Frontmatter is too long ({len(raw_frontmatter)} characters). "
            f"Maximum is {MAX_FRONTMATTER_LENGTH} characters."
        )

    try:
        frontmatter = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as exc:
        return False, f"Invalid YAML in frontmatter: {exc}"
    if not isinstance(frontmatter, dict):
        return False, "Frontmatter must be a YAML dictionary"

    unexpected = set(frontmatter) - ALLOWED_PROPERTIES
    if unexpected:
        allowed = ", ".join(sorted(ALLOWED_PROPERTIES))
        return (
            False,
            f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected))}. "
            f"Allowed properties are: {allowed}",
        )

    name = frontmatter.get("name")
    if not isinstance(name, str):
        return False, "Missing or invalid 'name' in frontmatter"
    name = name.strip()
    if not re.match(r"^[a-z0-9-]+$", name):
        return False, f"Name '{name}' should be hyphen-case"
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens"
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return False, f"Name is too long ({len(name)} characters). Maximum is {MAX_SKILL_NAME_LENGTH} characters."

    description = frontmatter.get("description")
    if not isinstance(description, str):
        return False, "Missing or invalid 'description' in frontmatter"
    description = description.strip()
    if not description.startswith("Use when"):
        return False, "Description must start with 'Use when'"
    if "<" in description or ">" in description:
        return False, "Description cannot contain angle brackets (< or >)"

    return True, "Skill is valid!"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("Usage: python validate_skill.py <skill_directory>")
        return 1

    valid, message = validate_skill(args[0])
    print(message)
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
