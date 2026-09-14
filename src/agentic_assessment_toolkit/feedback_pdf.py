"""Render student feedback locally with Pandoc and Typst.

Pandoc parses Markdown and translates equations; Typst supplies the PDF
layout and bundled fonts. Author text cannot supply executable markup,
document metadata, external images, or Typst attributes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import typst


class FeedbackRenderError(Exception):
    """Feedback could not be rendered without losing content."""


def _pandoc(source: str, source_format: str, target: str) -> str:
    executable = shutil.which("pandoc")
    if executable is None:
        raise FeedbackRenderError("PDF export requires Pandoc 3.1.2 or later on PATH")
    arguments = []
    if target == "typst":
        arguments = [
            "--standalone",
            "--template",
            str(Path(__file__).parent / "templates" / "feedback.typ"),
        ]
    try:
        completed = subprocess.run(
            [
                executable,
                "--sandbox",
                "--fail-if-warnings",
                "--from",
                source_format,
                "--to",
                target,
                *arguments,
            ],
            input=source,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FeedbackRenderError(f"Pandoc failed: {error}") from error
    if completed.returncode:
        raise FeedbackRenderError(f"Pandoc failed: {completed.stderr.strip()}")
    return completed.stdout


def _normalize_math(source: str) -> str:
    """Convert grouped TeX roman declarations to Pandoc's supported notation."""
    # Consume commands and escaped characters first so literal braces and
    # backslashes cannot be mistaken for the start of a font declaration.
    return re.sub(
        r"\\[A-Za-z]+|\\.|(\{\s*\\rm(?![A-Za-z])\s*)",
        lambda match: r"\mathrm{" if match.group(1) else match.group(0),
        source,
    )


def _clean(node: Any) -> Any:
    if isinstance(node, dict):
        if node.get("t") in {"RawBlock", "RawInline", "Image"}:
            raise FeedbackRenderError("feedback must use text, tables, code, and equations only")
        cleaned = {key: _clean(value) for key, value in node.items()}
        if node.get("t") == "Math":
            cleaned["c"][1] = _normalize_math(cleaned["c"][1])
        return cleaned
    if isinstance(node, list):
        # Pandoc Attr: identifier, classes, key/value pairs. In particular,
        # typst:* attributes must never reach the Typst writer as code.
        if (
            len(node) == 3
            and isinstance(node[0], str)
            and all(isinstance(value, list) for value in node[1:])
        ):
            return ["", [], []]
        return [_clean(value) for value in node]
    return node


def render_pdf(markdown: str) -> bytes:
    """Render with fixed metadata and bundled fonts, without network access."""
    document = json.loads(
        _pandoc(
            markdown,
            "markdown-raw_tex-raw_html-raw_attribute-yaml_metadata_block"
            "-pandoc_title_block+tex_math_single_backslash",
            "json",
        )
    )
    document["meta"] = {}
    body = _pandoc(json.dumps(_clean(document)), "json", "typst")
    try:
        with tempfile.TemporaryDirectory(prefix="aat-pdf-") as directory:
            source = Path(directory) / "feedback.typ"
            source.write_text(body, encoding="utf-8")
            rendered = typst.compile(
                str(source), root=directory, ignore_system_fonts=True, timestamp=0
            )
    except Exception as error:
        raise FeedbackRenderError(f"Typst could not render feedback: {error}") from error
    if not isinstance(rendered, bytes) or not rendered.startswith(b"%PDF-"):
        raise FeedbackRenderError("Typst did not produce a PDF")
    return rendered
