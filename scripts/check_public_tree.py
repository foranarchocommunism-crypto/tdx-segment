"""Check the public file allowlist, local Markdown links and Python syntax."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"CMakeLists.txt", "VERSION", "LICENSE", "README.md", "CHANGELOG.md", "CONTRIBUTING.md", ".gitignore", ".gitattributes", "docs/assets/overview.svg"}
SUFFIXES = {"src": {".cpp", ".hpp"}, "include": {".hpp"}, "compat": {".h"}, "reference": {".py"}, "tests": {".cpp", ".py"}, "scripts": {".ps1", ".py"}, "docs": {".md"}, "examples": {".txt"}, ".github": {".yml"}}


def check() -> list[str]:
    if (ROOT / ".git").exists():
        raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        paths = [ROOT / item.decode("utf-8") for item in raw.split(b"\0") if item]
    else:
        paths = [p for p in ROOT.rglob("*") if p.is_file() and not {"build", "dist", "__pycache__"}.intersection(p.relative_to(ROOT).parts)]
    errors = []
    for path in paths:
        rel = path.relative_to(ROOT)
        if rel.as_posix() not in ROOT_FILES and path.suffix not in SUFFIXES.get(rel.parts[0], set()):
            errors.append(f"File outside public allowlist: {rel.as_posix()}")
        text = path.read_text(encoding="utf-8")
        if re.search(r"[A-Za-z]:[\\/]Users[\\/][^\s]+", text):
            errors.append(f"Personal absolute path: {rel.as_posix()}")
        if path.suffix == ".py":
            try:
                compile(text, str(rel), "exec")
            except SyntaxError as error:
                errors.append(f"Python syntax: {rel.as_posix()}:{error.lineno}")
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^\s)]+)\)", text):
                if target.startswith(("https://", "http://", "#", "mailto:")):
                    continue
                local = unquote(target.split("#", 1)[0])
                destination = (path.parent / local).resolve()
                if not destination.is_relative_to(ROOT) or not destination.is_file():
                    errors.append(f"Broken or external local link: {rel.as_posix()} -> {target}")
    return errors


if __name__ == "__main__":
    problems = check()
    if problems:
        raise SystemExit("\n".join(problems))
    print("Public file allowlist, Markdown links and Python syntax: passed")
