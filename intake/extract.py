"""Traceable synthetic PDF intake with native text and local macOS OCR.

This module extracts *candidate observations*, never diagnoses or authoritative
case facts. It deliberately refuses real-case use until an intake gate exists.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import date as CalendarDate
from decimal import Decimal

import pymupdf


LAB = re.compile(
    r"^\s*(Creatinina|Glucosa|Hemoglobina)\s*[:=]?\s*"
    r"(\d+(?:[.,]\d+)?)\s*(mg/dL|g/dL)\b"
    r"(?:\s*\(\s*(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*\))?",
    re.IGNORECASE,
)
DATE = re.compile(r"\bFecha(?:\s+de\s+toma|\s+toma)?\s*[:=]\s*(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_PAGES = 250
PROJECT = Path(__file__).resolve().parents[1]
PROTECTED_ROOTS = [PROJECT / "Gabriela_Gutierrez", PROJECT / "cases/private", PROJECT / "case-store"]
MARKER = "SYNTHETIC_INTAKE_FIXTURE_V1"
SYNTHETIC_CASE = re.compile(r"SYNTH-[0-9]{3,6}\Z")


def check_fixture(path, fixture_root):
    if fixture_root is None:
        raise ValueError("an explicit marked synthetic fixture root is required")
    root = Path(fixture_root).resolve(strict=True)
    path = Path(path).resolve(strict=True)
    if not root.is_dir() or not path.is_relative_to(root):
        raise ValueError("source/output must be inside the synthetic fixture root")
    if any(root.is_relative_to(protected) or path.is_relative_to(protected) for protected in PROTECTED_ROOTS):
        raise ValueError("private or case-store paths cannot be synthetic fixtures")
    marker_file = root / ".synthetic-fixture"
    if not marker_file.is_file() or marker_file.read_text(encoding="utf-8").strip() != MARKER:
        raise ValueError("synthetic fixture marker mismatch")
    return path


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def to_decimal(text):
    return str(Decimal(text.replace(",", ".")))


def parse_lines(lines, *, source_id, page_number, channel, date):
    candidates = []
    for line_number, line in enumerate(lines, start=1):
        match = LAB.match(line["text"])
        if not match:
            continue
        analyte, value, unit, low, high = match.groups()
        candidate = {
            "id": f"LAB-{source_id}-P{page_number}-{channel}-{line_number}",
            "status": "unverified_candidate",
            "analyte_original": analyte,
            "analyte_key": analyte.casefold(),
            "value_original": value,
            "value_decimal": to_decimal(value),
            "unit_original": unit,
            "reference_range_original": f"{low} - {high}" if low and high else None,
            "collection_date": date,
            "source": {
                "document_id": source_id, "page": page_number,
                "channel": channel, "line": line_number,
                "exact_text": line["text"], "bbox": line.get("bbox"),
                "ocr_confidence": line.get("confidence"),
            },
        }
        candidates.append(candidate)
    return candidates


def ocr_page(page):
    if sys.platform != "darwin":
        raise RuntimeError("Local Apple Vision OCR requires macOS; no fallback configured")
    if page.rect.width * 2 > 5000 or page.rect.height * 2 > 5000:
        raise RuntimeError("page is too large for local OCR; manual review required")
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    with tempfile.TemporaryDirectory(prefix="synthetic-ocr-") as tmp:
        image = Path(tmp) / "page.png"
        pix.save(image)
        command = ["swift", str(Path(__file__).with_name("vision_ocr.swift")), str(image)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Apple Vision OCR unavailable: " + result.stderr.strip()[-400:])
    lines = json.loads(result.stdout)["lines"]
    if not isinstance(lines, list):
        raise ValueError("invalid OCR output")
    return lines


def image_coverage(page):
    """Estimate image area; overlap can exceed one full page, hence clamp."""
    area = page.rect.width * page.rect.height
    if not area:
        return 0.0
    rectangles = []
    for image in page.get_images(full=True):
        for rect in page.get_image_rects(image[0]):
            rectangles.append(rect & page.rect)
    return min(1.0, sum(r.width * r.height for r in rectangles) / area)


def extract_pdf(path, *, fixture_root=None, synthetic_only=True, ocr=ocr_page):
    if not synthetic_only:
        raise ValueError("real-case intake is disabled; only synthetic sources are supported")
    path = check_fixture(path, fixture_root)
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise ValueError("expected a PDF file")
    if path.stat().st_size > MAX_FILE_SIZE:
        raise ValueError("PDF exceeds prototype size limit")
    with path.open("rb") as file:
        if not file.read(5).startswith(b"%PDF-"):
            raise ValueError("file is not a PDF, regardless of filename")
    digest = checksum(path)
    source_id = "SRC-" + digest[:16]
    document = {"source_id": source_id, "filename": path.name, "sha256": digest, "pages": [], "candidates": [], "warnings": []}
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass:
            raise ValueError("password-protected PDF requires explicit handling")
        if len(pdf) > MAX_PAGES:
            raise ValueError("PDF exceeds prototype page limit")
        for number, page in enumerate(pdf, start=1):
            native = [{"text": text} for text in page.get_text("text", sort=True).splitlines() if text.strip()]
            coverage = image_coverage(page)
            use_ocr = not native or coverage >= 0.3
            ocr_lines = []
            warnings = []
            if use_ocr:
                try:
                    ocr_lines = ocr(page)
                except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError) as exc:
                    warnings.append("OCR failed; page requires manual review: " + type(exc).__name__)
            if coverage >= 0.3 and native:
                warnings.append("mixed image/text page; compare both channels manually")
            if not native and not ocr_lines:
                warnings.append("no extracted text; page may be blank or requires manual review")
            if any(line.get("confidence", 1) < 0.7 for line in ocr_lines):
                warnings.append("low-confidence OCR line(s) require source inspection")
            dates = {m.group(1) for line in native + ocr_lines for m in [DATE.search(line["text"])] if m}
            date = next(iter(dates)) if len(dates) == 1 else None
            if len(dates) > 1:
                warnings.append("conflicting dates on page; do not assign collection date")
            if date:
                try:
                    CalendarDate.fromisoformat(date)
                except ValueError:
                    warnings.append("invalid collection date; do not assign")
                    date = None
            if not dates:
                warnings.append("no explicit ISO collection date found on page")
            page_record = {
                "page": number, "native_lines": native, "ocr_lines": ocr_lines,
                "image_coverage_estimate": round(coverage, 3),
                "collection_date_candidate": date, "warnings": warnings,
                "requires_review": bool(use_ocr or warnings),
            }
            document["pages"].append(page_record)
            document["candidates"].extend(parse_lines(native, source_id=source_id, page_number=number, channel="native", date=date))
            document["candidates"].extend(parse_lines(ocr_lines, source_id=source_id, page_number=number, channel="ocr", date=date))
    document["format"] = "pdf"
    document["blocks"] = [
        {
            "id": f"{source_id}:p{page['page']}:{channel}:{number}",
            "type": "text_line", "text": line["text"],
            "locator": {"page": page["page"], "channel": channel, "line": number,
                        "bbox": line.get("bbox"), "ocr_confidence": line.get("confidence")},
        }
        for page in document["pages"]
        for channel, lines in (("native", page["native_lines"]), ("ocr", page["ocr_lines"]))
        for number, line in enumerate(lines, 1)
    ]
    for candidate in document["candidates"]:
        ref = candidate["source"]
        ref["block_id"] = f"{source_id}:p{ref['page']}:{ref['channel']}:{ref['line']}"
    return document


def reconcile(documents):
    """Flag collisions; never choose one value or merge differently sourced facts."""
    grouped = {}
    for document in documents:
        for item in document["candidates"]:
            # A missing date is never used to merge observations across documents.
            group = (item["analyte_key"], item["collection_date"], item["unit_original"].casefold())
            if item["collection_date"] is None:
                group += (document["source_id"], item["source"]["page"])
            grouped.setdefault(group, []).append(item)
    conflicts = []
    for group, items in grouped.items():
        if len({item["value_decimal"] for item in items}) > 1:
            conflicts.append({"analyte": group[0], "date": group[1], "status": "unresolved", "candidate_ids": [i["id"] for i in items]})
    return conflicts


def extract_snapshot(paths, output, *, fixture_root=None, synthetic_only=True, case_id="SYNTH-001", ocr=ocr_page):
    if not synthetic_only:
        raise ValueError("real-case intake is disabled")
    if not isinstance(case_id, str) or not SYNTHETIC_CASE.fullmatch(case_id):
        raise ValueError("only synthetic case IDs are accepted")
    paths = list(paths)
    if not paths:
        raise ValueError("at least one synthetic source is required")
    documents = []
    for path in paths:
        suffix = Path(path).suffix.lower()
        if suffix == ".pdf":
            document = extract_pdf(path, fixture_root=fixture_root, synthetic_only=True, ocr=ocr)
        elif suffix == ".docx":
            from intake.adapters import extract_docx
            document = extract_docx(path, fixture_root=fixture_root)
        elif suffix == ".xlsx":
            from intake.adapters import extract_xlsx
            document = extract_xlsx(path, fixture_root=fixture_root)
        elif suffix in (".csv", ".tsv"):
            from intake.adapters import extract_csv
            document = extract_csv(path, fixture_root=fixture_root)
        else:
            raise ValueError("unsupported source format: " + suffix)
        documents.append(document)
    if len({doc["source_id"] for doc in documents}) != len(documents):
        raise ValueError("duplicate source content; disambiguate before extraction")
    snapshot = {
        "schema_version": 1, "case_id": case_id, "status": "unverified_synthetic_extraction",
        "documents": documents, "conflicts": reconcile(documents),
        "requires_manual_review": True,
    }
    output = Path(output)
    # Output may be a new file; resolve the existing parent separately.
    check_fixture(output.parent, fixture_root)
    output = output.parent.resolve() / output.name
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, prefix=".intake-", delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(snapshot, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return snapshot
