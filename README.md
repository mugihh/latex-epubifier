# latex-epubifier

Turn LaTeX papers into e-reader-friendly XHTML and EPUB.

## Quick Start

For most papers, run this from the repository folder:

```bash
python3 convert.py path/to/main.tex
```

For example:

```bash
python3 convert.py arXiv-2410.03959v1/main.tex
```

The EPUB will be created at:

- `build/book.epub`

If you are using a dark-mode oriented e-reader and math looks too dark, try:

```bash
python3 convert.py path/to/main.tex --dark-math
```

If something looks wrong and you want troubleshooting files, add:

```bash
python3 convert.py path/to/main.tex --debug
```

## Common commands

Beginner-friendly command:

```bash
python3 convert.py path/to/main.tex
```

Advanced module command:

```bash
python3 -m src.latex_epubifier.cli path/to/main.tex --output-dir build --validate-epub
```

Advanced dark-math command:

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

- `convert.py`: `--dark-math`
- `convert.py`: `--skip-validation`
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

## Copyright and Usage

This tool converts LaTeX source into EPUB-friendly HTML and EPUB output. It does not transfer, replace, or grant any copyright in the original work.

When you convert a paper with this tool:

- the copyright of the paper remains with the original author(s), publisher, or other rights holder
- you are responsible for making sure you have permission to convert, store, read, share, or redistribute that content
- converting a document with this tool does not grant republication, distribution, or commercial-use rights

The generated EPUB is intended as a personal reading format conversion unless the original work's license or the rights holder's permission allows broader use.

## Project License

This repository is licensed under the MIT License. See [LICENSE](/Users/chyu/Desktop/code/latex-epubifier/LICENSE).

The MIT License applies to the source code of this tool only. It does not change the copyright, license, or redistribution status of any paper or other source material converted with it.
