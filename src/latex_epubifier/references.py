from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Callable


def find_bibliography_text(expanded: str, main_tex: Path, bibliography_re: re.Pattern[str]) -> str:
    inline_match = re.search(
        r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}",
        expanded,
        flags=re.DOTALL,
    )
    if inline_match:
        return inline_match.group(0)

    bbl_path = main_tex.with_suffix(".bbl")
    if bbl_path.exists():
        return bbl_path.read_text(encoding="utf-8")

    bibliography_match = bibliography_re.search(expanded)
    if bibliography_match:
        return f"% Missing bibliography output for: {bibliography_match.group(1)}\n"
    return ""


def split_bibliography_items(
    bibliography_text: str,
    bibitem_re: re.Pattern[str],
) -> list[tuple[str, str]]:
    matches = list(bibitem_re.finditer(bibliography_text))
    items: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(bibliography_text)
        key = match.group(1).strip()
        content = bibliography_text[start:end].strip()
        if content:
            items.append((key, content))
    return items


def count_bibliography_items(
    expanded: str,
    main_tex: Path,
    bibliography_re: re.Pattern[str],
    bibitem_re: re.Pattern[str],
) -> int:
    bibliography_text = find_bibliography_text(expanded, main_tex, bibliography_re)
    if not bibliography_text or bibliography_text.lstrip().startswith("% Missing bibliography output"):
        return 0
    return len(split_bibliography_items(bibliography_text, bibitem_re))


def normalize_reference_item_text(
    text: str,
    sanitize_latex: Callable[[str], str],
    normalize_inline_markup: Callable[[str], str],
) -> str:
    cleaned = sanitize_latex(text)
    cleaned = re.sub(r"\\begin\{[^}]+\}|\\end\{[^}]+\}", " ", cleaned)
    cleaned = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?", " ", cleaned)
    cleaned = cleaned.replace("{", "").replace("}", "")
    cleaned = normalize_inline_markup(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip()


def emphasize_reference_title(reference_html: str) -> str:
    match = re.search(r"([“\"'])(.+?)([”\"'])", reference_html)
    if not match:
        return reference_html
    title = match.group(2).strip()
    if len(title) < 8:
        return reference_html
    replacement = f'{match.group(1)}<em class="ref-title">{title}</em>{match.group(3)}'
    return reference_html[: match.start()] + replacement + reference_html[match.end() :]


def build_references_html(
    expanded: str,
    main_tex: Path,
    bibliography_re: re.Pattern[str],
    bibitem_re: re.Pattern[str],
    sanitize_latex: Callable[[str], str],
    normalize_inline_markup: Callable[[str], str],
) -> str:
    bibliography_text = find_bibliography_text(expanded, main_tex, bibliography_re)
    if not bibliography_text or bibliography_text.lstrip().startswith("% Missing bibliography output"):
        return ""

    items = split_bibliography_items(bibliography_text, bibitem_re)
    if not items:
        return ""

    lines = ['<section class="references" id="references">', "<h1>References</h1>", '<ol class="reference-list">']
    for key, content in items:
        normalized = emphasize_reference_title(
            normalize_reference_item_text(content, sanitize_latex, normalize_inline_markup)
        )
        if not normalized:
            continue
        safe_id = html.escape(f"ref-{key}", quote=True)
        lines.append(f'  <li id="{safe_id}">{normalized}</li>')
    lines.extend(["</ol>", "</section>"])
    return "\n".join(lines)
