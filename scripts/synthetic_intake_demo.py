"""Generate fictional Spanish PDF pages and run source-traceable extraction.

Only generated synthetic documents are accepted. Never reads private case data.
"""

from pathlib import Path
import tempfile

import pymupdf

from intake.extract import extract_snapshot
from intake.markdown import write_bundle


def create_page(path, value, scan):
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((55, 80), "Fecha toma: 2025-12-04", fontsize=17)
    page.insert_text((55, 115), f"Creatinina {value} mg/dL (0,6 - 1,1)", fontsize=17)
    if scan:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        document.close()
        document = pymupdf.open()
        page = document.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=pix.tobytes("png"))
    document.save(path)
    document.close()


def main():
    root = Path(tempfile.mkdtemp(prefix="synthetic-intake-"))
    root.chmod(0o700)
    (root / ".synthetic-fixture").write_text("SYNTHETIC_INTAKE_FIXTURE_V1", encoding="utf-8")
    native = root / "fictional-text.pdf"
    scanned = root / "fictional-scan.pdf"
    create_page(native, "1,4", scan=False)
    create_page(scanned, "1,8", scan=True)
    snapshot = extract_snapshot([native, scanned], root / "extraction.json", fixture_root=root)
    markdown = write_bundle(snapshot, root, fixture_root=root)
    print("Synthetic fixture:", root)
    print("Documents:", len(snapshot["documents"]))
    print("Unverified candidates:", sum(len(doc["candidates"]) for doc in snapshot["documents"]))
    print("Unresolved conflicts:", len(snapshot["conflicts"]))
    print("Manual review required:", snapshot["requires_manual_review"])
    print("Snapshot:", root / "extraction.json")
    print("Markdown files:", len(markdown), "(one per original and an index)")


if __name__ == "__main__":
    main()
