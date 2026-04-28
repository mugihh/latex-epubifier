from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .progress import ProgressReporter
from .utils import run_command, slug_for_content


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
    display_math_re: re.Pattern[str],
    math_theme: str = "light",
    progress: ProgressReporter | None = None,
) -> str:
    matches = list(display_math_re.finditer(text))
    total = len(matches)
    progress_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal progress_count
        groups = [group for group in match.groups() if group]
        if not groups:
            return match.group(0)
        content = groups[0].strip()
        progress_count += 1
        if progress:
            progress.item("display math", progress_count, total)
        block = content
        asset_name = slug_for_content(f"display-math-{math_theme}", block)
        svg_path = assets_dir / "math" / f"{asset_name}.svg"
        render_math_to_svg(block, svg_path, macro_definitions, display_mode=True, theme=math_theme)
        manifest.setdefault("rendered_display_math", []).append(
            {"latex": content, "asset": f"assets/math/{asset_name}.svg"}
        )
        alt = html.escape(content, quote=True)
        return f'\n<latex-epub-block-math src="assets/math/{asset_name}.svg" alt="{alt}"></latex-epub-block-math>\n'

    return display_math_re.sub(replace, text)


def replace_inline_math_with_images(
    text: str,
    assets_dir: Path,
    manifest: dict,
    macro_definitions: str,
    inline_math_re: re.Pattern[str],
    math_theme: str = "light",
    progress: ProgressReporter | None = None,
) -> str:
    matches = list(inline_math_re.finditer(text))
    total = len(matches)
    progress_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal progress_count
        content = match.group(1).strip()
        progress_count += 1
        if progress:
            progress.item("inline math", progress_count, total)
        inline = f"${content}$"
        asset_name = slug_for_content(f"inline-math-{math_theme}", inline)
        svg_path = assets_dir / "math" / f"{asset_name}.svg"
        render_math_to_svg(inline, svg_path, macro_definitions, display_mode=False, theme=math_theme)
        manifest.setdefault("rendered_inline_math", []).append(
            {"latex": content, "asset": f"assets/math/{asset_name}.svg"}
        )
        alt = html.escape(content, quote=True)
        return f'<img class="math-inline" src="assets/math/{asset_name}.svg" alt="{alt}" />'

    return inline_math_re.sub(replace, text)


def replace_figures_with_images(
    text: str,
    source_root: Path,
    assets_dir: Path,
    manifest: dict,
    includegraphics_re: re.Pattern[str],
    progress: ProgressReporter | None = None,
) -> str:
    matches = list(includegraphics_re.finditer(text))
    total = len(matches)
    progress_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal progress_count
        figure_ref = match.group(1).strip()
        source = (source_root / figure_ref).resolve()
        if not source.exists():
            return match.group(0)
        progress_count += 1
        if progress:
            progress.item("figures", progress_count, total, figure_ref)
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

    return includegraphics_re.sub(replace, text)


def replace_tables_with_images(
    text: str,
    assets_dir: Path,
    manifest: dict,
    preamble_packages: str,
    macro_definitions: str,
    source_text: str,
    body_macro_setup: str,
    source_root: Path,
    table_block_re: re.Pattern[str],
    extract_command_arg: Callable[[str, str], str],
    normalize_caption_text: Callable[[str], str],
    extract_local_table_setup: Callable[[str, str], str],
    progress: ProgressReporter | None = None,
) -> str:
    matches = list(table_block_re.finditer(text))
    total = len(matches)
    progress_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal progress_count
        table_block = match.group(0).strip()
        caption = normalize_caption_text(extract_command_arg(table_block, "caption") or "Table")
        progress_count += 1
        if progress:
            progress.item("tables", progress_count, total, caption)
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

    return table_block_re.sub(replace, text)


def render_assets_and_reinsert(
    raw_body: str,
    sanitize_latex: Callable[[str], str],
    escape_table_placeholder_captions: Callable[[str], str],
    build_html_ready: Callable[[str], str],
    main_tex: Path,
    output_dir: Path,
    manifest: dict,
    macro_definitions: str,
    preamble_packages: str,
    expanded_source: str,
    body_macro_setup: str,
    table_block_re: re.Pattern[str],
    includegraphics_re: re.Pattern[str],
    display_math_re: re.Pattern[str],
    inline_math_re: re.Pattern[str],
    extract_command_arg: Callable[[str, str], str],
    normalize_caption_text: Callable[[str], str],
    extract_local_table_setup: Callable[[str, str], str],
    math_theme: str = "light",
    progress: ProgressReporter | None = None,
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
        table_block_re,
        extract_command_arg,
        normalize_caption_text,
        extract_local_table_setup,
        progress=progress,
    )
    working = sanitize_latex(raw_with_tables)
    working = escape_table_placeholder_captions(working)
    working = replace_figures_with_images(
        working,
        main_tex.parent,
        assets_dir,
        manifest,
        includegraphics_re,
        progress=progress,
    )
    working = replace_display_math_with_images(
        working,
        assets_dir,
        manifest,
        macro_definitions,
        display_math_re,
        math_theme=math_theme,
        progress=progress,
    )
    working = replace_inline_math_with_images(
        working,
        assets_dir,
        manifest,
        macro_definitions,
        inline_math_re,
        math_theme=math_theme,
        progress=progress,
    )
    return build_html_ready(working)
