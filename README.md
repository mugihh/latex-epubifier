# latex-epubifier

Turn LaTeX papers into e-reader-friendly XHTML and EPUB.

## How to use

Basic usage:

```bash
python3 -m src.latex_epubifier.cli path/to/main.tex --output-dir build --validate-epub
```

This generates:

- `book.epub`

Add `--debug` if you want to keep all intermediate files, rendered assets, and EPUB staging files.

## Common commands

Build a normal EPUB:

```bash
python3 -m src.latex_epubifier.cli path/to/main.tex --output-dir build --validate-epub
```

Build a dark-math EPUB:

```bash
python3 -m src.latex_epubifier.cli path/to/main.tex --output-dir build-dark --validate-epub --epub-theme dark
```

## Output files

Typical output files:

- default: `book.epub`

Files kept with `--debug`:

- `content.html`
- `manifest.json`
- `preview-standalone.xhtml`
- `expanded.tex`
- `body.tex`
- `sanitized.tex`
- `assets/`
- `epub/`

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
python3 -m src.latex_epubifier.cli path/to/main.tex --output-dir build-dark --validate-epub --epub-theme dark
```

This version:

- keeps normal text styling neutral
- keeps figures unchanged
- swaps math snippets to white SVG assets for dark readers

## CLI flags

- `--validate-epub`
- `--debug`
- `--epub-theme auto|light|dark`

## Validation

The built-in validation checks:

- XHTML/XML well-formedness
- required EPUB files
- manifest and spine consistency
- referenced asset existence

If `epubcheck` is installed, it is used automatically during validation.
