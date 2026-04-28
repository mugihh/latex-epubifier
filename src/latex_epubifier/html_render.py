from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class BlockNode:
    kind: str
    content: str = ""
    level: int = 0


STRUCTURAL_TOKEN_RE = re.compile(
    r"\\begin\{abstract\}.*?\\end\{abstract\}"
    r"|\\begin\{figure\*?\}(?:\[[^\]]*\])?.*?\\end\{figure\*?\}"
    r"|\\(?:section|subsection|subsubsection)\*?\{.*?\}"
    r"|<latex-epub-block-math\s+[^>]*></latex-epub-block-math>"
    r"|<latex-epub-table\s+[^>]*></latex-epub-table>"
    r"|<prompt-block>.*?</prompt-block>",
    re.DOTALL,
)


def build_front_matter(title: str, author: str) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"<h1>{html.escape(title)}</h1>")
    if author:
        parts.append(f'<p class="authors">{html.escape(author)}</p>')
    return "\n".join(parts)


def build_conversion_notice(repo_url: str) -> str:
    safe_url = html.escape(repo_url, quote=True)
    safe_label = html.escape(repo_url)
    return "\n".join(
        [
            '<section class="conversion-notice" id="conversion-notice">',
            "<h1>Conversion Notice</h1>",
            "<p>This EPUB was generated from LaTeX source using latex-epubifier.</p>",
            "<p>This tool only converts document format. Copyright remains with the original author(s) or rights holder(s).</p>",
            "<p>This conversion does not grant redistribution, republication, or other reuse rights. Any sharing or reuse must comply with the original work's license or the rights holder's permission.</p>",
            f'<p>Tool repository: <a href="{safe_url}">{safe_label}</a></p>',
            "</section>",
        ]
    )


def escape_table_placeholder_captions(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        prefix = match.group(1)
        caption = match.group(2)
        suffix = match.group(3)
        return f'{prefix}{html.escape(caption, quote=True)}{suffix}'

    return re.sub(r'(<latex-epub-table\s+[^>]*caption=")(.*?)("></latex-epub-table>)', replace, text, flags=re.DOTALL)


def parse_figure_block(
    block: str,
    extract_command_arg: Callable[[str, str], str],
    normalize_caption_text: Callable[[str], str],
) -> str:
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
        heading_match = re.fullmatch(r"\\(section|subsection|subsubsection)\*?\{(.*)\}", chunk, flags=re.DOTALL)
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


def render_block(
    block: BlockNode,
    normalize_inline_markup: Callable[[str], str],
    normalize_prompt_block: Callable[[str], str],
    extract_command_arg: Callable[[str, str], str],
    normalize_caption_text: Callable[[str], str],
) -> str:
    if block.kind == "heading":
        title = normalize_inline_markup(block.content)
        return f"<h{block.level}>{title}</h{block.level}>"
    if block.kind == "abstract":
        return f'<section class="abstract"><p>{normalize_inline_markup(block.content)}</p></section>'
    if block.kind == "figure":
        return parse_figure_block(block.content, extract_command_arg, normalize_caption_text)
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


def build_html_ready(
    sanitized: str,
    normalize_inline_markup: Callable[[str], str],
    normalize_prompt_block: Callable[[str], str],
    extract_command_arg: Callable[[str, str], str],
    normalize_caption_text: Callable[[str], str],
) -> str:
    blocks = parse_blocks(sanitized)
    rendered = [
        render_block(
            block,
            normalize_inline_markup,
            normalize_prompt_block,
            extract_command_arg,
            normalize_caption_text,
        )
        for block in blocks
    ]
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
            "    .citation-link { color: #555555; text-decoration-line: underline; text-decoration-style: dashed; text-decoration-color: #8a8a8a; text-underline-offset: 0.12em; }",
            "    @media (prefers-color-scheme: dark) { .citation-link { color: #c8c8c8; text-decoration-color: #a8a8a8; } }",
            "    .footnote { color: inherit; font-size: 0.95em; }",
            "    .abstract { padding: 0; background: transparent; border: none; }",
            "    .authors { margin-top: -0.15rem; color: inherit; font-size: 0.98rem; }",
            "    .references { margin-top: 2.25rem; }",
            "    .conversion-notice { margin-top: 2.25rem; color: #444444; font-size: 0.95rem; }",
            "    .reference-list { padding-left: 1.4rem; }",
            "    .reference-list li { margin: 0.7rem 0; }",
            "    .ref-title { font-style: italic; }",
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
