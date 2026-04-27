from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path


INPUT_RE = re.compile(r"\\input\{([^}]+)\}")
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
    title: str
    author: str
    manifest: dict


@dataclass
class BlockNode:
    kind: str
    content: str = ""
    level: int = 0


def is_supported_setup_line(stripped: str) -> bool:
    return stripped.startswith(
        (
            r"\definecolor",
            r"\newcommand",
            r"\renewcommand",
            r"\newcolumntype",
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize a LaTeX project for EPUB conversion.")
    parser.add_argument("main_tex", type=Path, help="Path to the main .tex file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build"),
        help="Directory for intermediate files",
    )
    parser.add_argument(
        "--render-assets",
        action="store_true",
        help="Render math to SVG and PDF figures to PNG, then reinsert them into HTML-ready output",
    )
    parser.add_argument(
        "--build-epub",
        action="store_true",
        help="Package the validated XHTML output into an EPUB file",
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


def ensure_tex_suffix(path_text: str) -> str:
    return path_text if path_text.endswith(".tex") else f"{path_text}.tex"


def expand_inputs(tex_path: Path, seen: set[Path] | None = None) -> str:
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
        return expand_inputs(child, seen)

    return INPUT_RE.sub(replace, text)


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


def build_front_matter(title: str, author: str) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"<h1>{html.escape(title)}</h1>")
    if author:
        parts.append(f'<p class="authors">{html.escape(author)}</p>')
    return "\n".join(parts)


def run_command(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )


def slug_for_content(prefix: str, content: str) -> str:
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def extract_macro_definitions(preamble: str) -> str:
    matches: list[str] = []
    for pattern in MACRO_PATTERNS:
        matches.extend(re.findall(pattern, preamble, flags=re.DOTALL))
    lines = []
    for line in preamble.splitlines():
        stripped = line.strip()
        if stripped.startswith(r"\newcommand") or stripped.startswith(r"\renewcommand"):
            lines.append(stripped)
    return "\n".join(lines) + ("\n" if lines else "")


def extract_usepackage_lines(preamble: str) -> str:
    lines = []
    for line in USEPACKAGE_LINE_RE.findall(preamble):
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


def normalize_math_snippet(latex_snippet: str, display_mode: bool) -> str:
    normalized = latex_snippet.replace(r"\mathbbm{1}", r"\mathbf{1}")
    if display_mode:
        if "&" in normalized or r"\\" in normalized:
            return "\n".join(
                [
                    r"\begin{align*}",
                    normalized,
                    r"\end{align*}",
                ]
            )
        return rf"\[{normalized}\]"
    return normalized if normalized.startswith("$") else f"${normalized}$"


def recolor_svg(svg_text: str, theme: str) -> str:
    if theme == "dark":
        recolored = re.sub(
            r'fill="rgb\(0%, 0%, 0%\)"',
            'fill="rgb(100%, 100%, 100%)"',
            svg_text,
        )
        recolored = re.sub(
            r'stroke="rgb\(0%, 0%, 0%\)"',
            'stroke="rgb(100%, 100%, 100%)"',
            recolored,
        )
        return recolored
    return svg_text


def render_math_to_svg(
    latex_snippet: str,
    output_path: Path,
    macro_definitions: str = "",
    display_mode: bool = False,
    theme: str = "light",
) -> None:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="latex-epubifier-math-") as tmp_dir:
        tmp = Path(tmp_dir)
        tex_file = tmp / "math.tex"
        tex_file.write_text(
            "\n".join(
                [
                    r"\documentclass{article}",
                    r"\usepackage{amsmath}",
                    r"\usepackage{amssymb}",
                    r"\usepackage{amsfonts}",
                    r"\usepackage{bm}",
                    r"\pagestyle{empty}",
                    macro_definitions.rstrip(),
                    r"\begin{document}",
                    normalize_math_snippet(latex_snippet, display_mode),
                    r"\end{document}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        run_command(["pdflatex", "-interaction=nonstopmode", "math.tex"], cwd=tmp)
        run_command(["pdfcrop", "math.pdf", "math-crop.pdf"], cwd=tmp)
        run_command(
            [
                "pdftocairo",
                "-svg",
                "math-crop.pdf",
                str(output_path),
            ],
            cwd=tmp,
        )
    svg_text = output_path.read_text(encoding="utf-8")
    output_path.write_text(recolor_svg(svg_text, theme), encoding="utf-8")


def render_pdf_figure_to_png(source_pdf: Path, output_base: Path) -> Path:
    output_base.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "pdftoppm",
            "-png",
            "-singlefile",
            str(source_pdf),
            str(output_base),
        ]
    )
    return output_base.with_suffix(".png")


def copy_figure_asset(source: Path, output_base: Path) -> Path:
    output_path = output_base.with_suffix(source.suffix.lower())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output_path)
    return output_path


def render_table_to_png(
    table_block: str,
    output_base: Path,
    preamble_packages: str,
    macro_definitions: str,
    local_table_setup: str,
    source_root: Path,
) -> Path:
    output_base.parent.mkdir(parents=True, exist_ok=True)
    tex_env = os.environ.copy()
    existing_texinputs = tex_env.get("TEXINPUTS", "")
    tex_env["TEXINPUTS"] = f"{source_root.resolve()}{os.pathsep}{existing_texinputs}"
    with tempfile.TemporaryDirectory(prefix="latex-epubifier-table-") as tmp_dir:
        tmp = Path(tmp_dir)
        tex_file = tmp / "table.tex"
        tex_file.write_text(
            "\n".join(
                [
                    r"\documentclass{article}",
                    preamble_packages.rstrip(),
                    macro_definitions.rstrip(),
                    local_table_setup.rstrip(),
                    r"\pagestyle{empty}",
                    r"\begin{document}",
                    table_block,
                    r"\end{document}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "table.tex"],
            cwd=str(tmp),
            capture_output=True,
            text=True,
            env=tex_env,
        )
        if proc.returncode != 0 and not (tmp / "table.pdf").exists():
            raise subprocess.CalledProcessError(
                proc.returncode,
                proc.args,
                output=proc.stdout,
                stderr=proc.stderr,
            )
        run_command(["pdfcrop", "table.pdf", "table-crop.pdf"], cwd=tmp)
        run_command(
            [
                "pdftoppm",
                "-png",
                "-singlefile",
                str((tmp / "table-crop.pdf").resolve()),
                str(output_base.resolve()),
            ]
        )
    return output_base.with_suffix(".png")


def replace_display_math_with_images(
    text: str,
    assets_dir: Path,
    manifest: dict,
    macro_definitions: str,
    math_theme: str = "light",
) -> str:
    def replace(match: re.Match[str]) -> str:
        groups = [group for group in match.groups() if group]
        if not groups:
            return match.group(0)
        content = groups[0].strip()
        block = content
        asset_name = slug_for_content(f"display-math-{math_theme}", block)
        svg_path = assets_dir / "math" / f"{asset_name}.svg"
        render_math_to_svg(block, svg_path, macro_definitions, display_mode=True, theme=math_theme)
        manifest.setdefault("rendered_display_math", []).append(
            {"latex": content, "asset": f"assets/math/{asset_name}.svg"}
        )
        alt = html.escape(content, quote=True)
        return f'\n<latex-epub-block-math src="assets/math/{asset_name}.svg" alt="{alt}"></latex-epub-block-math>\n'

    return DISPLAY_MATH_RE.sub(replace, text)


def replace_inline_math_with_images(
    text: str,
    assets_dir: Path,
    manifest: dict,
    macro_definitions: str,
    math_theme: str = "light",
) -> str:
    def replace(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        inline = f"${content}$"
        asset_name = slug_for_content(f"inline-math-{math_theme}", inline)
        svg_path = assets_dir / "math" / f"{asset_name}.svg"
        render_math_to_svg(inline, svg_path, macro_definitions, display_mode=False, theme=math_theme)
        manifest.setdefault("rendered_inline_math", []).append(
            {"latex": content, "asset": f"assets/math/{asset_name}.svg"}
        )
        alt = html.escape(content, quote=True)
        return f'<img class="math-inline" src="assets/math/{asset_name}.svg" alt="{alt}" />'

    return INLINE_MATH_RE.sub(replace, text)


def replace_figures_with_images(text: str, source_root: Path, assets_dir: Path, manifest: dict) -> str:
    def replace(match: re.Match[str]) -> str:
        figure_ref = match.group(1).strip()
        source = (source_root / figure_ref).resolve()
        if not source.exists():
            return match.group(0)
        asset_name = slug_for_content("figure", figure_ref)
        output_base = assets_dir / "figures" / asset_name
        if source.suffix.lower() == ".pdf":
            asset_path = render_pdf_figure_to_png(source, output_base)
        elif source.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
            asset_path = copy_figure_asset(source, output_base)
        else:
            return match.group(0)
        manifest.setdefault("rendered_figures", []).append(
            {
                "source": figure_ref,
                "asset": f"assets/figures/{asset_path.name}",
            }
        )
        return f'\\includegraphics{{assets/figures/{asset_path.name}}}'
        return match.group(0)

    return INCLUDEGRAPHICS_RE.sub(replace, text)


def replace_tables_with_images(
    text: str,
    assets_dir: Path,
    manifest: dict,
    preamble_packages: str,
    macro_definitions: str,
    source_text: str,
    body_macro_setup: str,
    source_root: Path,
) -> str:
    def replace(match: re.Match[str]) -> str:
        table_block = match.group(0).strip()
        caption = normalize_caption_text(extract_command_arg(table_block, "caption") or "Table")
        asset_name = slug_for_content("table", table_block)
        output_base = assets_dir / "tables" / asset_name
        local_table_setup = extract_local_table_setup(source_text, table_block)
        png_path = render_table_to_png(
            table_block,
            output_base,
            preamble_packages,
            macro_definitions + body_macro_setup,
            local_table_setup,
            source_root,
        )
        manifest.setdefault("rendered_tables", []).append(
            {
                "caption": caption,
                "asset": f"assets/tables/{png_path.name}",
            }
        )
        safe_caption = html.escape(caption, quote=True)
        return (
            f'\n<latex-epub-table src="assets/tables/{png_path.name}" '
            f'caption="{safe_caption}"></latex-epub-table>\n'
        )

    return TABLE_BLOCK_RE.sub(replace, text)


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


def escape_table_placeholder_captions(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        prefix = match.group(1)
        caption = match.group(2)
        suffix = match.group(3)
        return f'{prefix}{html.escape(caption, quote=True)}{suffix}'

    return re.sub(r'(<latex-epub-table\s+[^>]*caption=")(.*?)("></latex-epub-table>)', replace, text, flags=re.DOTALL)


def parse_figure_block(block: str) -> str:
    image_match = re.search(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", block)
    img_html = ""
    if image_match:
        src = html.escape(image_match.group(1).strip(), quote=True)
        img_html = f'<img src="{src}" alt="figure" />'
    caption_html = ""
    caption = extract_command_arg(block, "caption")
    if caption:
        caption = normalize_caption_text(caption)
        caption_html = f"<figcaption>{caption}</figcaption>"
    return f"<figure>{img_html}{caption_html}</figure>"


STRUCTURAL_TOKEN_RE = re.compile(
    r"\\begin\{abstract\}.*?\\end\{abstract\}"
    r"|\\begin\{figure\*?\}(?:\[[^\]]*\])?.*?\\end\{figure\*?\}"
    r"|\\(?:section|subsection|subsubsection)\{.*?\}"
    r"|<latex-epub-block-math\s+[^>]*></latex-epub-block-math>"
    r"|<latex-epub-table\s+[^>]*></latex-epub-table>"
    r"|<prompt-block>.*?</prompt-block>",
    re.DOTALL,
)


def parse_blocks(text: str) -> list[BlockNode]:
    blocks: list[BlockNode] = []
    working = text
    working = re.sub(r"\\begin\{small\}|\\end\{small\}", "", working)
    working = re.sub(r"\r\n", "\n", working)
    cursor = 0
    tokens: list[str] = []
    for match in STRUCTURAL_TOKEN_RE.finditer(working):
        if match.start() > cursor:
            tokens.append(working[cursor:match.start()])
        tokens.append(match.group(0))
        cursor = match.end()
    if cursor < len(working):
        tokens.append(working[cursor:])

    for token in tokens:
        chunk = token.strip()
        if not chunk:
            continue
        if chunk.startswith(r"\begin{abstract}"):
            content = chunk.removeprefix(r"\begin{abstract}").removesuffix(r"\end{abstract}").strip()
            blocks.append(BlockNode(kind="abstract", content=content))
            continue
        if chunk.startswith(r"\begin{figure"):
            blocks.append(BlockNode(kind="figure", content=chunk))
            continue
        if chunk.startswith("<latex-epub-block-math"):
            blocks.append(BlockNode(kind="display_math", content=chunk))
            continue
        if chunk.startswith("<latex-epub-table"):
            blocks.append(BlockNode(kind="table", content=chunk))
            continue
        if chunk.startswith("<prompt-block>"):
            content = chunk.removeprefix("<prompt-block>").removesuffix("</prompt-block>").strip()
            blocks.append(BlockNode(kind="prompt", content=content))
            continue
        heading_match = re.fullmatch(r"\\(section|subsection|subsubsection)\{(.*)\}", chunk, flags=re.DOTALL)
        if heading_match:
            level_map = {"section": 1, "subsection": 2, "subsubsection": 3}
            blocks.append(
                BlockNode(
                    kind="heading",
                    level=level_map[heading_match.group(1)],
                    content=heading_match.group(2).strip(),
                )
            )
            continue
        paragraphs = re.split(r"\n\s*\n", chunk)
        for paragraph in paragraphs:
            para = paragraph.strip()
            if not para:
                continue
            para_heading_match = re.match(r"\\paragraph\{([^}]+)\}\s*(.*)", para, flags=re.DOTALL)
            if para_heading_match:
                title = para_heading_match.group(1).strip()
                rest = para_heading_match.group(2).strip()
                content = f"<strong>{title}</strong>"
                if rest:
                    content += f" {rest}"
                blocks.append(BlockNode(kind="paragraph", content=content))
                continue
            blocks.append(BlockNode(kind="paragraph", content=para))
    return blocks


def render_block(block: BlockNode) -> str:
    if block.kind == "heading":
        title = normalize_inline_markup(block.content)
        return f"<h{block.level}>{title}</h{block.level}>"
    if block.kind == "abstract":
        return f'<section class="abstract"><p>{normalize_inline_markup(block.content)}</p></section>'
    if block.kind == "figure":
        return parse_figure_block(block.content)
    if block.kind == "display_math":
        match = re.search(r'src="([^"]+)" alt="([^"]*)"', block.content)
        if not match:
            return block.content
        src = match.group(1)
        alt = match.group(2)
        return f'<div class="equation"><img src="{src}" alt="{alt}" /></div>'
    if block.kind == "table":
        match = re.search(r'src="([^"]+)" caption="([^"]*)"', block.content)
        if not match:
            return block.content
        src = match.group(1)
        caption = html.unescape(match.group(2))
        alt_text = html.escape(re.sub(r"<[^>]+>", "", caption), quote=True)
        return f'<figure class="table-figure"><img src="{src}" alt="{alt_text}" /><figcaption>{caption}</figcaption></figure>'
    if block.kind == "prompt":
        prompt = html.escape(normalize_prompt_block(block.content))
        return f'<pre class="prompt-block"><code>{prompt}</code></pre>'
    if block.kind == "paragraph":
        content = normalize_inline_markup(block.content)
        return f"<p>{content}</p>"
    return block.content


def build_html_ready(sanitized: str) -> str:
    blocks = parse_blocks(sanitized)
    rendered = [render_block(block) for block in blocks]
    return "\n".join(rendered).strip() + "\n"


def build_standalone_xhtml(body_html: str, title: str = "latex-epubifier preview") -> str:
    safe_title = html.escape(title)
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<!DOCTYPE html>',
            '<html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">',
            "<head>",
            '  <meta charset="utf-8" />',
            f"  <title>{safe_title}</title>",
            "  <style>",
            "    html { background: #ffffff; color: #111111; }",
            "    body { max-width: 42rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; line-height: 1.6; font-family: serif; }",
            "    h1, h2, h3 { line-height: 1.25; margin-top: 1.5em; margin-bottom: 0.5em; font-weight: 600; }",
            "    p { margin: 0.9em 0; }",
            "    figure { margin: 1.25em 0; }",
            "    figcaption { font-size: 0.95em; color: #333333; margin-top: 0.45em; }",
            "    img { max-width: 100%; height: auto; }",
            "    .equation { margin: 1.1em 0; text-align: center; }",
            "    .math-inline { vertical-align: -0.2em; display: inline-block; max-height: 1.35em; margin: 0 0.08em; }",
            "    code { font-family: monospace; font-size: 0.92em; background: transparent; padding: 0; border-radius: 0; }",
            "    pre.prompt-block { white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; background: transparent; border: none; padding: 0; margin: 1em 0; font-size: 0.92rem; line-height: 1.45; }",
            "    .table-figure img { background: white; }",
            "    .xref { color: inherit; }",
            "    .footnote { color: inherit; font-size: 0.95em; }",
            "    .abstract { padding: 0; background: transparent; border: none; }",
            "    .authors { margin-top: -0.15rem; color: inherit; font-size: 0.98rem; }",
            "    @media (max-width: 640px) { body { padding: 1rem 0.875rem 2.5rem; } }",
            "  </style>",
            "</head>",
            "<body>",
            body_html.strip(),
            "</body>",
            "</html>",
            "",
        ]
    )


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
        ".footnote {",
        "  font-size: 0.95em;",
        "}",
        ".abstract {",
        "  padding: 0;",
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
def package_epub(output_dir: Path, body_html: str, title: str, author: str, theme: str = "auto") -> Path:
    identifier = f"urn:uuid:{hashlib.sha1((title + author + body_html).encode('utf-8')).hexdigest()}"
    chapter_xhtml = build_epub_chapter_xhtml(body_html, title, author)
    nav_xhtml = build_epub_nav_xhtml(title)
    validate_xhtml(chapter_xhtml)
    validate_xhtml(nav_xhtml)

    epub_root = output_dir / "epub"
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


def render_assets_and_reinsert(
    raw_body: str,
    sanitized: str,
    main_tex: Path,
    output_dir: Path,
    manifest: dict,
    macro_definitions: str,
    preamble_packages: str,
    expanded_source: str,
    body_macro_setup: str,
    math_theme: str = "light",
) -> str:
    assets_dir = output_dir / "assets"
    raw_with_tables = replace_tables_with_images(
        raw_body,
        assets_dir,
        manifest,
        preamble_packages,
        macro_definitions,
        expanded_source,
        body_macro_setup,
        main_tex.parent,
    )
    working = sanitize_latex(raw_with_tables)
    working = escape_table_placeholder_captions(working)
    working = replace_figures_with_images(working, main_tex.parent, assets_dir, manifest)
    working = replace_display_math_with_images(
        working, assets_dir, manifest, macro_definitions, math_theme=math_theme
    )
    working = replace_inline_math_with_images(
        working, assets_dir, manifest, macro_definitions, math_theme=math_theme
    )
    return build_html_ready(working)


def run(
    main_tex: Path,
    output_dir: Path,
    render_assets: bool = False,
    math_theme: str = "light",
) -> BuildArtifacts:
    expanded = expand_inputs(main_tex)
    preamble = extract_preamble(expanded)
    body = extract_body(expanded)
    sanitized = sanitize_latex(body)
    title = normalize_metadata_text(extract_command_arg(preamble, "title"))
    author = normalize_metadata_text(extract_command_arg(preamble, "author"))
    manifest = build_manifest(expanded, body, sanitized)
    html_ready = build_html_ready(sanitized)
    macro_definitions = extract_macro_definitions(preamble)
    preamble_packages = extract_usepackage_lines(preamble)
    body_macro_setup = extract_body_macro_setup(body)

    if render_assets:
        html_ready = render_assets_and_reinsert(
            body,
            sanitized,
            main_tex,
            output_dir,
            manifest,
            macro_definitions,
            preamble_packages,
            expanded,
            body_macro_setup,
            math_theme,
        )

    front_matter = build_front_matter(title, author)
    if front_matter:
        html_ready = front_matter + "\n" + html_ready

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "expanded.tex").write_text(expanded, encoding="utf-8")
    (output_dir / "body.tex").write_text(body, encoding="utf-8")
    (output_dir / "sanitized.tex").write_text(sanitized, encoding="utf-8")
    (output_dir / "content.html").write_text(html_ready, encoding="utf-8")
    (output_dir / "preview-standalone.xhtml").write_text(
        build_standalone_xhtml(html_ready, title=title or "latex-epubifier preview"),
        encoding="utf-8",
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return BuildArtifacts(
        expanded=expanded,
        body=body,
        sanitized=sanitized,
        html_ready=html_ready,
        title=title,
        author=author,
        manifest=manifest,
    )


def main() -> int:
    args = parse_args()
    math_theme = "dark" if args.epub_theme == "dark" else "auto"
    artifacts = run(
        args.main_tex,
        args.output_dir,
        render_assets=args.render_assets,
        math_theme=math_theme,
    )
    if args.build_epub:
        epub_path = package_epub(
            args.output_dir,
            artifacts.html_ready,
            artifacts.title,
            artifacts.author,
            theme=args.epub_theme,
        )
        artifacts.manifest["epub"] = str(epub_path)
        if args.validate_epub:
            validation = validate_epub(epub_path, args.output_dir / "epub")
            artifacts.manifest["epub_validation"] = validation
            if not validation["ok"]:
                print(json.dumps(artifacts.manifest, indent=2, ensure_ascii=False))
                return 1
    print(json.dumps(artifacts.manifest, indent=2, ensure_ascii=False))
    return 0
