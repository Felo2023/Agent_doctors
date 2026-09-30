"""Optional Playwright smoke test for the synthetic source-review UI.

Creates only fictional PDFs and screenshots inside a private temp directory.
Requires Playwright + its Chromium browser in the active Python environment.
"""

from pathlib import Path
import secrets
import tempfile
import threading
from http.server import ThreadingHTTPServer

import pymupdf
from playwright.sync_api import sync_playwright

from case_review.server import Handler
from case_review.store import ReviewStore
from intake.extract import extract_snapshot
from intake.markdown import write_bundle
from intake.dual_abstract import save as save_abstractions


def main():
    root = Path(tempfile.mkdtemp(prefix="synthetic-review-browser-"))
    root.chmod(0o700)
    print("Synthetic browser fixture:", root, flush=True)
    (root / ".synthetic-fixture").write_text("SYNTHETIC_INTAKE_FIXTURE_V1")
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((55, 75), "Fecha: 2025-12-04", fontsize=15)
    page.insert_text((55, 105), "Glucosa 92 mg/dL", fontsize=15)
    source = root / "fictional-laboratory.pdf"
    pdf.save(source)
    pdf.close()
    snapshot = extract_snapshot([source], root / "extraction.json", fixture_root=root)
    write_bundle(snapshot, root, fixture_root=root)
    block_id = snapshot["documents"][0]["candidates"][0]["source"]["block_id"]
    claim = {"block_id": block_id, "category": "test_report", "statement": "Fictional source shows glucose 92 mg/dL",
             "event_date": None, "date_precision": "unknown", "attribution": "source_text"}
    save_abstractions(root, snapshot, [claim], [{**claim, "statement": "The fictional source lists glucose 92 mg/dL"}])
    store = ReviewStore(root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.store = store
    server.password = secrets.token_urlsafe(32)
    server.csrf = secrets.token_urlsafe(32)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        address = f"http://127.0.0.1:{server.server_port}/"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(http_credentials={"username": "reviewer", "password": server.password},
                                          viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.goto(address)
            page.wait_for_load_state("networkidle")
            assert "Make every fact traceable" in page.locator("h1").inner_text()
            desktop = root / "dashboard-desktop.png"
            page.screenshot(path=str(desktop), full_page=True)
            page.get_by_role("link", name="Compare two independent AI abstractions").click()
            page.wait_for_load_state("networkidle")
            assert "Agreement is not evidence" in page.locator("h1").inner_text()
            page.screenshot(path=str(root / "independent-comparison.png"), full_page=True)
            page.get_by_role("link", name="Back to source review").click()
            page.wait_for_load_state("networkidle")
            form = page.locator("form[action='/decision']").first
            form.locator("select[name='action']").select_option("verify")
            form.locator("input[name='reviewer']").fill("fictional-reviewer")
            form.locator("textarea[name='note']").fill("Checked the fictional PDF on page 1")
            form.get_by_role("button", name="Save candidate review").click()
            page.wait_for_load_state("networkidle")
            if not page.locator("aside").count():
                page.screenshot(path=str(root / "after-decision.png"), full_page=True)
                raise AssertionError("review did not return to dashboard: " + page.locator("body").inner_text()[:600])
            assert "1 of 1 labs assessed" in page.locator("aside").inner_text()
            page.get_by_role("button", name="Create reviewed case version").click()
            page.wait_for_load_state("networkidle")
            assert "Case version 1" in page.locator("aside").inner_text()
            page.set_viewport_size({"width": 390, "height": 844})
            mobile = root / "dashboard-mobile.png"
            page.screenshot(path=str(mobile), full_page=True)
            context.close()
            browser.close()
        print("Browser review passed; screenshots:", desktop, mobile)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        store.close()


if __name__ == "__main__":
    main()
