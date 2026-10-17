"""Offline structural checks for the maintained engineering entry documents.

Only reads files. Does not import business code, resolve URLs or inspect databases.
Historical reports outside DOCUMENTS are deliberately not a link-cleanup project.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import unquote, urlsplit


DOCUMENTS = (
    "AGENTS.md",
    "README.md",
    "docs/PROJECT_GOVERNANCE.md",
    "docs/ARCHITECTURE_CURRENT.md",
    "docs/CAPABILITY_REGISTRY.md",
    "docs/B0_IMPLEMENTATION.md",
    "docs/B0_PRODUCTION_RUNBOOK.md",
    "docs/dev_setup.md",
    "docs/GOVERNANCE_AUDIT.md",
    "docs/GOVERNANCE_VALIDATION_REPORT.md",
    ".github/pull_request_template.md",
)
REQUIRED_READS = (
    "README.md",
    "docs/PROJECT_GOVERNANCE.md",
    "docs/ARCHITECTURE_CURRENT.md",
    "docs/CAPABILITY_REGISTRY.md",
)


def unfenced(text: str) -> str:
    lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            lines.append("")
        else:
            lines.append(line if fence is None else "")
    return "\n".join(lines)


def heading_ids(text: str) -> set[str]:
    counts: dict[str, int] = {}
    result = set()
    for line in unfenced(text).splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = match[1].lower().replace("`", "")
        title = "".join(
            c for c in title
            if c in "-_" or not unicodedata.category(c).startswith(("P", "S"))
        ).replace(" ", "-")
        count = counts.get(title, 0)
        counts[title] = count + 1
        result.add(f"{title}-{count}" if count else title)
    return result


def links(text: str):
    # Maintained documents use ordinary inline links or reference definitions.
    for number, line in enumerate(unfenced(text).splitlines(), 1):
        for match in re.finditer(r"!?\[[^\]\n]*\]\((<[^>]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\)", line):
            yield number, match[1].strip("<>")
        reference = re.match(r"^\s*\[[^\]]+\]:\s*(\S+)", line)
        if reference:
            yield number, reference[1].strip("<>")


def check(root: Path) -> list[str]:
    errors = []
    for name in DOCUMENTS:
        source = root / name
        if not source.is_file():
            errors.append(f"{name}: missing maintained document")
            continue
        text = source.read_text(encoding="utf-8")
        targets = list(links(text))
        if name == "AGENTS.md":
            present = {urlsplit(link).path for _, link in targets}
            for required in REQUIRED_READS:
                if required not in present:
                    errors.append(f"AGENTS.md: missing required read link: {required}")
        for line, link in targets:
            parts = urlsplit(link)
            if parts.scheme or parts.netloc:
                continue
            target = (source.parent / unquote(parts.path)).resolve() if parts.path else source
            try:
                target.relative_to(root)
                inside = True
            except ValueError:
                inside = False
            if not inside:
                errors.append(f"{name}:{line}: link escapes repository: {link}")
            elif not target.exists():
                errors.append(f"{name}:{line}: missing local target: {link}")
            elif parts.fragment and target.suffix.lower() == ".md":
                if unquote(parts.fragment) not in heading_ids(target.read_text(encoding="utf-8")):
                    errors.append(f"{name}:{line}: missing heading: {link}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    root = parser.parse_args().root.resolve()
    errors = check(root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Engineering docs: PASS ({len(DOCUMENTS)} maintained documents; local links/anchors and required reads)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
