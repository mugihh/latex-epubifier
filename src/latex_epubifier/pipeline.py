from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .assets import render_assets_and_reinsert
from .epub import package_epub, validate_epub
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
from .utils import cleanup_non_debug_outputs


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
