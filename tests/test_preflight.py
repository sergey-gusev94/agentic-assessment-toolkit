"""The PDF preflight script: flag decisions, manifest, Dockerfile embed.

The pure decision helpers are tested by importing the script as a
module; the full pipeline runs as a real subprocess against synthetic
PDFs the script itself authors (raw-built, stdlib only). Pipeline tests
need poppler-utils and a pixel-reading backend and skip without them;
qpdf is optional to the script and to these tests.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from agentic_assessment_toolkit.config import environment_path, preflight_source_path


def _load_preflight() -> ModuleType:
    spec = importlib.util.spec_from_file_location("aat_preflight", preflight_source_path())
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = _load_preflight()

_POPPLER_TOOLS = ("pdftoppm", "pdfimages", "pdftotext")
requires_pipeline_tools = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in _POPPLER_TOOLS)
    or (importlib.util.find_spec("PIL") is None and importlib.util.find_spec("pymupdf") is None),
    reason="poppler-utils and a pixel-reading backend (Pillow or pymupdf) required",
)


def test_dockerfile_embeds_canonical_script() -> None:
    """The heredoc copy in grading.Dockerfile is byte-identical to the source.

    The grading image builds from an empty context, so the script ships
    as a heredoc; this test is what keeps that copy in sync.
    """
    dockerfile = environment_path("grading").read_text(encoding="utf-8")
    lines = dockerfile.splitlines(keepends=True)
    start = lines.index("COPY <<'PREFLIGHT_EOF' /opt/aat/preflight.py\n")
    end = lines.index("PREFLIGHT_EOF\n")
    embedded = "".join(lines[start + 1 : end])
    assert embedded == preflight_source_path().read_text(encoding="utf-8")


def test_ink_fraction_from_histogram() -> None:
    all_white = [0] * 255 + [400]
    assert preflight.ink_fraction_from_histogram(all_white) == 0.0
    half_black = [200] + [0] * 254 + [200]
    assert preflight.ink_fraction_from_histogram(half_black) == 0.5
    assert preflight.ink_fraction_from_histogram([0] * 256) == 0.0
    # 247 and above is background, 246 and below is ink.
    boundary = [0] * 256
    boundary[preflight.INK_PIXEL_MAX] = 10
    assert preflight.ink_fraction_from_histogram(boundary) == 0.0
    boundary[preflight.INK_PIXEL_MAX - 1] = 10
    assert preflight.ink_fraction_from_histogram(boundary) == 0.5


def test_sniff_kind() -> None:
    assert preflight.sniff_kind(b"%PDF-1.4\n...") == "pdf"
    assert preflight.sniff_kind(b"junk before header %PDF-1.4") == "pdf"
    assert preflight.sniff_kind(b"PK\x03\x04rest") == "zip"
    assert preflight.sniff_kind(b"\x89PNG\r\n\x1a\nrest") == "png"
    assert preflight.sniff_kind(b"\xff\xd8\xff\xe0rest") == "jpeg"
    assert preflight.sniff_kind(b"") == "empty"
    assert preflight.sniff_kind(b"\x00\x01\x02") == "binary"
    assert preflight.sniff_kind(b"\xff\xfe garbled") == "binary"
    assert preflight.sniff_kind(b"plain notes\n") == "text"


def _flags(
    ink: float | None,
    render_exists: bool,
    content_bytes: int | None,
    image_inks: list[float | None],
    drawable: int,
    *,
    renderer_ran: bool = True,
    accounting_ran: bool = True,
) -> list[str]:
    flags: list[str] = preflight.page_flags(
        ink_fraction=ink,
        render_exists=render_exists,
        renderer_ran=renderer_ran,
        content_stream_bytes=content_bytes,
        image_ink_fractions=image_inks,
        file_drawable_stream_bytes=drawable,
        content_accounting_ran=accounting_ran,
    )
    return flags


def test_page_flags_blank_render_with_substantial_content() -> None:
    # Parsed content streams above the threshold.
    assert _flags(0.0, True, 5000, [], 0) == ["DISCREPANCY"]
    # Accounting failed (malformed objects) but the raw scan shows
    # drawable streams: the overflow-/BBox case.
    assert _flags(0.0, True, None, [], 2500) == ["DISCREPANCY"]
    # The renderer dropped an embedded image that itself holds ink.
    assert _flags(0.0, True, 40, [0.6], 0) == ["DISCREPANCY"]
    # A page that did not render at all, with real content behind it.
    assert _flags(None, False, 5000, [], 0) == ["DISCREPANCY"]


def test_page_flags_do_not_fire_without_a_discrepancy() -> None:
    # A genuinely blank scanned page: near-white image, tiny content.
    assert _flags(0.0, True, 40, [0.0], 0) == []
    # A page that renders with ink is never flagged.
    assert _flags(0.3, True, 5000, [0.6], 9000) == []
    # Accounting succeeded and found little content: blank page in a
    # healthy file, even when the file holds big streams elsewhere.
    assert _flags(0.0, True, 40, [], 50000) == []
    # Unknown ink on an existing render is inconclusive.
    assert _flags(None, True, 5000, [], 5000) == []


def test_page_flags_missing_tools_are_not_evidence() -> None:
    # The renderer never ran: a missing render is not blankness.
    assert _flags(None, False, 5000, [], 0, renderer_ran=False) == []
    # Content accounting never ran: an unknown byte count does not
    # route to the raw-scan fallback, even over a big drawable file.
    assert _flags(0.0, True, None, [], 50000, accounting_ran=False) == []
    # An embedded image with real ink is still substance on its own.
    assert _flags(0.0, True, None, [0.6], 0, accounting_ran=False) == ["DISCREPANCY"]


def test_file_flags() -> None:
    flags = preflight.file_flags("ok", None, None, 500, [[], []], [])
    assert flags == []
    assert preflight.file_flags("errors", None, None, 500, [[]], []) == ["DAMAGED"]
    assert preflight.file_flags("ok", "pypdf: X", None, 500, [[]], []) == ["DAMAGED"]
    assert preflight.file_flags("ok", None, "pdftoppm exit 1", 500, [[]], []) == ["DAMAGED"]
    assert preflight.file_flags("ok", None, None, 0, [[]], []) == ["NO_TEXT_LAYER"]
    assert preflight.file_flags("warnings", None, None, 500, [["DISCREPANCY"], []], []) == [
        "DISCREPANCY"
    ]


def test_file_flags_tool_unavailability_is_distinct() -> None:
    # qpdf's own absence is its distinct check value, never DAMAGED.
    assert preflight.file_flags("unavailable", None, None, 500, [[]], []) == []
    # An unknown text total (pdftotext never ran) is not an empty layer.
    assert preflight.file_flags("ok", None, None, None, [[]], ["pdftotext"]) == ["TOOL_UNAVAILABLE"]
    assert preflight.file_flags("ok", None, None, 500, [[]], ["pdftoppm", "pypdf"]) == [
        "TOOL_UNAVAILABLE"
    ]


def test_parse_pdfimages_size() -> None:
    assert preflight.parse_pdfimages_size("256B") == 256
    assert preflight.parse_pdfimages_size("2.0K") == 2048
    assert preflight.parse_pdfimages_size("1.5M") == 1536 * 1024
    assert preflight.parse_pdfimages_size("-") == 0
    assert preflight.parse_pdfimages_size("") == 0


def test_render_dir_name_stays_within_filename_limits() -> None:
    """A deeply nested path flattens to a name a filesystem accepts."""
    deep = "/".join(["directory-" + "x" * 60] * 6) + "/" + "y" * 150 + ".pdf"
    name = preflight._render_dir_name(deep)
    assert len(name.encode("utf-8")) <= 255
    # Two paths sharing the truncated readable part stay distinct.
    other = preflight._render_dir_name(deep.replace(".pdf", "-2.pdf"))
    assert name != other
    # Determinism: the name depends only on the relative path.
    assert name == preflight._render_dir_name(deep)


def test_render_dir_name_pins_flattening_collisions() -> None:
    """a/b.pdf and a__b.pdf flatten alike; the digest keeps them apart."""
    assert preflight._render_dir_name("a/b.pdf") != preflight._render_dir_name("a__b.pdf")


def test_extracted_image_paths_skips_params_files(tmp_path: Path) -> None:
    """pdfimages -all CCITT pairs: the .params sidecar never wins the key."""
    (tmp_path / "img-001-000.ccitt").write_bytes(b"ccitt data")
    (tmp_path / "img-001-000.params").write_text("-decodedct\n", encoding="utf-8")
    (tmp_path / "img-002-001.png").write_bytes(b"\x89PNG")
    (tmp_path / "unrelated.txt").write_text("no", encoding="utf-8")
    extracted = preflight.extracted_image_paths(tmp_path)
    assert extracted == {
        (1, 0): tmp_path / "img-001-000.ccitt",
        (2, 1): tmp_path / "img-002-001.png",
    }


def test_run_treats_launch_failures_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any OS-level launch failure means the tool did not run — never a crash."""

    def boom(*_args: object, **_kwargs: object) -> object:
        raise PermissionError("not executable")

    monkeypatch.setattr(preflight.subprocess, "run", boom)
    assert preflight._run(["sometool"]) is None


def test_raw_stream_scan_on_authored_fixtures() -> None:
    overflow = preflight._fixture_overflow_bbox()
    # The form stream is drawable and large enough to count as
    # substantial even though no object parser accepts the file.
    assert preflight.raw_drawable_stream_bytes(overflow) >= preflight.SUBSTANTIAL_CONTENT_BYTES
    blank_scan = preflight._fixture_blank_scan()
    # The image stream is classified non-drawable; only the tiny
    # content stream remains.
    assert preflight.raw_drawable_stream_bytes(blank_scan) < preflight.SUBSTANTIAL_CONTENT_BYTES
    assert preflight.raw_stream_bytes(blank_scan) > preflight.raw_drawable_stream_bytes(blank_scan)


def _run_preflight(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    subprocess.run(
        [sys.executable, str(preflight_source_path()), str(input_dir), "-o", str(output_dir)],
        capture_output=True,
        check=True,
    )
    data: dict[str, Any] = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    return data


@requires_pipeline_tools
def test_pipeline_manifest_and_flags(tmp_path: Path) -> None:
    input_dir = tmp_path / "submission"
    input_dir.mkdir()
    (input_dir / "overflow_bbox.pdf").write_bytes(preflight._fixture_overflow_bbox())
    (input_dir / "nested").mkdir()
    (input_dir / "nested" / "vector.pdf").write_bytes(preflight._fixture_vector_over_image())
    (input_dir / "blank_scan.pdf").write_bytes(preflight._fixture_blank_scan())
    (input_dir / "notes.txt").write_text("plain notes\n", encoding="utf-8")
    (input_dir / "blob.bin").write_bytes(b"\x00\x01\x02")

    output_dir = tmp_path / "out"
    manifest = _run_preflight(input_dir, output_dir)
    by_path = {entry["path"]: entry for entry in manifest["files"]}
    assert list(by_path) == sorted(by_path)

    overflow = by_path["overflow_bbox.pdf"]
    assert "DISCREPANCY" in overflow["flags"]
    assert overflow["pages"][0]["flags"] == ["DISCREPANCY"]
    assert overflow["pages"][0]["ink_fraction"] < preflight.LOW_INK_FRACTION

    vector = by_path["nested/vector.pdf"]
    assert "DISCREPANCY" not in vector["flags"]
    page = vector["pages"][0]
    assert page["ink_fraction"] is not None and page["ink_fraction"] > 0.05
    assert (output_dir / page["render"]).is_file()
    assert page["image_count"] == 1
    assert page["images"][0]["md5"]

    blank = by_path["blank_scan.pdf"]
    assert "DISCREPANCY" not in blank["flags"]

    assert by_path["notes.txt"]["kind"] == "text"
    assert by_path["notes.txt"]["size_bytes"] == 12
    assert by_path["blob.bin"]["kind"] == "binary"

    text = (output_dir / "manifest.txt").read_text(encoding="utf-8")
    assert "overflow_bbox.pdf" in text
    assert "DISCREPANCY" in text


@requires_pipeline_tools
def test_pipeline_is_deterministic(tmp_path: Path) -> None:
    input_dir = tmp_path / "submission"
    input_dir.mkdir()
    (input_dir / "vector.pdf").write_bytes(preflight._fixture_vector_over_image())
    (input_dir / "notes.txt").write_text("plain notes\n", encoding="utf-8")

    _run_preflight(input_dir, tmp_path / "first")
    _run_preflight(input_dir, tmp_path / "second")
    first = (tmp_path / "first" / "manifest.json").read_bytes()
    second = (tmp_path / "second" / "manifest.json").read_bytes()
    assert first == second
    assert (tmp_path / "first" / "manifest.txt").read_bytes() == (
        tmp_path / "second" / "manifest.txt"
    ).read_bytes()


@requires_pipeline_tools
def test_pipeline_handles_deeply_nested_long_filenames(tmp_path: Path) -> None:
    """A relative path far beyond one filename's byte limit still renders."""
    input_dir = tmp_path / "submission"
    nested = input_dir
    for index in range(4):
        nested = nested / f"level-{index}-{'x' * 60}"
    nested.mkdir(parents=True)
    file_name = "y" * 120 + ".pdf"
    (nested / file_name).write_bytes(preflight._fixture_vector_over_image())

    output_dir = tmp_path / "out"
    manifest = _run_preflight(input_dir, output_dir)
    (entry,) = manifest["files"]
    assert entry["path"].endswith(file_name)
    render_dir = output_dir / entry["render_dir"]
    assert render_dir.is_dir()
    assert len(render_dir.name.encode("utf-8")) <= 255
    assert (output_dir / entry["pages"][0]["render"]).is_file()


def _hide_tool(monkeypatch: pytest.MonkeyPatch, tool: str) -> None:
    """Make preflight's tool runner report ``tool`` as not installed."""
    real_run = preflight._run
    monkeypatch.setattr(
        preflight, "_run", lambda command: None if command[0] == tool else real_run(command)
    )


@requires_pipeline_tools
def test_missing_pdftoppm_degrades_without_flagging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No renderer: pages are unknown, not blank — healthy files stay clean."""
    input_dir = tmp_path / "submission"
    input_dir.mkdir()
    (input_dir / "vector.pdf").write_bytes(preflight._fixture_vector_over_image())
    _hide_tool(monkeypatch, "pdftoppm")

    manifest = preflight.run_preflight(input_dir, tmp_path / "out")
    (entry,) = manifest["files"]
    assert entry["tools_unavailable"] == ["pdftoppm"]
    assert entry["render_error"] is None
    assert "DISCREPANCY" not in entry["flags"]
    assert "DAMAGED" not in entry["flags"]
    assert "TOOL_UNAVAILABLE" in entry["flags"]
    assert all(page["render"] is None for page in entry["pages"])


@requires_pipeline_tools
def test_missing_pdftotext_degrades_without_flagging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No text extractor: the text layer is unknown, never reported absent."""
    input_dir = tmp_path / "submission"
    input_dir.mkdir()
    (input_dir / "vector.pdf").write_bytes(preflight._fixture_vector_over_image())
    _hide_tool(monkeypatch, "pdftotext")

    manifest = preflight.run_preflight(input_dir, tmp_path / "out")
    (entry,) = manifest["files"]
    assert entry["tools_unavailable"] == ["pdftotext"]
    assert entry["text_chars_total"] is None
    assert "NO_TEXT_LAYER" not in entry["flags"]
    assert "TOOL_UNAVAILABLE" in entry["flags"]
    text = (tmp_path / "out" / "manifest.txt").read_text(encoding="utf-8")
    assert "unknown text chars" in text
    assert "tools unavailable: pdftotext" in text


@requires_pipeline_tools
def test_missing_pypdf_degrades_without_flagging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No parser: content accounting is unknown; nothing reads as damage,
    and the raw-scan fallback (evidence only when parsing ran and failed
    on the file) stays off even for the overflow-/BBox fixture."""
    input_dir = tmp_path / "submission"
    input_dir.mkdir()
    (input_dir / "vector.pdf").write_bytes(preflight._fixture_vector_over_image())
    (input_dir / "overflow_bbox.pdf").write_bytes(preflight._fixture_overflow_bbox())
    monkeypatch.setitem(sys.modules, "pypdf", None)

    manifest = preflight.run_preflight(input_dir, tmp_path / "out")
    by_path = {entry["path"]: entry for entry in manifest["files"]}
    for entry in by_path.values():
        assert entry["tools_unavailable"] == ["pypdf"]
        assert entry["parse_error"] is None
        assert "DAMAGED" not in entry["flags"]
        assert "TOOL_UNAVAILABLE" in entry["flags"]
    assert "DISCREPANCY" not in by_path["overflow_bbox.pdf"]["flags"]


def test_output_path_existing_file_is_replaced(tmp_path: Path) -> None:
    input_dir = tmp_path / "submission"
    input_dir.mkdir()
    (input_dir / "notes.txt").write_text("plain notes\n", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.write_text("stale file where the output directory goes", encoding="utf-8")

    preflight.run_preflight(input_dir, output_dir)
    assert (output_dir / "manifest.json").is_file()


@requires_pipeline_tools
def test_selftest_passes() -> None:
    completed = subprocess.run(
        [sys.executable, str(preflight_source_path()), "--selftest"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "preflight selftest ok" in completed.stdout


def test_missing_input_dir_is_an_error(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(preflight_source_path()), str(tmp_path / "missing")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "not a directory" in completed.stderr


def test_overlapping_output_dir_is_an_error(tmp_path: Path) -> None:
    input_dir = tmp_path / "submission"
    input_dir.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            str(preflight_source_path()),
            str(input_dir),
            "-o",
            str(input_dir / "out"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
