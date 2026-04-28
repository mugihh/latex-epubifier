from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path


def ensure_tex_suffix(path_text: str) -> str:
    return path_text if path_text.endswith(".tex") else f"{path_text}.tex"


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


def remove_output_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink()


def cleanup_non_debug_outputs(output_dir: Path) -> None:
    for name in [
        "expanded.tex",
        "body.tex",
        "sanitized.tex",
        "content.html",
        "preview-standalone.xhtml",
        "manifest.json",
        "assets",
        "epub",
    ]:
        remove_output_path(output_dir / name)
