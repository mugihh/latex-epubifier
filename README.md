# latex-epubifier

Turn LaTeX papers into e-reader-friendly XHTML and EPUB.

## How to use

Basic usage:

```bash
python3 -m src.latex_epubifier.cli path/to/main.tex --output-dir build --render-assets --build-epub --validate-epub
```

This generates:

- rendered math / figure / table assets
- a validated EPUB

Add `--keep-preview` if you want a browser preview file.
Add `--keep-debug-artifacts` if you want the normalized `.tex` files plus HTML/manifest debugging outputs.

## Common commands

Build a normal EPUB:

```bash
python3 -m src.latex_epubifier.cli path/to/main.tex --output-dir build --render-assets --build-epub --validate-epub
```

Build a dark-math EPUB:

```bash
python3 -m src.latex_epubifier.cli path/to/main.tex --output-dir build-dark --render-assets --build-epub --validate-epub --epub-theme dark
```

## Output files

Typical output files:

- with `--build-epub`: `book.epub`
- without `--build-epub`: no final file is written unless you ask for preview/debug outputs

Optional preview file with `--keep-preview`:

- `preview-standalone.xhtml`

Optional debugging files with `--keep-debug-artifacts`:

- `content.html`
- `manifest.json`
- `expanded.tex`
- `body.tex`
- `sanitized.tex`

Optional EPUB staging directory with `--keep-epub-workdir`:

- `epub/`

Asset folders:

- `assets/math/`
- `assets/figures/`
- `assets/tables/`

## Supported features

- expand `\input`
- extract paper body
- preserve title / author / abstract
- render inline math and display math to SVG
- convert PDF figures to PNG
- convert complex LaTeX tables to PNG
- keep prompt / code-like blocks
- package XHTML into EPUB
- run built-in EPUB validation

## Notes on dark mode

Recommended default:

- use the normal EPUB (`book.epub`)
- let the reader handle light / dark mode for regular text

If your e-reader does not recolor math images correctly in dark mode, build the dark-math version instead:

```bash
python3 -m src.latex_epubifier.cli path/to/main.tex --output-dir build-dark --render-assets --build-epub --validate-epub --epub-theme dark
```

This version:

- keeps normal text styling neutral
- keeps figures unchanged
- swaps math snippets to white SVG assets for dark readers

## CLI flags

- `--render-assets`
- `--build-epub`
- `--validate-epub`
- `--keep-preview`
- `--keep-debug-artifacts`
- `--keep-epub-workdir`
- `--epub-theme auto|light|dark`

## Validation

The built-in validation checks:

- XHTML/XML well-formedness
- required EPUB files
- manifest and spine consistency
- referenced asset existence

If `epubcheck` is installed, it is used automatically during validation.
