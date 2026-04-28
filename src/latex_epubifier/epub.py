from __future__ import annotations

import hashlib
import html
import mimetypes
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .utils import slug_for_content


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
