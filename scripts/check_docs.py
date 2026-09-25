#!/usr/bin/env python
"""Check documentation integrity (documentation tooling; does not touch the simulator).

Verifies, for README.md and every Markdown file under docs/:
  1. relative Markdown links resolve to existing files, and `#anchor`s to existing headings;
  2. backtick repository paths (simulator/..., api/..., configs/..., ...) exist;
  3. source references  "- `path` — `Symbol`, `Class.method`"  name symbols defined in that file;
  4. `tests/file.py::test_name` references name existing tests.

Exit code 1 when anything is broken.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_FILES = [ROOT / "README.md"] + sorted((ROOT / "docs").rglob("*.md"))
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
TICK_RE = re.compile(r"`([^`]+)`")
PATH_RE = re.compile(r"^(simulator|api|configs|contract|scripts|tests|ui|scenarios|docs)/[\w./-]+$")
SOURCE_LINE_RE = re.compile(r"^\s*-\s+`([^`]+)`\s*(?:—|-)\s*(.+)$")
SYMBOL_RE = re.compile(r"`([A-Za-z_][\w.]*)`")


def slug(heading: str) -> str:
    s = heading.strip().lower()
    s = re.sub(r"[`*_~]", "", s)
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s", "-", s)


def anchors(md: Path) -> set:
    out = set()
    for line in md.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#{1,6}\s+(.*)$", line)
        if m:
            out.add(slug(m.group(1)))
    return out


def symbol_defined(path: Path, symbol: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    parts = symbol.split(".")
    for p in parts:
        pat = (rf"(^|\n)\s*(def|class)\s+{re.escape(p)}\b|(^|\n){re.escape(p)}\s*[:=]"
               rf"|(^|\n)\s+{re.escape(p)}\s*[:=]")
        if not re.search(pat, text):
            return False
    return True


def main() -> int:
    errors = []
    checked = {"links": 0, "paths": 0, "symbols": 0, "tests": 0}
    for md in DOC_FILES:
        rel = md.relative_to(ROOT)
        text = md.read_text(encoding="utf-8")
        in_code = False
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            for target in LINK_RE.findall(line):
                if re.match(r"^[a-z]+:", target) or target.startswith("mailto"):
                    continue
                file_part, _, anchor = target.partition("#")
                dest = (md.parent / file_part).resolve() if file_part else md
                checked["links"] += 1
                if not dest.exists():
                    errors.append(f"{rel}:{n}: broken link {target}")
                elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                    errors.append(f"{rel}:{n}: missing anchor #{anchor} in {dest.relative_to(ROOT)}")
            for tok in TICK_RE.findall(line):
                tok = tok.strip()
                if "::" in tok and tok.startswith("tests/"):
                    f, _, t = tok.partition("::")
                    checked["tests"] += 1
                    fp = ROOT / f
                    if not fp.exists() or not re.search(rf"def {re.escape(t.split('[')[0])}\b", fp.read_text(encoding="utf-8")):
                        errors.append(f"{rel}:{n}: unknown test {tok}")
                    continue
                if PATH_RE.match(tok) and "*" not in tok and "<" not in tok:
                    checked["paths"] += 1
                    if not (ROOT / tok).exists():
                        errors.append(f"{rel}:{n}: missing path {tok}")
            m = SOURCE_LINE_RE.match(line)
            if m and PATH_RE.match(m.group(1)):
                path = ROOT / m.group(1)
                if path.exists() and path.suffix == ".py":
                    for sym in SYMBOL_RE.findall(m.group(2)):
                        checked["symbols"] += 1
                        if not symbol_defined(path, sym):
                            errors.append(f"{rel}:{n}: symbol {sym} not found in {m.group(1)}")
    print(f"checked {len(DOC_FILES)} files: {checked}")
    for e in errors:
        print("ERROR", e)
    print("OK" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
