# Environment flavor: grading (all grading tasks)
#
# Grading is static inspection: nothing from the submission or the
# reference solution is ever executed. This image carries
# document-reading tools — file/JSON inspection, PDF text extraction
# and OCR for scanned pages, image inspection and cropping, PDF
# structure inspection, spreadsheet and tabular reading, notebook
# parsing, Word and PowerPoint reading, archive unpacking, text
# extraction from binaries — plus scipy, scikit-learn, and sympy for
# the grader's own independent check calculations (its code on its own
# inputs, which the grader prompt sanctions). Everything is
# preinstalled: the grader never installs at run time, so no grade
# depends on a per-trial network fetch.
#
# mupdf-tools provides mutool, a second, independent PDF renderer and
# cleaner beside poppler: when a malformed PDF defeats one engine, the
# grader rewrites or re-renders it with the other.
#
# ImageMagick keeps Debian's default security policy, which disables
# its Ghostscript-based PDF conversion: graders rasterize untrusted
# PDFs with poppler's pdftoppm instead, so the Ghostscript attack
# surface stays closed. fonts-urw-base35 is load-bearing: ImageMagick
# resolves its default font through its own type map
# (/etc/ImageMagick-6/type-*.xml), which lists only the URW base-35
# fonts, and with --no-install-recommends none are installed — on the
# empty map `montage` and `convert -annotate` abort (SIGABRT).
# fonts-dejavu-core is not in that map and does not prevent the abort;
# it only serves explicit `-font DejaVu-Sans` use.

FROM python:3.12.11-slim-bookworm

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        binutils \
        ca-certificates \
        curl \
        file \
        fonts-dejavu-core \
        fonts-urw-base35 \
        imagemagick \
        jq \
        mupdf-tools \
        pandoc \
        poppler-data \
        poppler-utils \
        qpdf \
        ripgrep \
        tesseract-ocr \
        unzip \
        xxd \
    && rm -rf /var/lib/apt/lists/*

RUN pip install \
        pandas==2.3.1 \
        scipy==1.16.1 \
        scikit-learn==1.7.1 \
        sympy==1.14.0 \
        openpyxl==3.1.5 \
        pypdf==5.7.0 \
        pymupdf==1.28.0 \
        pikepdf==10.11.0 \
        nbformat==5.10.4 \
        python-docx==1.2.0 \
        python-pptx==1.0.2

# The PDF preflight script: the grader's mandatory first step. It
# renders every submission PDF page at a fixed DPI and writes a
# measurement manifest, so blank-looking or malformed pages are caught
# by measurement instead of impression. This image builds from an empty
# context (base_images.py), so the script cannot be COPY'd from disk;
# it is embedded here as a BuildKit heredoc, byte-identical to the
# canonical source beside this file (preflight.py) — a repository test
# keeps the two in sync.
COPY <<'PREFLIGHT_EOF' /opt/aat/preflight.py
#!/usr/bin/env python3
"""PDF preflight for grading containers: canonical renders plus a manifest.

The grader runs this before reading anything else. It walks an input
directory (default /app/submission), renders every page of every PDF to
PNG with pdftoppm at a fixed 150 DPI, extracts every embedded image
with pdfimages, and writes an output directory (default /tmp/preflight)
holding those renders and extracted images plus manifest.json and a
short human-readable manifest.txt. The renders are the canonical
reading of the submission; the manifest records, per page, the rendered
ink fraction, the extractable text count, and the embedded-image
inventory with content hashes and extracted-file paths, so claims about
blank, duplicated, or missing pages can cite measurements instead of
impressions — and a page whose render hides part of its scan can be
read from the extracted scan itself.

Flags (advisory measurements, never judgments about the work):

- DISCREPANCY (per page, echoed on the file): the page renders (nearly)
  blank while the file holds substantial content for it — drawable
  content streams above a size threshold, or an embedded image that is
  itself not blank. This catches malformed PDFs that standard renderers
  silently render blank (for example a Form XObject whose /BBox holds
  overflowed numbers). A genuinely blank scanned page — a near-white
  embedded image behind a tiny content stream — does not flag. A
  scan-sized image whose pixels no installed reader can measure (for
  example a raw CCITT extraction) counts as substantial: a failed
  measurement is never evidence of blankness.
- PARTIAL_RENDER (per page, echoed on the file): the page renders with
  real ink, yet several times less than a scan-sized embedded image on
  it holds — the signature of a scan clipped or partly hidden by the
  page layout (for example a portrait scan placed partly outside a
  landscape page). Transparency-mask rows are ignored, as are small
  images such as corner badges and logos. The extracted image named in
  the manifest holds the scan's full content.
- DAMAGED (per file): qpdf --check reports errors, the PDF library
  cannot parse the file, or the renderer fails outright.
- NO_TEXT_LAYER (per file, informational): near-zero extractable text;
  the pages are images and need OCR to read as text.
- TOOL_UNAVAILABLE (per file): a tool the measurements needed
  (pdftoppm, pdfimages, pypdf, or pdftotext) could not run; the
  affected facts are recorded as unknown (and listed in
  tools_unavailable), never fed into the flags above — a missing tool
  must not flag a healthy file.

Determinism: files are visited in sorted order, the manifest carries no
timestamps or random content, and render filenames are normalized, so
the same input directory always yields byte-identical manifests.

Standalone by design: stdlib plus the tools already in the grading
image (poppler-utils, qpdf, pypdf, and Pillow or pymupdf for pixel
counts). Each external tool degrades gracefully — a missing tool is
recorded in the manifest, never a crash. `--selftest` authors four
synthetic PDFs (an overflow-/BBox form that renders blank, vector
strokes over an embedded image, a blank scanned page, and a scan placed
partly outside its page) and asserts the flag decisions on them; the
grading image runs it at build time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

DEFAULT_INPUT_DIR = "/app/submission"
DEFAULT_OUTPUT_DIR = "/tmp/preflight"

# Canonical render resolution. 150 DPI keeps handwriting legible while
# a full submission's renders stay small enough to inspect page by page.
RENDER_DPI = 150

# A grayscale pixel strictly below this value counts as ink. 247 keeps
# faint pencil and JPEG-compressed strokes countable while off-white
# page background still reads as blank.
INK_PIXEL_MAX = 247

# A page whose ink fraction is below this renders "nearly blank".
LOW_INK_FRACTION = 0.001

# Drawable content bytes (page content streams plus reachable form
# streams, or the raw-scan fallback) at or above this count as
# substantial: enough for the vector work this flag exists to catch,
# while a genuinely blank page's content stream stays far below it.
SUBSTANTIAL_CONTENT_BYTES = 1024

# An embedded image whose own nonwhite fraction is at or above this is
# substantial content; a scan of blank paper stays below it.
SUBSTANTIAL_IMAGE_INK = 0.001

# An embedded image at least this many pixels across on both sides is
# scan-sized: a scanned sheet is thousands of pixels across, while
# corner badges and logos stay far below.
SCAN_IMAGE_MIN_PIXELS = 1000

# A page whose rendered ink falls short of a scan-sized image's
# nonwhite fraction by at least this factor renders only part of the
# scan. Measured on real submissions: clipped pages sit near a tenth of
# the image's fraction, completely rendered pages near or above it.
PARTIAL_RENDER_RATIO = 4.0

# A file whose total extractable text falls below this many
# non-whitespace characters gets the informational NO_TEXT_LAYER flag.
NO_TEXT_LAYER_CHARS = 50

FLAG_DISCREPANCY = "DISCREPANCY"
FLAG_PARTIAL_RENDER = "PARTIAL_RENDER"
FLAG_DAMAGED = "DAMAGED"
FLAG_NO_TEXT_LAYER = "NO_TEXT_LAYER"
FLAG_TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"

# Error value the tool wrappers return when the tool could not run at
# all, as opposed to running and failing on this file.
UNAVAILABLE = "unavailable"


# --- pure measurement and decision helpers (unit-tested offline) ---


def ink_fraction_from_histogram(histogram: list[int]) -> float:
    """Share of pixels that count as ink, from a 256-bin grayscale histogram."""
    total = sum(histogram)
    if total == 0:
        return 0.0
    return sum(histogram[:INK_PIXEL_MAX]) / total


def sniff_kind(head: bytes) -> str:
    """Cheap content sniff from a file's first bytes."""
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if not head:
        return "empty"
    if b"\x00" in head:
        return "binary"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


def page_is_low_ink(ink_fraction: float | None, render_exists: bool, renderer_ran: bool) -> bool:
    """A page renders nearly blank, or the renderer ran and produced none.

    An unknown ink fraction on an existing render (no pixel-reading
    backend) is inconclusive and never counts as low ink. When the
    renderer never ran (pdftoppm unavailable), a missing render says
    nothing about the page and never counts either.
    """
    if not render_exists:
        return renderer_ran
    return ink_fraction is not None and ink_fraction < LOW_INK_FRACTION


def page_has_substantial_content(
    content_stream_bytes: int | None,
    image_ink_fractions: list[float | None],
    unmeasured_scan_image: bool,
    file_drawable_stream_bytes: int,
    content_accounting_ran: bool,
) -> bool:
    """The file plausibly holds real content for this page.

    When per-page content accounting ran and failed (malformed objects
    are exactly the case this flag exists for), fall back to the file's
    raw drawable-stream scan, which needs no object parsing. When the
    accounting never ran (pypdf unavailable), an unknown byte count is
    not evidence about the file and the fallback stays off; only an
    embedded image with real ink can then establish substance. A
    scan-sized image nothing could measure also establishes substance:
    an unknown scan is never assumed blank.
    """
    if content_stream_bytes is not None and content_stream_bytes >= SUBSTANTIAL_CONTENT_BYTES:
        return True
    if any(
        fraction is not None and fraction >= SUBSTANTIAL_IMAGE_INK
        for fraction in image_ink_fractions
    ):
        return True
    if unmeasured_scan_image:
        return True
    return (
        content_accounting_ran
        and content_stream_bytes is None
        and file_drawable_stream_bytes >= SUBSTANTIAL_CONTENT_BYTES
    )


def scan_image_ink_fractions(images: list[dict[str, Any]]) -> list[float]:
    """Nonwhite fractions of the measurable scan-sized images on a page.

    Only real image rows count: pdfimages also lists transparency-mask
    rows (smask, stencil, mask), whose pixels encode alpha coverage,
    not ink — a mostly-black soft mask means transparent, and its
    fraction would read as heavy ink on a page that renders perfectly.
    """
    return [
        row["nonwhite_fraction"]
        for row in images
        if row["type"] == "image"
        and row["nonwhite_fraction"] is not None
        and row["width"] >= SCAN_IMAGE_MIN_PIXELS
        and row["height"] >= SCAN_IMAGE_MIN_PIXELS
    ]


def has_unmeasured_scan_image(images: list[dict[str, Any]]) -> bool:
    """A scan-sized image row whose pixels no reader could measure.

    Raw CCITT/JBIG2 extractions carry no image header the installed
    readers accept, so their nonwhite fraction stays unknown. For the
    substance decision that unknown must count as content: treating it
    as blank would let a fax-mode scan render blank without a flag.
    """
    return any(
        row["type"] == "image"
        and row["nonwhite_fraction"] is None
        and row["width"] >= SCAN_IMAGE_MIN_PIXELS
        and row["height"] >= SCAN_IMAGE_MIN_PIXELS
        for row in images
    )


def page_is_partial_render(
    ink_fraction: float | None, scan_image_ink_fractions: list[float]
) -> bool:
    """The page renders real ink, yet far less than its scan holds.

    Catches a scan the page layout clips or hides: the render shows the
    printed header or a fragment of the work while the embedded scan
    holds much more. A page that renders nearly blank is DISCREPANCY
    territory and never flags here; an unknown ink fraction is
    inconclusive and never flags either.
    """
    if ink_fraction is None or ink_fraction < LOW_INK_FRACTION:
        return False
    return any(
        fraction >= SUBSTANTIAL_IMAGE_INK and ink_fraction * PARTIAL_RENDER_RATIO < fraction
        for fraction in scan_image_ink_fractions
    )


def page_flags(
    ink_fraction: float | None,
    render_exists: bool,
    renderer_ran: bool,
    content_stream_bytes: int | None,
    image_ink_fractions: list[float | None],
    scan_ink_fractions: list[float],
    unmeasured_scan_image: bool,
    file_drawable_stream_bytes: int,
    content_accounting_ran: bool,
) -> list[str]:
    if page_is_low_ink(ink_fraction, render_exists, renderer_ran) and page_has_substantial_content(
        content_stream_bytes,
        image_ink_fractions,
        unmeasured_scan_image,
        file_drawable_stream_bytes,
        content_accounting_ran,
    ):
        return [FLAG_DISCREPANCY]
    if page_is_partial_render(ink_fraction, scan_ink_fractions):
        return [FLAG_PARTIAL_RENDER]
    return []


def file_flags(
    qpdf_check: str,
    parse_error: str | None,
    render_error: str | None,
    text_chars_total: int | None,
    page_flag_lists: list[list[str]],
    tools_unavailable: list[str],
) -> list[str]:
    """Per-file flags; each asserts a fact only when its tool ran.

    Callers pass parse/render errors of None and a None text total for
    tools that never ran, with those tools named in tools_unavailable —
    unavailability surfaces as TOOL_UNAVAILABLE, never as
    DAMAGED or NO_TEXT_LAYER. qpdf reports its own absence as the
    distinct check value "unavailable", which likewise never counts as
    errors here.
    """
    flags = []
    if any(FLAG_DISCREPANCY in flags_for_page for flags_for_page in page_flag_lists):
        flags.append(FLAG_DISCREPANCY)
    if any(FLAG_PARTIAL_RENDER in flags_for_page for flags_for_page in page_flag_lists):
        flags.append(FLAG_PARTIAL_RENDER)
    if qpdf_check == "errors" or parse_error is not None or render_error is not None:
        flags.append(FLAG_DAMAGED)
    if text_chars_total is not None and text_chars_total < NO_TEXT_LAYER_CHARS:
        flags.append(FLAG_NO_TEXT_LAYER)
    if tools_unavailable:
        flags.append(FLAG_TOOL_UNAVAILABLE)
    return flags


def raw_stream_bytes(data: bytes) -> int:
    """Total bytes inside stream...endstream spans, without object parsing."""
    total = 0
    for match in re.finditer(rb"\bstream\r?\n", data):
        end = data.find(b"endstream", match.end())
        if end != -1:
            total += end - match.end()
    return total


# Markers in a stream's own dictionary that classify it as something
# other than drawable page/form content: images, fonts, metadata,
# packed-object and cross-reference streams, attachments.
_NON_DRAWABLE_MARKERS = (
    b"/Image",
    b"/FontFile",
    b"/Length1",
    b"/Type1C",
    b"/CIDFontType0C",
    b"/OpenType",
    b"/Metadata",
    b"/ObjStm",
    b"/XRef",
    b"/EmbeddedFile",
)


def raw_drawable_stream_bytes(data: bytes) -> int:
    """Bytes of streams that look like page or form content, by raw scan.

    Works on files whose objects no parser accepts (the malformed-value
    case): each stream is classified by markers in the dictionary text
    immediately before it, and only unclassified — drawable — streams
    are counted, at their stored (possibly compressed) size.
    """
    total = 0
    for match in re.finditer(rb"\bstream\r?\n", data):
        end = data.find(b"endstream", match.end())
        if end == -1:
            continue
        head = data[max(0, match.start() - 2048) : match.start()]
        obj_start = head.rfind(b"obj")
        if obj_start != -1:
            head = head[obj_start:]
        if any(marker in head for marker in _NON_DRAWABLE_MARKERS):
            continue
        total += end - match.end()
    return total


def parse_pdfimages_size(size: str) -> int:
    """A pdfimages -list size field ('256B', '13.2K', '1.0M') in bytes."""
    units = {"B": 1, "K": 1024, "M": 1024 * 1024, "G": 1024 * 1024 * 1024}
    unit = units.get(size[-1:])
    if unit is None:
        return 0
    try:
        return int(float(size[:-1]) * unit)
    except ValueError:
        return 0


# --- tool wrappers ---


def _run(command: list[str]) -> subprocess.CompletedProcess[bytes] | None:
    """Run a tool; None when it could not run at all.

    Not installed, not executable, or any other OS-level launch failure:
    all count as the tool being unavailable, never a fact about the file.
    """
    try:
        return subprocess.run(command, capture_output=True, check=False)  # noqa: S603
    except OSError:
        return None


def gray_histogram(image_path: Path) -> list[int] | None:
    """256-bin grayscale histogram of an image, via Pillow or pymupdf."""
    try:
        from PIL import Image
    except ImportError:
        pass
    else:
        try:
            with Image.open(image_path) as image:
                histogram = image.convert("L").histogram()
            return histogram[:256]
        except Exception:  # noqa: BLE001 - unreadable image, fall through
            pass
    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        pixmap = pymupdf.Pixmap(str(image_path))
        if pixmap.n != 1:
            pixmap = pymupdf.Pixmap(pymupdf.csGRAY, pixmap)
        histogram = [0] * 256
        for value in pixmap.samples:
            histogram[value] += 1
        return histogram
    except Exception:  # noqa: BLE001 - unreadable image
        return None


def image_ink_fraction(image_path: Path) -> float | None:
    histogram = gray_histogram(image_path)
    if histogram is None:
        return None
    return ink_fraction_from_histogram(histogram)


def qpdf_check(pdf_path: Path) -> str:
    completed = _run(["qpdf", "--check", str(pdf_path)])
    if completed is None:
        return "unavailable"
    if completed.returncode == 0:
        return "ok"
    if completed.returncode == 3:
        return "warnings"
    return "errors"


def render_pdf(pdf_path: Path, render_dir: Path) -> tuple[dict[int, str], str | None]:
    """Render every page to page-NNNN.png at RENDER_DPI.

    Returns page number -> render filename, plus an error: UNAVAILABLE
    when pdftoppm could not run, its exit status when it failed, None on
    success.
    """
    render_dir.mkdir(parents=True, exist_ok=True)
    completed = _run(
        ["pdftoppm", "-r", str(RENDER_DPI), "-png", str(pdf_path), str(render_dir / "page")]
    )
    renders: dict[int, str] = {}
    for produced in sorted(render_dir.glob("page-*.png")):
        match = re.fullmatch(r"page-(\d+)\.png", produced.name)
        if match is None:
            continue
        page_number = int(match.group(1))
        canonical = render_dir / f"page-{page_number:04d}.png"
        if produced.name != canonical.name:
            produced.rename(canonical)
        renders[page_number] = canonical.name
    if completed is None:
        return renders, UNAVAILABLE
    if completed.returncode != 0:
        return renders, f"pdftoppm exit {completed.returncode}"
    return renders, None


def page_text_chars(pdf_path: Path, page_number: int) -> tuple[int | None, bool]:
    """(non-whitespace character count, whether pdftotext ran).

    The count is None both when extraction failed and when the tool is
    missing; the second element distinguishes the two, so a missing
    pdftotext never reads as an empty text layer.
    """
    completed = _run(
        [
            "pdftotext",
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-enc",
            "UTF-8",
            str(pdf_path),
            "-",
        ]
    )
    if completed is None:
        return None, False
    if completed.returncode != 0:
        return None, True
    text = completed.stdout.decode("utf-8", errors="replace")
    return sum(1 for character in text if not character.isspace()), True


def extracted_image_paths(extract_dir: Path) -> dict[tuple[int, int], Path]:
    """(page, image index) -> the file pdfimages -all extracted for it.

    pdfimages -all writes a CCITT image as an img-P-N.ccitt plus
    img-P-N.params pair; the .params file holds decoding parameters,
    never image data, so it is skipped — otherwise its md5 and nonwhite
    fraction would clobber the real image's under the same key. First
    match wins as a further guard against any same-key pair.
    """
    extracted: dict[tuple[int, int], Path] = {}
    for image_path in sorted(extract_dir.iterdir()):
        if image_path.suffix == ".params":
            continue
        match = re.fullmatch(r"img-(\d+)-(\d+)\.[a-z0-9]+", image_path.name)
        if match is None:
            continue
        extracted.setdefault((int(match.group(1)), int(match.group(2))), image_path)
    return extracted


def pdf_image_inventory(
    pdf_path: Path, images_dir: Path, images_dir_prefix: str
) -> tuple[dict[int, list[dict[str, Any]]], bool]:
    """Per-page embedded images: pdfimages -list joined with extraction.

    Each entry carries the listed geometry and embedded size, plus the
    path, md5, and nonwhite fraction of the image as pdfimages -all
    extracts it into images_dir. The extracted files stay in the output
    directory as an alternate reading of the page, so a scan the render
    clips or hides can be read directly from its extraction. Empty when
    pdfimages is missing or lists nothing — which itself is evidence:
    extraction-only reading misses content that rendering shows. The
    second element reports whether pdfimages ran at all; its absence is
    a missing tool, never a fact about the file.
    """
    listed = _run(["pdfimages", "-list", str(pdf_path)])
    inventory: dict[int, list[dict[str, Any]]] = {}
    if listed is None:
        return inventory, False
    if listed.returncode != 0:
        return inventory, True
    rows: list[dict[str, Any]] = []
    for line in listed.stdout.decode("utf-8", errors="replace").splitlines()[2:]:
        fields = line.split()
        if len(fields) < 5:
            continue
        try:
            rows.append(
                {
                    "page": int(fields[0]),
                    "index": int(fields[1]),
                    "type": fields[2],
                    "width": int(fields[3]),
                    "height": int(fields[4]),
                    "embedded_bytes": parse_pdfimages_size(fields[-2]),
                }
            )
        except ValueError:
            continue

    if not rows:
        return inventory, True
    images_dir.mkdir(parents=True, exist_ok=True)
    _run(["pdfimages", "-p", "-all", str(pdf_path), str(images_dir / "img")])
    extracted = extracted_image_paths(images_dir)
    for row in rows:
        extracted_path = extracted.get((row["page"], row["index"]))
        if extracted_path is None:
            row["extracted_path"] = None
            row["extracted_bytes"] = None
            row["md5"] = None
            row["nonwhite_fraction"] = None
        else:
            data = extracted_path.read_bytes()
            row["extracted_path"] = f"{images_dir_prefix}/{extracted_path.name}"
            row["extracted_bytes"] = len(data)
            row["md5"] = hashlib.md5(data).hexdigest()  # noqa: S324 - content id, not security
            fraction = image_ink_fraction(extracted_path)
            row["nonwhite_fraction"] = None if fraction is None else round(fraction, 6)
        inventory.setdefault(row["page"], []).append(row)
    for images in inventory.values():
        images.sort(key=lambda row: row["index"])
    return inventory, True


def pdf_content_stream_bytes(pdf_path: Path) -> tuple[int | None, list[int | None], str | None]:
    """(page count, per-page drawable decoded bytes, whole-file parse error).

    Per-page bytes cover the page's content streams plus every form
    XObject stream reachable from its resources, decoded. Accounting is
    strict on purpose: any malformed object in that chain yields None
    for the page, which routes the substance decision to the raw-scan
    fallback instead of silently undercounting — the exact failure mode
    of the overflow-/BBox files this script exists to catch. The parse
    error is UNAVAILABLE when pypdf itself is not installed — no fact
    about the file.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, [], UNAVAILABLE
    try:
        reader = PdfReader(str(pdf_path))
        page_count = len(reader.pages)
    except Exception as error:  # noqa: BLE001 - malformed input file
        return None, [], f"pypdf: {type(error).__name__}"
    per_page: list[int | None] = []
    for page in reader.pages:
        try:
            total = 0
            contents = page.get_contents()
            if contents is not None:
                total += len(contents.get_data())
            total += _form_stream_bytes(page.get("/Resources"), set())
            per_page.append(total)
        except Exception:  # noqa: BLE001 - malformed page objects
            per_page.append(None)
    return page_count, per_page, None


def _form_stream_bytes(resources: Any, seen: set[int]) -> int:
    if resources is None:
        return 0
    xobjects = resources.get_object().get("/XObject")
    if xobjects is None:
        return 0
    total = 0
    xobjects = xobjects.get_object()
    for name in sorted(xobjects.keys()):
        xobject = xobjects[name].get_object()
        if xobject.get("/Subtype") != "/Form" or id(xobject) in seen:
            continue
        seen.add(id(xobject))
        total += len(xobject.get_data())
        total += _form_stream_bytes(xobject.get("/Resources"), seen)
    return total


# --- manifest assembly ---


def _derived_dir_name(relative_path: str) -> str:
    """A flat, collision-free directory name for one PDF's derived files.

    The readable part is truncated to 100 bytes so the name always fits
    a 255-byte filename limit; the digest — over the full relative path —
    keeps names unique when truncation (or flattening itself, as in
    a/b.pdf versus a__b.pdf) makes readable parts coincide.
    """
    digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:8]
    flat = relative_path.replace("/", "__").encode("utf-8")[:100].decode("utf-8", "ignore")
    return f"{flat}-{digest}"


def analyze_pdf(pdf_path: Path, relative_path: str, output_dir: Path) -> dict[str, Any]:
    data = pdf_path.read_bytes()
    derived_dir_name = _derived_dir_name(relative_path)
    render_dir = output_dir / "renders" / derived_dir_name
    renders, render_error = render_pdf(pdf_path, render_dir)
    page_count, per_page_content, parse_error = pdf_content_stream_bytes(pdf_path)
    inventory, pdfimages_ran = pdf_image_inventory(
        pdf_path, output_dir / "images" / derived_dir_name, f"images/{derived_dir_name}"
    )
    drawable_bytes = raw_drawable_stream_bytes(data)

    # A tool that could not run leaves its facts unknown: it is listed
    # in tools_unavailable (surfacing as TOOL_UNAVAILABLE) and its
    # error slot is cleared so it never reads as damage to the file.
    tools_unavailable = []
    renderer_ran = render_error != UNAVAILABLE
    if not renderer_ran:
        tools_unavailable.append("pdftoppm")
        render_error = None
    parser_ran = parse_error != UNAVAILABLE
    if not parser_ran:
        tools_unavailable.append("pypdf")
        parse_error = None
    if not pdfimages_ran:
        tools_unavailable.append("pdfimages")

    last_page = max(
        page_count or 0, max(renders, default=0), max(inventory, default=0), len(per_page_content)
    )
    pages = []
    page_flag_lists = []
    text_chars_seen = 0
    pdftotext_ran = True
    image_count_total = 0
    image_bytes_total = 0
    for page_number in range(1, last_page + 1):
        render_name = renders.get(page_number)
        render_path = None if render_name is None else f"renders/{derived_dir_name}/{render_name}"
        ink = None if render_name is None else image_ink_fraction(render_dir / render_name)
        text_chars, text_tool_ran = page_text_chars(pdf_path, page_number)
        pdftotext_ran = pdftotext_ran and text_tool_ran
        text_chars_seen += text_chars or 0
        images = inventory.get(page_number, [])
        image_count_total += len(images)
        page_image_bytes = sum(row["embedded_bytes"] for row in images)
        image_bytes_total += page_image_bytes
        content_bytes = None
        if page_number <= len(per_page_content):
            content_bytes = per_page_content[page_number - 1]
        flags = page_flags(
            ink_fraction=ink,
            render_exists=render_name is not None,
            renderer_ran=renderer_ran,
            content_stream_bytes=content_bytes,
            image_ink_fractions=[row["nonwhite_fraction"] for row in images],
            scan_ink_fractions=scan_image_ink_fractions(images),
            unmeasured_scan_image=has_unmeasured_scan_image(images),
            file_drawable_stream_bytes=drawable_bytes,
            content_accounting_ran=parser_ran,
        )
        page_flag_lists.append(flags)
        pages.append(
            {
                "page": page_number,
                "render": render_path,
                "ink_fraction": None if ink is None else round(ink, 6),
                "text_chars": text_chars,
                "content_stream_bytes": content_bytes,
                "image_count": len(images),
                "image_bytes_total": page_image_bytes,
                "images": images,
                "flags": flags,
            }
        )

    if not pdftotext_ran:
        tools_unavailable.append("pdftotext")
    # None means unknown: pdftotext never ran, so a total of zero would
    # be an assertion the tool did not make.
    text_chars_total = text_chars_seen if pdftotext_ran else None
    tools_unavailable.sort()

    check = qpdf_check(pdf_path)
    return {
        "path": relative_path,
        "kind": "pdf",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "qpdf_check": check,
        "parse_error": parse_error,
        "render_error": render_error,
        "tools_unavailable": tools_unavailable,
        "page_count": last_page,
        "render_dir": f"renders/{derived_dir_name}",
        # None when nothing was extracted: the directory then does not
        # exist, and a path pointing at nothing would be a false claim.
        "images_dir": f"images/{derived_dir_name}" if inventory else None,
        "raw_stream_bytes": raw_stream_bytes(data),
        "drawable_stream_bytes": drawable_bytes,
        "image_count": image_count_total,
        "image_bytes_total": image_bytes_total,
        "text_chars_total": text_chars_total,
        "pages": pages,
        "flags": file_flags(
            check,
            parse_error,
            render_error,
            text_chars_total,
            page_flag_lists,
            tools_unavailable,
        ),
    }


def analyze_other_file(path: Path, relative_path: str, kind: str) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": relative_path,
        "kind": kind,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "flags": [],
    }


def build_manifest(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    entries = []
    for path in sorted(input_dir.rglob("*"), key=lambda p: p.relative_to(input_dir).as_posix()):
        if not path.is_file():
            continue
        relative_path = path.relative_to(input_dir).as_posix()
        with path.open("rb") as handle:
            head = handle.read(1024)
        kind = sniff_kind(head)
        if kind == "pdf":
            entries.append(analyze_pdf(path, relative_path, output_dir))
        else:
            entries.append(analyze_other_file(path, relative_path, kind))
    return {
        "schema_version": SCHEMA_VERSION,
        "input_dir": str(input_dir),
        "render_dpi": RENDER_DPI,
        "thresholds": {
            "ink_pixel_max": INK_PIXEL_MAX,
            "low_ink_fraction": LOW_INK_FRACTION,
            "substantial_content_bytes": SUBSTANTIAL_CONTENT_BYTES,
            "substantial_image_ink": SUBSTANTIAL_IMAGE_INK,
            "scan_image_min_pixels": SCAN_IMAGE_MIN_PIXELS,
            "partial_render_ratio": PARTIAL_RENDER_RATIO,
            "no_text_layer_chars": NO_TEXT_LAYER_CHARS,
        },
        "summary": {
            "pdf_files": sum(1 for entry in entries if entry["kind"] == "pdf"),
            "other_files": sum(1 for entry in entries if entry["kind"] != "pdf"),
            "flagged_files": sum(1 for entry in entries if entry["flags"]),
        },
        "files": entries,
    }


def manifest_text(manifest: dict[str, Any]) -> str:
    """The short human-readable summary written as manifest.txt."""
    summary = manifest["summary"]
    lines = [
        f"preflight manifest (schema {manifest['schema_version']})",
        f"input: {manifest['input_dir']}",
        f"{summary['pdf_files']} PDF file(s), {summary['other_files']} other file(s), "
        f"{summary['flagged_files']} flagged file(s)",
        "",
    ]
    for entry in manifest["files"]:
        if entry["kind"] != "pdf":
            lines.append(
                f"{entry['path']}: {entry['kind']}, {entry['size_bytes']} bytes (not a PDF)"
            )
            continue
        flags = ", ".join(entry["flags"]) or "none"
        text_total = (
            "unknown" if entry["text_chars_total"] is None else str(entry["text_chars_total"])
        )
        lines.append(
            f"{entry['path']}: {entry['page_count']} page(s), qpdf {entry['qpdf_check']}, "
            f"{entry['image_count']} image(s), {text_total} text chars, "
            f"flags: {flags}"
        )
        lines.append(f"  renders: {entry['render_dir']}/")
        if entry["tools_unavailable"]:
            lines.append(f"  tools unavailable: {', '.join(entry['tools_unavailable'])}")
        for page in entry["pages"]:
            if not page["flags"]:
                continue
            ink = "unknown" if page["ink_fraction"] is None else f"{page['ink_fraction']:.6f}"
            content = (
                "unknown"
                if page["content_stream_bytes"] is None
                else str(page["content_stream_bytes"])
            )
            lines.append(
                f"  page {page['page']}: ink {ink}, text {page['text_chars']}, "
                f"images {page['image_count']} ({page['image_bytes_total']} bytes), "
                f"content {content} bytes, flags: {', '.join(page['flags'])}"
            )
    lines.append("")
    return "\n".join(lines)


def run_preflight(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    input_resolved = input_dir.resolve()
    output_resolved = output_dir.resolve()
    if (
        input_resolved == output_resolved
        or input_resolved in output_resolved.parents
        or output_resolved in input_resolved.parents
    ):
        raise ValueError("output directory must not overlap the input directory")
    if output_dir.is_symlink() or output_dir.exists():
        # The output path may exist as anything: replace a stale
        # directory tree, but also a plain file or symlink left there.
        if output_dir.is_dir() and not output_dir.is_symlink():
            shutil.rmtree(output_dir)
        else:
            output_dir.unlink()
    output_dir.mkdir(parents=True)
    manifest = build_manifest(input_dir, output_dir)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "manifest.txt").write_text(manifest_text(manifest), encoding="utf-8")
    return manifest


# --- selftest fixtures: raw-authored PDFs, stdlib only ---


def _raw_pdf(objects: list[bytes]) -> bytes:
    """A complete PDF with a correct xref table; objects[i] is object i+1."""
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_position = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _stream_obj(dictionary: str, data: bytes) -> bytes:
    return (
        f"<< {dictionary} /Length {len(data)} >>\n".encode() + b"stream\n" + data + b"\nendstream"
    )


def _page_objects(resources: str, content: bytes, resource_obj: bytes) -> list[bytes]:
    return [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        + f"/Resources << /XObject << {resources} >> >> /Contents 4 0 R >>".encode(),
        _stream_obj("", content),
        resource_obj,
    ]


def _strokes(count: int) -> bytes:
    lines = [
        f"72 {80 + (index * 7) % 600} m 540 {83 + (index * 7) % 600} l S" for index in range(count)
    ]
    return ("2 w 0 g\n" + "\n".join(lines) + "\n").encode()


def _fixture_overflow_bbox() -> bytes:
    """Renders blank in standard renderers: form /BBox of overflowed integers."""
    big = "9" * 309
    form = _stream_obj(f"/Type /XObject /Subtype /Form /BBox [0 0 {big} {big}]", _strokes(120))
    return _raw_pdf(_page_objects("/Fx0 5 0 R", b"q /Fx0 Do Q\n", form))


def _gray_image_obj(value: int) -> bytes:
    return _stream_obj(
        "/Type /XObject /Subtype /Image /Width 16 /Height 16 "
        "/ColorSpace /DeviceGray /BitsPerComponent 8",
        bytes([value]) * (16 * 16),
    )


def _fixture_vector_over_image() -> bytes:
    """Vector strokes over an embedded gray background image: real ink."""
    content = b"q 468 0 0 648 72 72 cm /Im0 Do Q\n" + _strokes(40)
    return _raw_pdf(_page_objects("/Im0 5 0 R", content, _gray_image_obj(0xB0)))


def _fixture_blank_scan() -> bytes:
    """A scan of blank paper: a near-white full-page image, nothing else."""
    content = b"q 468 0 0 648 72 72 cm /Im0 Do Q\n"
    return _raw_pdf(_page_objects("/Im0 5 0 R", content, _gray_image_obj(0xFF)))


def _clipped_scan_image_obj() -> bytes:
    """A 1200x1600 1-bit scan: a little ink in the top half, most of it
    in the bottom half that the clipped-scan fixture pushes off-page."""
    width, height = 1200, 1600
    white_row = b"\xff" * (width // 8)
    black_row = b"\x00" * (width // 8)
    rows = [
        black_row if 40 <= index < 48 or 900 <= index < 1100 else white_row
        for index in range(height)
    ]
    return _stream_obj(
        f"/Type /XObject /Subtype /Image /Width {width} /Height {height} "
        "/ColorSpace /DeviceGray /BitsPerComponent 1",
        b"".join(rows),
    )


def _fixture_clipped_scan() -> bytes:
    """Renders only part of its scan: the portrait scan is placed so its
    lower half — holding most of the ink — lies below the page."""
    content = b"q 612 0 0 1584 0 -792 cm /Im0 Do Q\n"
    return _raw_pdf(_page_objects("/Im0 5 0 R", content, _clipped_scan_image_obj()))


def selftest() -> int:
    """Author the four fixtures, run the pipeline, assert the decisions."""
    with tempfile.TemporaryDirectory() as scratch:
        input_dir = Path(scratch) / "input"
        input_dir.mkdir()
        (input_dir / "overflow_bbox.pdf").write_bytes(_fixture_overflow_bbox())
        (input_dir / "vector_over_image.pdf").write_bytes(_fixture_vector_over_image())
        (input_dir / "blank_scan.pdf").write_bytes(_fixture_blank_scan())
        (input_dir / "clipped_scan.pdf").write_bytes(_fixture_clipped_scan())

        manifest = run_preflight(input_dir, Path(scratch) / "out")
        repeat = run_preflight(input_dir, Path(scratch) / "out2")

        failures = []
        by_path = {entry["path"]: entry for entry in manifest["files"]}

        overflow = by_path["overflow_bbox.pdf"]
        if FLAG_DISCREPANCY not in overflow["flags"]:
            failures.append("overflow_bbox.pdf must flag DISCREPANCY")

        vector = by_path["vector_over_image.pdf"]
        vector_page = vector["pages"][0]
        if vector_page["ink_fraction"] is None or vector_page["ink_fraction"] < 0.05:
            failures.append("vector_over_image.pdf page 1 must render with substantial ink")
        if vector_page["render"] is None:
            failures.append("vector_over_image.pdf page 1 must have a render")
        if FLAG_DISCREPANCY in vector["flags"]:
            failures.append("vector_over_image.pdf must not flag DISCREPANCY")
        if vector["image_count"] != 1 or not vector_page["images"][0]["md5"]:
            failures.append("vector_over_image.pdf must inventory its embedded image with an md5")

        blank = by_path["blank_scan.pdf"]
        if FLAG_DISCREPANCY in blank["flags"]:
            failures.append("blank_scan.pdf must not flag DISCREPANCY")

        clipped = by_path["clipped_scan.pdf"]
        if FLAG_PARTIAL_RENDER not in clipped["flags"]:
            failures.append("clipped_scan.pdf must flag PARTIAL_RENDER")
        if FLAG_DISCREPANCY in clipped["flags"]:
            failures.append("clipped_scan.pdf must not flag DISCREPANCY")
        clipped_image = clipped["pages"][0]["images"][0]
        if (
            clipped_image["extracted_path"] is None
            or not (Path(scratch) / "out" / clipped_image["extracted_path"]).is_file()
        ):
            failures.append("clipped_scan.pdf must keep its extracted scan in the output")

        if manifest != repeat:
            failures.append("manifest must be deterministic across runs")

        if failures:
            for failure in failures:
                print(f"selftest failure: {failure}", file=sys.stderr)
            return 1
    print("preflight selftest ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render every PDF page and write a measurement manifest."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=DEFAULT_INPUT_DIR,
        help=f"directory to preflight (default {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUTPUT_DIR,
        help=f"output directory for renders and manifest (default {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="author synthetic fixtures and assert the flag decisions",
    )
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"error: not a directory: {input_dir}", file=sys.stderr)
        return 2
    try:
        manifest = run_preflight(input_dir, Path(args.output))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(manifest_text(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
PREFLIGHT_EOF

# Build-time smoke test: a tool can install cleanly yet be broken at
# run time — ImageMagick in a font-less image aborted every `montage`
# call across two full grading runs while `identify` and plain
# `convert` worked. Every inspection capability the grader prompt
# advertises must prove itself here, so a broken tool fails the build
# instead of surfacing mid-grading. The preflight selftest authors a
# PDF whose form /BBox holds overflowed integers (renders blank while
# holding a sizable stream — must flag DISCREPANCY), a PDF of vector
# strokes over an embedded image (must render with nonzero ink), and a
# blank scanned page (must not flag).
RUN echo "smoke: convert" \
    && convert -size 60x60 xc:white /tmp/a.png \
    && convert -size 60x60 xc:gray /tmp/b.png \
    && echo "smoke: montage" \
    && montage /tmp/a.png /tmp/b.png -tile 2x1 -geometry +2+2 /tmp/sheet.png \
    && identify /tmp/sheet.png \
    && echo "smoke: annotate" \
    && convert /tmp/a.png -annotate +6+30 ok /tmp/annotated.png \
    && echo "smoke: ocr" \
    && convert -size 240x80 xc:white -pointsize 40 -annotate +20+55 OCR /tmp/ocr.png \
    && tesseract /tmp/ocr.png /tmp/ocr_out \
    && echo "smoke: pdf" \
    && python -c "from pypdf import PdfWriter; w = PdfWriter(); w.add_blank_page(width=200, height=200); w.write('/tmp/t.pdf')" \
    && pdftoppm -png /tmp/t.pdf /tmp/t_page \
    && pdfimages -list /tmp/t.pdf \
    && qpdf --qdf /tmp/t.pdf /tmp/t_qdf.pdf \
    && echo "smoke: mutool" \
    && mutool clean /tmp/t.pdf /tmp/t_mutool.pdf \
    && mutool draw -o /tmp/t_mutool.png -r 72 /tmp/t.pdf \
    && echo "smoke: python readers" \
    && python -c "import pymupdf, pikepdf, pandas, scipy, sklearn, sympy, openpyxl, nbformat, docx, pptx" \
    && echo "smoke: preflight" \
    && python3 /opt/aat/preflight.py --selftest \
    && echo "smoke: binary tools" \
    && strings /bin/ls > /dev/null \
    && xxd -l 16 /bin/ls > /dev/null \
    && rm -f /tmp/a.png /tmp/b.png /tmp/sheet.png /tmp/annotated.png \
        /tmp/ocr.png /tmp/ocr_out.txt /tmp/t.pdf /tmp/t_qdf.pdf \
        /tmp/t_mutool.pdf /tmp/t_mutool.png /tmp/t_page*

# Preinstalled agent runtime: pinned Node and Codex, so Harbor's
# agent-install step finds `codex` on PATH and becomes a no-op. This
# removes the per-trial network install (whose remote Node lookup can
# fail a trial mid-run) and pins the agent version into the image bytes
# instead of letting each trial resolve `@latest`. ripgrep above is
# what that install step would have added alongside. linux-x64: images
# are built and run on x86_64.
ENV NODE_VERSION=22.23.2 \
    CODEX_VERSION=0.146.0
RUN curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz" \
    && curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" \
    && grep " node-v${NODE_VERSION}-linux-x64.tar.gz\$" SHASUMS256.txt | sha256sum -c - \
    && tar -xzf "node-v${NODE_VERSION}-linux-x64.tar.gz" -C /usr/local --strip-components=1 --no-same-owner \
    && rm "node-v${NODE_VERSION}-linux-x64.tar.gz" SHASUMS256.txt \
    && npm install -g "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force \
    && node --version \
    && codex --version

# The grading task layout presents an empty output directory the grader
# must fill (docs/design.md, "Grading task layout").
RUN mkdir -p /app/grading_output

WORKDIR /app
