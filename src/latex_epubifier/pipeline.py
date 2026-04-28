from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .assets import render_assets_and_reinsert
from .html_render import (
    build_front_matter,
    build_html_ready,
    build_standalone_xhtml,
    escape_table_placeholder_captions,
)
from .parsing import (
    expand_inputs,
    extract_body,
    extract_body_macro_setup,
    extract_command_arg,
    extract_local_table_setup,
    extract_macro_definitions,
    extract_preamble,
    extract_usepackage_lines,
)
from .progress import ProgressReporter
from .references import build_references_html, count_bibliography_items
from .utils import (
    cleanup_non_debug_outputs,
    slug_for_content,
)


INPUT_RE = re.compile(r"\\input\{([^}]+)\}")
INCLUDE_RE = re.compile(r"\\include\{([^}]+)\}")
INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}",
    re.MULTILINE,
)
INLINE_MATH_RE = re.compile(r"(?<!\\)\$(.+?)(?<!\\)\$", re.DOTALL)
DISPLAY_MATH_RE = re.compile(
    r"\\\[(.*?)\\\]|\\begin\{equation\*?\}(.*?)\\end\{equation\*?\}|\\begin\{align\*?\}(.*?)\\end\{align\*?\}",
    re.DOTALL,
)
USEPACKAGE_LINE_RE = re.compile(r"^\\usepackage(?:\[[^\]]*\])?\{[^}]+\}\s*$", re.MULTILINE)
TABLE_BLOCK_RE = re.compile(r"\\begin\{table\*?\}.*?\\end\{table\*?\}", re.DOTALL)
BIBLIOGRAPHY_RE = re.compile(r"\\bibliography\{([^}]+)\}")
BIBITEM_RE = re.compile(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}")


PREAMBLE_PATTERNS = [
    r"\\documentclass(?:\[[^\]]*\])?\{[^}]+\}",
    r"\\usepackage(?:\[[^\]]*\])?\{[^}]+\}",
    r"\\bibliographystyle\{[^}]+\}",
    r"\\bibliography\{[^}]+\}",
    r"\\title\{.*?\}",
    r"\\author\{.*?\}",
    r"\\date\{.*?\}",
    r"\\maketitle",
    r"\\appendix",
]

MACRO_PATTERNS = [
    r"\\newcommand\*?(?:\\\w+|\{\\[^}]+\})(?:\[[^\]]+\])?\{.*?\}",
    r"\\renewcommand\*?(?:\\\w+|\{\\[^}]+\})(?:\[[^\]]+\])?\{.*?\}",
]

STYLE_COMMAND_PATTERNS = [
    r"\\vspace\*?\{[^}]+\}",
    r"\\hspace\*?\{[^}]+\}",
    r"\\noindent\b",
    r"\\centering\b",
    r"\\definecolor\{[^}]+\}\{[^}]+\}\{[^}]+\}",
    r"\\fbox\{%",
    r"\\parbox\{[^}]+\}\{%",
]


@dataclass
class BuildArtifacts:
    expanded: str
    body: str
    sanitized: str
    html_ready: str
    references_html: str
    title: str
    author: str
    manifest: dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize a LaTeX project for EPUB conversion.")
    parser.add_argument("main_tex", type=Path, help="Path to the main .tex file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build"),
        help="Directory for generated output files",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Keep all intermediate files, rendered assets, and EPUB staging files",
    )
    parser.add_argument(
        "--validate-epub",
        action="store_true",
        help="Run EPUB validation after packaging; uses built-in checks and epubcheck if available",
    )
    parser.add_argument(
        "--epub-theme",
        choices=["auto", "light", "dark"],
        default="auto",
        help="EPUB theme mode. 'auto' leaves text colors neutral, 'dark' emits white math assets and dark-oriented CSS.",
    )
    return parser.parse_args()


def unescape_latex_text(text: str) -> str:
    return (
        text.replace(r"\%", "%")
        .replace(r"\&", "&")
        .replace(r"\#", "#")
        .replace(r"\_", "_")
        .replace(r"\$", "$")
    )


def normalize_metadata_text(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"(?m)(?<!\\)%.*$", " ", text)
    cleaned = unescape_latex_text(cleaned)
    cleaned = cleaned.replace(r"\{", "{").replace(r"\}", "}")
    cleaned = re.sub(r"\\texttt\{([^}]*)\}", r"\1", cleaned)
    cleaned = re.sub(r"\\(?:textit|emph|textbf)\{([^}]*)\}", r"\1", cleaned)
    cleaned = re.sub(r"\\hspace\*?\{[^}]+\}", " ", cleaned)
    cleaned = re.sub(r"\\{2,}", " | ", cleaned)
    cleaned = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?", " ", cleaned)
    cleaned = cleaned.replace("{", "").replace("}", "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"(?:\s*\|\s*){2,}", " | ", cleaned)
    cleaned = cleaned.strip(" |")
    return cleaned.strip()


def normalize_caption_text(text: str) -> str:
    normalized = normalize_inline_markup(normalize_metadata_text(text)).replace("\n", " ").strip()
    normalized = re.sub(r"(?<!\\)\$(.+?)(?<!\\)\$", r"\1", normalized)
    return normalized


def replace_texttt_blocks(text: str) -> str:
    result: list[str] = []
    i = 0
    marker = r"\texttt{"
    while True:
        start = text.find(marker, i)
        if start == -1:
            result.append(text[i:])
            break
        result.append(text[i:start])
        j = start + len(marker) - 1
        depth = 0
        body_chars: list[str] = []
        while j < len(text):
            ch = text[j]
            if ch == "{":
                depth += 1
                if depth > 1:
                    body_chars.append(ch)
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
                body_chars.append(ch)
            else:
                if depth >= 1:
                    body_chars.append(ch)
            j += 1
        body = "".join(body_chars).strip()
        if "\n" in body:
            result.append(f"<prompt-block>{body}</prompt-block>")
        else:
            result.append(f"<code>{body}</code>")
        i = j
    return "".join(result)


def strip_patterns(text: str, patterns: list[str]) -> str:
    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL)
    return cleaned


def sanitize_latex(text: str) -> str:
    cleaned = strip_patterns(text, PREAMBLE_PATTERNS)
    cleaned = re.sub(r"^\\(?:newcommand|renewcommand)\b.*$", "", cleaned, flags=re.MULTILINE)
    cleaned = strip_patterns(cleaned, STYLE_COMMAND_PATTERNS)
    cleaned = strip_patterns(cleaned, MACRO_PATTERNS)
    cleaned = re.sub(r"(?m)^\s*%.*$", "", cleaned)
    cleaned = replace_texttt_blocks(cleaned)
    cleaned = re.sub(r"\\cite[t|p]?\{([^}]+)\}", r"<cite data-keys='\1'>[\1]</cite>", cleaned)
    cleaned = re.sub(r"\\ref\{([^}]+)\}", r"<ref data-label='\1'>\1</ref>", cleaned)
    cleaned = re.sub(r"\\label\{[^}]+\}", "", cleaned)
    cleaned = re.sub(r"\\url\{([^}]+)\}", r"\1", cleaned)
    cleaned = re.sub(r"\\href\{([^}]+)\}\{([^}]+)\}", r"\2 (\1)", cleaned)
    cleaned = re.sub(r"\\doi\{([^}]+)\}", r"doi: \1", cleaned)
    cleaned = re.sub(r"\\newblock\b", " ", cleaned)
    cleaned = re.sub(r"\\(?:textit|emph)\{([^}]+)\}", r"<em>\1</em>", cleaned)
    cleaned = re.sub(r"\\textbf\{([^}]+)\}", r"<strong>\1</strong>", cleaned)
    cleaned = re.sub(r"\\begin\{itemize\}", "", cleaned)
    cleaned = re.sub(r"\\end\{itemize\}", "", cleaned)
    cleaned = re.sub(r"\\item\s+", " - ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() + "\n"


def collect_display_math(text: str) -> list[str]:
    snippets: list[str] = []
    for match in DISPLAY_MATH_RE.finditer(text):
        groups = [group for group in match.groups() if group]
        if groups:
            snippets.append(groups[0].strip())
    return snippets


def collect_inline_math(text: str) -> list[str]:
    return [match.group(1).strip() for match in INLINE_MATH_RE.finditer(text)]


def build_manifest(expanded: str, body: str, sanitized: str) -> dict:
    figures = INCLUDEGRAPHICS_RE.findall(expanded)
    inline_math = collect_inline_math(body)
    display_math = collect_display_math(body)
    return {
        "figures": figures,
        "inline_math_count": len(inline_math),
        "display_math_count": len(display_math),
        "inline_math_samples": inline_math[:10],
        "display_math_samples": display_math[:10],
        "expanded_length": len(expanded),
        "body_length": len(body),
        "sanitized_length": len(sanitized),
    }


def render_citation_links(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw_keys = match.group(1)
        keys = [key.strip() for key in raw_keys.split(",") if key.strip()]
        if not keys:
            return "[]"
        links = [
            f'<a class="citation-link" href="#{html.escape(f"ref-{key}", quote=True)}">{html.escape(key)}</a>'
            for key in keys
        ]
        return f"[{', '.join(links)}]"

    return re.sub(r"<cite data-keys='([^']+)'>\[[^]]*\]</cite>", replace, text)


def normalize_references(text: str) -> str:
    normalized = re.sub(
        r"Figure~<ref data-label='([^']+)'>[^<]+</ref>",
        r'<span class="xref xref-figure">Figure \1</span>',
        text,
    )
    normalized = re.sub(
        r"Section~<ref data-label='([^']+)'>[^<]+</ref>",
        r'<span class="xref xref-section">Section \1</span>',
        normalized,
    )
    normalized = re.sub(
        r"Appendix~<ref data-label='([^']+)'>[^<]+</ref>",
        r'<span class="xref xref-appendix">Appendix \1</span>',
        normalized,
    )
    normalized = re.sub(
        r"<ref data-label='([^']+)'>[^<]+</ref>",
        r'<span class="xref">\1</span>',
        normalized,
    )
    return normalized


def normalize_inline_markup(text: str) -> str:
    normalized = text
    normalized = re.sub(r"\\footnote\{([^}]+)\}", r' <span class="footnote">[\1]</span>', normalized)
    normalized = render_citation_links(normalized)
    normalized = normalize_references(normalized)
    normalized = unescape_latex_text(normalized)
    normalized = normalized.replace("&", "&amp;")
    normalized = re.sub(r"<(?=\s*\d)", "&lt;", normalized)
    normalized = re.sub(r"~", " ", normalized)
    normalized = re.sub(r"\s+\n", "\n", normalized)
    normalized = re.sub(r"\n\s+", "\n", normalized)
    return normalized.strip()


def normalize_prompt_block(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"\\begin\{itemize\}", "", cleaned)
    cleaned = re.sub(r"\\end\{itemize\}", "", cleaned)
    cleaned = re.sub(r"\\item\s+", "- ", cleaned)
    cleaned = re.sub(r"\\(?:textbf|textit|emph)\{([^}]*)\}", r"\1", cleaned)
    cleaned = re.sub(r"\\{2,}", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def build_epub_chapter_xhtml(body_html: str, title: str, author: str) -> str:
    safe_title = html.escape(title or "Untitled")
    safe_author = html.escape(author or "")
    author_meta = f'  <meta name="author" content="{safe_author}" />' if safe_author else ""
    chapter_body = re.sub(r'((?:src|href)=["\'])assets/', r"\1../assets/", body_html)
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<!DOCTYPE html>',
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">',
            "<head>",
            '  <meta charset="utf-8" />',
            f"  <title>{safe_title}</title>",
            author_meta,
            '  <link rel="stylesheet" type="text/css" href="../styles/book.css" />',
            "</head>",
            "<body>",
            chapter_body.strip(),
            "</body>",
            "</html>",
            "",
        ]
    )


def build_epub_nav_xhtml(title: str) -> str:
    safe_title = html.escape(title or "Untitled")
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<!DOCTYPE html>',
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">',
            "<head>",
            '  <meta charset="utf-8" />',
            "  <title>Table of Contents</title>",
            "</head>",
            "<body>",
            '  <nav epub:type="toc" id="toc">',
            "    <h1>Contents</h1>",
            "    <ol>",
            f'      <li><a href="text/chapter.xhtml">{safe_title}</a></li>',
            "    </ol>",
            "  </nav>",
            "</body>",
            "</html>",
            "",
        ]
    )


def build_epub_ncx(title: str, identifier: str) -> str:
    safe_title = html.escape(title or "Untitled")
    safe_id = html.escape(identifier)
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN"',
            '  "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">',
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">',
            "  <head>",
            f'    <meta name="dtb:uid" content="{safe_id}" />',
            '    <meta name="dtb:depth" content="1" />',
            '    <meta name="dtb:totalPageCount" content="0" />',
            '    <meta name="dtb:maxPageNumber" content="0" />',
            "  </head>",
            f"  <docTitle><text>{safe_title}</text></docTitle>",
            "  <navMap>",
            '    <navPoint id="navpoint-1" playOrder="1">',
            f"      <navLabel><text>{safe_title}</text></navLabel>",
            '      <content src="text/chapter.xhtml" />',
            "    </navPoint>",
            "  </navMap>",
            "</ncx>",
            "",
        ]
    )


def build_epub_opf(title: str, author: str, identifier: str, manifest_items: list[str]) -> str:
    safe_title = html.escape(title or "Untitled")
    safe_author = html.escape(author or "Unknown")
    safe_id = html.escape(identifier)
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<package version="3.0" unique-identifier="bookid" xmlns="http://www.idpf.org/2007/opf">',
            "  <metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">",
            f"    <dc:identifier id=\"bookid\">{safe_id}</dc:identifier>",
            f"    <dc:title>{safe_title}</dc:title>",
            f"    <dc:creator>{safe_author}</dc:creator>",
            "    <dc:language>en</dc:language>",
            "  </metadata>",
            "  <manifest>",
            *manifest_items,
            "  </manifest>",
            '  <spine toc="ncx">',
            '    <itemref idref="chapter" />',
            "  </spine>",
            "</package>",
            "",
        ]
    )


def build_epub_css(theme: str = "auto") -> str:
    lines = [
        "html, body {",
        "  margin: 0;",
        "  padding: 0;",
        "}",
        "body {",
        "  line-height: 1.6;",
        "  font-family: serif;",
        "}",
        "h1, h2, h3 {",
        "  line-height: 1.25;",
        "  margin: 1.5em 0 0.5em;",
        "  font-weight: 600;",
        "}",
        "p {",
        "  margin: 0.9em 0;",
        "}",
        "figure {",
        "  margin: 1.25em 0;",
        "}",
        "figcaption {",
        "  font-size: 0.95em;",
        "  margin-top: 0.45em;",
        "}",
        "img {",
        "  max-width: 100%;",
        "  height: auto;",
        "}",
        ".equation {",
        "  margin: 1.1em 0;",
        "  text-align: center;",
        "}",
        ".math-inline {",
        "  vertical-align: -0.2em;",
        "  display: inline-block;",
        "  max-height: 1.35em;",
        "  margin: 0 0.08em;",
        "}",
        ".equation img {",
        "  display: inline-block;",
        "}",
        "@media (prefers-color-scheme: dark) {",
        "  .math-inline, .equation img {",
        "    filter: invert(1) hue-rotate(180deg);",
        "  }",
        "}",
        "code {",
        "  font-family: monospace;",
        "  font-size: 0.92em;",
        "}",
        "pre.prompt-block {",
        "  white-space: pre-wrap;",
        "  word-break: break-word;",
        "  overflow-wrap: anywhere;",
        "  margin: 1em 0;",
        "  font-size: 0.92rem;",
        "  line-height: 1.45;",
        "}",
        ".xref, .footnote, .authors {",
        "  color: inherit;",
        "}",
        ".citation-link {",
        "  color: #555555;",
        "  text-decoration-line: underline;",
        "  text-decoration-style: dashed;",
        "  text-decoration-color: #8a8a8a;",
        "  text-underline-offset: 0.12em;",
        "}",
        ".references {",
        "  margin-top: 2.25em;",
        "}",
        ".reference-list {",
        "  padding-left: 1.4em;",
        "}",
        ".reference-list li {",
        "  margin: 0.7em 0;",
        "}",
        ".ref-title {",
        "  font-style: italic;",
        "}",
        ".footnote {",
        "  font-size: 0.95em;",
        "}",
        ".abstract {",
        "  padding: 0;",
        "}",
        "@media (prefers-color-scheme: dark) {",
        "  .citation-link {",
        "    color: #c8c8c8;",
        "    text-decoration-color: #a8a8a8;",
        "  }",
        "}",
    ]
    lines.append("")
    return "\n".join(lines)


def validate_xhtml(xhtml_text: str) -> None:
    ET.fromstring(xhtml_text)


def read_xml(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def validate_epub_structure(epub_root: Path) -> dict:
    issues: list[str] = []
    warnings: list[str] = []

    mimetype_path = epub_root / "mimetype"
    if not mimetype_path.exists():
        issues.append("Missing mimetype file.")
    elif mimetype_path.read_text(encoding="utf-8") != "application/epub+zip":
        issues.append("mimetype file does not equal application/epub+zip.")

    container_path = epub_root / "META-INF" / "container.xml"
    if not container_path.exists():
        issues.append("Missing META-INF/container.xml.")
        return {"ok": False, "issues": issues, "warnings": warnings}

    try:
        container_root = read_xml(container_path)
    except ET.ParseError as exc:
        issues.append(f"container.xml is not valid XML: {exc}.")
        return {"ok": False, "issues": issues, "warnings": warnings}

    container_ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfile = container_root.find("c:rootfiles/c:rootfile", container_ns)
    if rootfile is None:
        issues.append("container.xml does not declare a rootfile.")
        return {"ok": False, "issues": issues, "warnings": warnings}

    opf_rel = rootfile.attrib.get("full-path", "")
    if not opf_rel:
        issues.append("container.xml rootfile is missing full-path.")
        return {"ok": False, "issues": issues, "warnings": warnings}
    opf_path = epub_root / opf_rel
    if not opf_path.exists():
        issues.append(f"Package document not found: {opf_rel}.")
        return {"ok": False, "issues": issues, "warnings": warnings}

    try:
        opf_root = read_xml(opf_path)
    except ET.ParseError as exc:
        issues.append(f"content.opf is not valid XML: {exc}.")
        return {"ok": False, "issues": issues, "warnings": warnings}

    opf_ns = {"opf": "http://www.idpf.org/2007/opf"}
    manifest = opf_root.find("opf:manifest", opf_ns)
    spine = opf_root.find("opf:spine", opf_ns)
    if manifest is None:
        issues.append("content.opf is missing manifest.")
        return {"ok": False, "issues": issues, "warnings": warnings}
    if spine is None:
        issues.append("content.opf is missing spine.")
        return {"ok": False, "issues": issues, "warnings": warnings}

    manifest_items = manifest.findall("opf:item", opf_ns)
    manifest_by_id = {item.attrib.get("id", ""): item for item in manifest_items}
    nav_found = False
    for item in manifest_items:
        href = item.attrib.get("href", "")
        item_id = item.attrib.get("id", "")
        if not href or not item_id:
            issues.append("A manifest item is missing id or href.")
            continue
        item_path = opf_path.parent / href
        if not item_path.exists():
            issues.append(f"Manifest item missing on disk: {href}.")
        if "nav" in item.attrib.get("properties", "").split():
            nav_found = True
    if not nav_found:
        issues.append("content.opf does not declare a nav document.")

    for itemref in spine.findall("opf:itemref", opf_ns):
        idref = itemref.attrib.get("idref", "")
        if idref not in manifest_by_id:
            issues.append(f"Spine references unknown manifest id: {idref}.")

    nav_path = opf_path.parent / "nav.xhtml"
    if nav_path.exists():
        try:
            read_xml(nav_path)
        except ET.ParseError as exc:
            issues.append(f"nav.xhtml is not valid XML: {exc}.")
    else:
        issues.append("Missing nav.xhtml.")

    chapter_path = opf_path.parent / "text" / "chapter.xhtml"
    if chapter_path.exists():
        try:
            read_xml(chapter_path)
        except ET.ParseError as exc:
            issues.append(f"chapter.xhtml is not valid XML: {exc}.")
    else:
        issues.append("Missing text/chapter.xhtml.")

    toc_path = opf_path.parent / "toc.ncx"
    if toc_path.exists():
        try:
            read_xml(toc_path)
        except ET.ParseError as exc:
            issues.append(f"toc.ncx is not valid XML: {exc}.")
    else:
        warnings.append("Missing toc.ncx; some EPUB 3 readers may still work, but compatibility is reduced.")

    css_path = opf_path.parent / "styles" / "book.css"
    if not css_path.exists():
        issues.append("Missing styles/book.css.")

    return {"ok": not issues, "issues": issues, "warnings": warnings}


def run_epubcheck(epub_path: Path) -> dict:
    epubcheck = shutil.which("epubcheck")
    if not epubcheck:
        return {
            "available": False,
            "ok": None,
            "stdout": "",
            "stderr": "",
            "returncode": None,
        }
    proc = subprocess.run(
        [epubcheck, str(epub_path)],
        capture_output=True,
        text=True,
    )
    return {
        "available": True,
        "ok": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
    }


def validate_epub(epub_path: Path, epub_root: Path) -> dict:
    structure = validate_epub_structure(epub_root)
    epubcheck = run_epubcheck(epub_path)
    return {
        "ok": structure["ok"] and (epubcheck["ok"] is not False),
        "structure": structure,
        "epubcheck": epubcheck,
    }


def add_epub_asset(manifest_lines: list[str], source: Path, href: str, item_id: str) -> None:
    media_type, _ = mimetypes.guess_type(source.name)
    if not media_type:
        media_type = "application/octet-stream"
    manifest_lines.append(
        f'    <item id="{html.escape(item_id)}" href="{html.escape(href)}" media-type="{html.escape(media_type)}" />'
    )
def package_epub(
    output_dir: Path,
    epub_root: Path,
    body_html: str,
    title: str,
    author: str,
    theme: str = "auto",
) -> Path:
    identifier = f"urn:uuid:{hashlib.sha1((title + author + body_html).encode('utf-8')).hexdigest()}"
    chapter_xhtml = build_epub_chapter_xhtml(body_html, title, author)
    nav_xhtml = build_epub_nav_xhtml(title)
    validate_xhtml(chapter_xhtml)
    validate_xhtml(nav_xhtml)

    oebps_dir = epub_root / "OEBPS"
    meta_inf_dir = epub_root / "META-INF"
    text_dir = oebps_dir / "text"
    styles_dir = oebps_dir / "styles"
    assets_target_dir = oebps_dir / "assets"
    for path in [text_dir, styles_dir, assets_target_dir, meta_inf_dir]:
        path.mkdir(parents=True, exist_ok=True)

    (epub_root / "mimetype").write_text("application/epub+zip", encoding="utf-8")
    (meta_inf_dir / "container.xml").write_text(
        "\n".join(
            [
                '<?xml version="1.0" encoding="utf-8"?>',
                '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">',
                "  <rootfiles>",
                '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />',
                "  </rootfiles>",
                "</container>",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (oebps_dir / "nav.xhtml").write_text(nav_xhtml, encoding="utf-8")
    (oebps_dir / "toc.ncx").write_text(build_epub_ncx(title, identifier), encoding="utf-8")
    (text_dir / "chapter.xhtml").write_text(chapter_xhtml, encoding="utf-8")
    (styles_dir / "book.css").write_text(build_epub_css(theme=theme), encoding="utf-8")

    source_assets = output_dir / "assets"
    manifest_items = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />',
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml" />',
        '    <item id="chapter" href="text/chapter.xhtml" media-type="application/xhtml+xml" />',
        '    <item id="css" href="styles/book.css" media-type="text/css" />',
    ]
    if source_assets.exists():
        for source in sorted(source_assets.rglob("*")):
            if not source.is_file():
                continue
            relative_asset = source.relative_to(source_assets)
            target = assets_target_dir / relative_asset
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            href = f"assets/{relative_asset.as_posix()}"
            item_id = slug_for_content("asset", href)
            add_epub_asset(manifest_items, source, href, item_id)

    (oebps_dir / "content.opf").write_text(
        build_epub_opf(title, author, identifier, manifest_items),
        encoding="utf-8",
    )

    epub_name = "book.epub" if theme in {"auto", "light"} else f"book-{theme}.epub"
    epub_path = output_dir / epub_name
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.write(epub_root / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
        for file_path in sorted(epub_root.rglob("*")):
            if file_path.is_dir() or file_path.name == "mimetype":
                continue
            archive.write(file_path, file_path.relative_to(epub_root).as_posix(), compress_type=zipfile.ZIP_DEFLATED)
    return epub_path


def run(
    main_tex: Path,
    output_dir: Path,
    render_assets: bool = True,
    math_theme: str = "light",
    debug: bool = False,
    progress: ProgressReporter | None = None,
) -> BuildArtifacts:
    if progress:
        progress.step(1, 6, "Expanding LaTeX inputs")
    expanded = expand_inputs(main_tex, INPUT_RE, INCLUDE_RE)
    if progress:
        progress.step(2, 6, "Parsing document structure and bibliography")
    preamble = extract_preamble(expanded)
    body = extract_body(expanded)
    sanitized = sanitize_latex(body)
    references_html = build_references_html(
        expanded,
        main_tex,
        BIBLIOGRAPHY_RE,
        BIBITEM_RE,
        sanitize_latex,
        normalize_inline_markup,
    )
    title = normalize_metadata_text(extract_command_arg(preamble, "title"))
    author = normalize_metadata_text(extract_command_arg(preamble, "author"))
    manifest = build_manifest(expanded, body, sanitized)
    html_ready = build_html_ready(
        sanitized,
        normalize_inline_markup,
        normalize_prompt_block,
        extract_command_arg,
        normalize_caption_text,
    )
    macro_definitions = extract_macro_definitions(preamble, MACRO_PATTERNS)
    preamble_packages = extract_usepackage_lines(preamble, USEPACKAGE_LINE_RE)
    body_macro_setup = extract_body_macro_setup(body)
    manifest["references_count"] = count_bibliography_items(
        expanded,
        main_tex,
        BIBLIOGRAPHY_RE,
        BIBITEM_RE,
    )

    if render_assets:
        if progress:
            progress.step(3, 6, "Rendering tables, figures, and math assets")
        html_ready = render_assets_and_reinsert(
            body,
            sanitize_latex,
            escape_table_placeholder_captions,
            lambda working: build_html_ready(
                working,
                normalize_inline_markup,
                normalize_prompt_block,
                extract_command_arg,
                normalize_caption_text,
            ),
            main_tex,
            output_dir,
            manifest,
            macro_definitions,
            preamble_packages,
            expanded,
            body_macro_setup,
            TABLE_BLOCK_RE,
            INCLUDEGRAPHICS_RE,
            DISPLAY_MATH_RE,
            INLINE_MATH_RE,
            extract_command_arg,
            normalize_caption_text,
            extract_local_table_setup,
            math_theme,
            progress=progress,
        )

    if progress:
        progress.step(4, 6, "Composing final HTML")
    front_matter = build_front_matter(title, author)
    if front_matter:
        html_ready = front_matter + "\n" + html_ready
    if references_html:
        html_ready = html_ready.rstrip() + "\n" + references_html + "\n"

    if progress:
        progress.step(5, 6, "Writing output files")
    output_dir.mkdir(parents=True, exist_ok=True)
    if debug:
        (output_dir / "expanded.tex").write_text(expanded, encoding="utf-8")
        (output_dir / "body.tex").write_text(body, encoding="utf-8")
        (output_dir / "sanitized.tex").write_text(sanitized, encoding="utf-8")
        (output_dir / "content.html").write_text(html_ready, encoding="utf-8")
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (output_dir / "preview-standalone.xhtml").write_text(
            build_standalone_xhtml(html_ready, title=title or "latex-epubifier preview"),
            encoding="utf-8",
        )

    return BuildArtifacts(
        expanded=expanded,
        body=body,
        sanitized=sanitized,
        html_ready=html_ready,
        references_html=references_html,
        title=title,
        author=author,
        manifest=manifest,
    )


def main() -> int:
    args = parse_args()
    math_theme = "dark" if args.epub_theme == "dark" else "auto"
    progress = ProgressReporter()
    artifacts = run(
        args.main_tex,
        args.output_dir,
        render_assets=True,
        math_theme=math_theme,
        debug=args.debug,
        progress=progress,
    )
    temp_epub_dir: Path | None = None
    epub_root = args.output_dir / "epub"
    if not args.debug:
        temp_epub_dir = Path(tempfile.mkdtemp(prefix="latex-epubifier-epub-"))
        epub_root = temp_epub_dir / "epub"
    try:
        progress.step(6, 6, "Packaging EPUB")
        epub_path = package_epub(
            args.output_dir,
            epub_root,
            artifacts.html_ready,
            artifacts.title,
            artifacts.author,
            theme=args.epub_theme,
        )
        artifacts.manifest["epub"] = str(epub_path)
        if args.validate_epub:
            validation = validate_epub(epub_path, epub_root)
            artifacts.manifest["epub_validation"] = validation
            if not validation["ok"]:
                print(json.dumps(artifacts.manifest, indent=2, ensure_ascii=False))
                return 1
    finally:
        if temp_epub_dir is not None:
            shutil.rmtree(temp_epub_dir, ignore_errors=True)
    if args.debug:
        print(json.dumps(artifacts.manifest, indent=2, ensure_ascii=False))
    else:
        cleanup_non_debug_outputs(args.output_dir)
    return 0
