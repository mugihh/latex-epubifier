from __future__ import annotations

import argparse
from pathlib import Path

from src.latex_epubifier.pipeline import main as pipeline_main


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a LaTeX paper into an EPUB with beginner-friendly defaults."
    )
    parser.add_argument("main_tex", type=Path, help="Path to the paper's main .tex file")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build"),
        help="Where to write the generated EPUB. Default: build",
    )
    parser.add_argument(
        "--dark-math",
        action="store_true",
        help="Use white math images for dark-mode oriented readers.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Keep intermediate files for troubleshooting.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip EPUB validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pipeline_args = [
        str(args.main_tex),
        "--output-dir",
        str(args.output_dir),
        "--epub-theme",
        "dark" if args.dark_math else "auto",
    ]
    if args.debug:
        pipeline_args.append("--debug")
    if not args.skip_validation:
        pipeline_args.append("--validate-epub")
    return pipeline_main(pipeline_args)


if __name__ == "__main__":
    raise SystemExit(main())
