from __future__ import annotations

import re
from pathlib import Path

from .utils import ensure_tex_suffix


def is_supported_setup_line(stripped: str) -> bool:
    return stripped.startswith(
        (
            r"\definecolor",
            r"\newcommand",
            r"\renewcommand",
            r"\newcolumntype",
        )
    )


def expand_inputs(
    tex_path: Path,
    input_re: re.Pattern[str],
    include_re: re.Pattern[str],
    seen: set[Path] | None = None,
) -> str:
    seen = seen or set()
    tex_path = tex_path.resolve()
    if tex_path in seen:
        return f"% Skipped recursive input: {tex_path}\n"
    seen.add(tex_path)

    text = tex_path.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        rel = ensure_tex_suffix(match.group(1).strip())
        child = (tex_path.parent / rel).resolve()
        if not child.exists():
            return f"% Missing input: {rel}\n"
        return expand_inputs(child, input_re, include_re, seen)

    expanded = input_re.sub(replace, text)
    return include_re.sub(replace, expanded)


def extract_body(text: str) -> str:
    start = text.find(r"\begin{document}")
    end = text.rfind(r"\end{document}")
    if start == -1 or end == -1 or start >= end:
        return text
    start += len(r"\begin{document}")
    return text[start:end].strip() + "\n"


def extract_preamble(text: str) -> str:
    start = text.find(r"\begin{document}")
    if start == -1:
        return text
    return text[:start]


def extract_command_arg(text: str, command: str) -> str:
    match = re.search(rf"\\{command}\s*\{{", text, flags=re.DOTALL)
    if not match:
        return ""
    start = match.end() - 1
    depth = 0
    chars: list[str] = []
    for ch in text[start:]:
        if ch == "{":
            depth += 1
            if depth > 1:
                chars.append(ch)
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
            chars.append(ch)
        else:
            if depth >= 1:
                chars.append(ch)
    return "".join(chars).strip()


def extract_macro_definitions(preamble: str, macro_patterns: list[str]) -> str:
    matches: list[str] = []
    for pattern in macro_patterns:
        matches.extend(re.findall(pattern, preamble, flags=re.DOTALL))
    lines = []
    for line in preamble.splitlines():
        stripped = line.strip()
        if stripped.startswith(r"\newcommand") or stripped.startswith(r"\renewcommand"):
            lines.append(stripped)
    return "\n".join(lines) + ("\n" if lines else "")


def extract_usepackage_lines(preamble: str, usepackage_line_re: re.Pattern[str]) -> str:
    lines = []
    for line in usepackage_line_re.findall(preamble):
        if "{acl}" in line:
            continue
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def extract_local_table_setup(text: str, table_block: str) -> str:
    start = text.find(table_block)
    if start == -1:
        return ""
    prefix = text[:start].rstrip()
    lines = prefix.splitlines()
    collected: list[str] = []
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            if collected:
                break
            continue
        if is_supported_setup_line(stripped):
            collected.append(stripped)
            continue
        if collected:
            break
    collected.reverse()
    return "\n".join(collected) + ("\n" if collected else "")


def extract_body_macro_setup(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if is_supported_setup_line(stripped):
            lines.append(stripped)
    return "\n".join(lines) + ("\n" if lines else "")
