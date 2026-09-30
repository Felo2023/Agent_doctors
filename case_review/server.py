"""Password-protected localhost review UI for marked SYNTH-* fixtures only.

Start with REVIEW_APP_PASSWORD and --fixture-root pointing at a marked fixture.
Never expose this prototype on a network or use it for clinical records.
"""

import argparse
import hmac
import html
import json
import os
from pathlib import Path
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import base64

from case_review.store import ConflictError, ReviewStore
from intake.corrections import propose, review
from intake.markdown import anchor


def safe(value):
    return html.escape(str(value), quote=True)


CSS = """
:root{--ink:#17344b;--muted:#617685;--line:#d2dee6;--sea:#087983;--water:#e4f3f3;--sand:#f7f9fb;--amber:#a35c1e;--warn:#fff3de}
*{box-sizing:border-box}body{margin:0;background:var(--sand);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
header{background:#eaf4f5;border-bottom:1px solid #b3ced1;padding:24px max(24px,calc((100vw - 1160px)/2))}
header small{font:12px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--sea);letter-spacing:.08em;text-transform:uppercase}
h1{font:normal 42px/1.06 Georgia,serif;letter-spacing:-.035em;margin:7px 0 5px}h2{font:normal 26px/1.1 Georgia,serif;margin:0 0 12px}h3{font:600 17px/1.3 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0 0 8px}
.intro{max-width:790px;color:#355264;margin:7px 0}.shell{max-width:1160px;margin:34px auto;padding:0 24px;display:grid;grid-template-columns:minmax(0,1.7fr) minmax(260px,.8fr);gap:35px}
.stack>*+*{margin-top:24px}.card{background:white;border:1px solid var(--line);border-radius:5px;padding:24px;box-shadow:0 4px 20px #113b4b09}.rail{border-left:3px solid var(--sea);padding-left:18px}
.eyebrow{font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.08em;color:var(--sea)}
.status{display:inline-block;padding:4px 9px;border-radius:2px;background:var(--water);color:#075960;font-size:12px;font-weight:700}.status.unreviewed,.status.defer{background:var(--warn);color:#77470e}.status.exclude{background:#eef0f2;color:#4b5b66}
.source{border-left:2px solid #9abec2;padding-left:15px;margin:18px 0}blockquote{margin:9px 0;font:18px/1.45 Georgia,serif;overflow-wrap:anywhere}
code{font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere;color:#476475}a{color:#006d76;text-underline-offset:3px}a:hover{color:#003f4a}
form{display:grid;gap:10px;margin-top:18px}label{font-weight:650;font-size:13px}input,textarea,select{width:100%;border:1px solid #afc3d0;border-radius:3px;padding:10px 11px;background:#fff;color:#17344b;font:14px/1.4 inherit}textarea{min-height:74px;resize:vertical}
button{appearance:none;cursor:pointer;border:1px solid var(--sea);background:var(--sea);color:white;font-weight:700;font-size:14px;padding:11px 15px;border-radius:3px;text-align:left}button:hover{background:#075d66}button:disabled{background:#bacacd;border-color:#bacacd;cursor:not-allowed}
:focus-visible{outline:3px solid #d48638;outline-offset:3px}.muted{color:var(--muted);font-size:13px}.topline{display:flex;gap:12px;align-items:center;justify-content:space-between}.notice{background:var(--warn);color:#653d19;padding:10px 13px;border-left:3px solid var(--amber);font-size:13px;margin-top:13px}
.step{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--sea);margin:0 0 6px}.foot{max-width:1160px;margin:22px auto 55px;padding:0 24px;color:var(--muted);font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;border:1px solid var(--line);padding:24px;font-size:13px}
@media(max-width:850px){.shell{display:block}.shell>aside{margin-top:24px}h1{font-size:34px}.card{padding:18px}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Avoid writing document contents or credentials to server logs.

    def send(self, status, content, content_type="text/html; charset=utf-8", *, headers=None):
        data = content.encode("utf-8") if isinstance(content, str) else content
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        expected = base64.b64encode(("reviewer:" + self.server.password).encode()).decode()
        return hmac.compare_digest(self.headers.get("Authorization", ""), "Basic " + expected)

    def guard(self, is_post=False):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.send(400, "Invalid host", "text/plain; charset=utf-8")
            return False
        if not self.authorized():
            self.send(401, "Authentication required", "text/plain; charset=utf-8",
                      headers={"WWW-Authenticate": 'Basic realm="Synthetic review"'})
            return False
        if is_post:
            origin = self.headers.get("Origin")
            fetch_site = self.headers.get("Sec-Fetch-Site")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if origin not in (expected, None, "null") or (origin in (None, "null") and fetch_site != "same-origin"):
                self.send(403, "Same-origin submission required", "text/plain; charset=utf-8")
                return False
        return True

    def document(self, title, content, *, status=200):
        page = f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{safe(title)}</title><style>{CSS}</style></head><body>{content}</body></html>"
        self.send(status, page)

    def hidden(self, revision):
        return f'<input type="hidden" name="csrf" value="{safe(self.server.csrf)}"><input type="hidden" name="revision" value="{revision}">'

    def do_GET(self):
        if not self.guard():
            return
        path = urlparse(self.path).path
        try:
            if path == "/":
                self.server.store.verify_snapshot()
                return self.document("Evidence review · " + self.server.store.case_id, self.dashboard())
            if path == "/case":
                return self.send(200, json.dumps(self.server.store.version(), ensure_ascii=False, indent=2), "application/json; charset=utf-8")
            if path.startswith("/case/version/"):
                number = path.rsplit("/", 1)[-1]
                if not number.isdigit():
                    return self.send(404, "Unknown version", "text/plain; charset=utf-8")
                return self.send(200, json.dumps(self.server.store.version(int(number)), ensure_ascii=False, indent=2), "application/json; charset=utf-8")
            if path == "/corrections":
                file = self.server.store.root / "corrections.md"
                text = file.read_text(encoding="utf-8") if file.is_file() and not file.is_symlink() else "No correction proposals yet."
                return self.document("Corrections", f"<header><small>Separate correction record</small><h1>Corrections</h1><a href='/'>← Back to review</a></header><main class='shell' style='display:block'><pre>{safe(text)}</pre></main>")
            if path == "/abstractions":
                return self.document("Independent abstractions", self.abstraction_page())
            if path.startswith("/source/") or path.startswith("/original/"):
                source_id = path.rsplit("/", 1)[-1]
                doc = self.server.store.documents.get(source_id)
                if not doc:
                    return self.send(404, "Unknown source", "text/plain; charset=utf-8")
                self.server.store.verify_source(doc)
                if path.startswith("/source/"):
                    file = self.server.store.root / (source_id + ".md")
                    text = file.read_text(encoding="utf-8") if file.is_file() and not file.is_symlink() else "Source Markdown not generated."
                    return self.document(doc["filename"], f"<header><small>Original text projection · {safe(source_id)}</small><h1>{safe(doc['filename'])}</h1><a href='/'>← Back to review</a> · <a href='/original/{safe(source_id)}'>Open original</a></header><main class='shell' style='display:block'><pre>{safe(text)}</pre></main>")
                file = self.server.store.root / doc["filename"]
                data = file.read_bytes()
                kind = "application/pdf" if doc["format"] == "pdf" else "application/octet-stream"
                disposition = "inline" if doc["format"] == "pdf" else "attachment"
                return self.send(200, data, kind,
                                 headers={"Content-Disposition": f"{disposition}; filename=source.{doc['format']}"})
            return self.send(404, "Not found", "text/plain; charset=utf-8")
        except (ValueError, ConflictError) as exc:
            return self.document("Review blocked", f"<header><h1>Review blocked</h1></header><main class='shell' style='display:block'><p>{safe(exc)}</p><a href='/'>Reload review</a></main>", status=409)

    def do_POST(self):
        if not self.guard(is_post=True):
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/x-www-form-urlencoded":
            return self.send(415, "Form data required", "text/plain; charset=utf-8")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                return self.send(413, "Invalid form size", "text/plain; charset=utf-8")
            form = parse_qs(self.rfile.read(length).decode("utf-8", errors="strict"),
                            strict_parsing=True, keep_blank_values=True)

            def field(name):
                values = form.get(name)
                if not values or len(values) != 1:
                    raise ValueError("missing or repeated form field: " + name)
                return values[0]

            if not hmac.compare_digest(field("csrf"), self.server.csrf):
                return self.send(403, "Invalid form token", "text/plain; charset=utf-8")
            revision = int(field("revision"))
            store = self.server.store
            if self.path == "/decision":
                store.decide(field("candidate_id"), field("action"), field("reviewer"), field("note"), revision,
                             correction_id=field("correction_id").strip() or None)
            elif self.path == "/fact/propose":
                store.propose_fact(field("source_id"), field("block_id"), field("category"),
                                   field("statement"), field("event_date"), field("date_precision"),
                                   field("attribution"), field("proposer"), revision)
            elif self.path == "/fact/review":
                store.decide_fact(field("fact_id"), field("action"), field("reviewer"), field("note"), revision)
            elif self.path == "/coverage":
                store.decide_coverage(field("item_id"), field("action"), field("reviewer"), field("note"), revision)
            elif self.path == "/commit":
                store.commit(revision)
            elif self.path in ("/correction/propose", "/correction/review"):
                with store.lock:
                    if revision != store.current_revision():
                        raise ConflictError("review changed; reload before submitting")
                    store.verify_snapshot()
                    ledger = store.root / "corrections.jsonl"
                    if self.path.endswith("/propose"):
                        cid = field("candidate_id")
                        if cid not in store.candidates:
                            raise ValueError("unknown candidate")
                        candidate, doc = store.candidates[cid]
                        event = propose(store.snapshot, ledger, fixture_root=store.root,
                                        source_id=doc["source_id"], block_id=candidate["source"]["block_id"],
                                        suggested_text=field("suggested_text"), reason=field("reason"), author=field("reviewer"))
                    else:
                        event = review(store.snapshot, ledger, fixture_root=store.root,
                                       proposal_id=field("proposal_id"), decision=field("decision"),
                                       reviewer=field("reviewer"), reason=field("reason"))
                    store.record_correction(event, revision)
            else:
                return self.send(404, "Unknown action", "text/plain; charset=utf-8")
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except (ValueError, ConflictError, UnicodeError) as exc:
            code = 409 if isinstance(exc, ConflictError) else 400
            self.document("Action not saved", f"<header><h1>Action not saved</h1></header><main class='shell' style='display:block'><p>{safe(exc)}</p><a href='/'>Reload review</a></main>", status=code)

    def dashboard(self):
        store = self.server.store
        state = store.state()
        revision = state["revision"]
        cards = []
        for item in state["candidates"]:
            candidate = item["candidate"]
            source = candidate["source"]
            source_id = source["document_id"]
            decision = item["decision"]
            badge = decision["action"] if decision else "unreviewed"
            corrected = (f" · corrected to <strong>{safe(decision['effective_value'])}</strong>"
                         if decision and decision["correction_id"] else "")
            cards.append(f"""
            <article class='card'>
              <div class='topline'><span class='eyebrow'>Lab candidate · {safe(candidate['id'])}</span><span class='status {safe(badge)}'>{safe(badge)}</span></div>
              <h2>{safe(candidate['analyte_original'])} <span style='color:var(--sea)'>{safe(candidate['value_original'])} {safe(candidate['unit_original'])}</span></h2>
              <div class='source'><span class='eyebrow'>Evidence rail / original wording</span><blockquote>{safe(source['exact_text'])}</blockquote>
              <div class='muted'>{safe(item['source_filename'])} · page {safe(source['page'])} · {safe(source['channel'])} · line {safe(source['line'])}</div>
              <a href='/original/{safe(source_id)}#page={safe(source['page'])}' target='_blank' rel='noopener'>Inspect original</a> · <a href='/source/{safe(source_id)}' target='_blank' rel='noopener'>Read extraction</a></div>
              <p class='muted'>Date: {safe(candidate['collection_date'] or 'unknown')} · Reference: {safe(candidate['reference_range_original'] or 'not provided')}{corrected}</p>
              <form method='post' action='/decision'>{self.hidden(revision)}
                <input type='hidden' name='candidate_id' value='{safe(candidate['id'])}'>
                <label>Decision<select name='action' required><option value='verify'>Verify against source</option><option value='exclude'>Exclude from case facts</option><option value='defer'>Defer / request clarification</option></select></label>
                <label>Accepted correction ID (only when verifying a corrected line)<input name='correction_id' placeholder='COR-… — optional'></label>
                <label>Reviewer<input name='reviewer' required maxlength='80' placeholder='Your reviewer identifier'></label>
                <label>What did you check?<textarea name='note' required minlength='4' maxlength='1000' placeholder='Describe what the original page shows and why this decision is justified.'></textarea></label>
                <button>Save candidate review</button>
              </form>
              <details style='margin-top:17px'><summary>Propose a transcription correction</summary>
                <form method='post' action='/correction/propose'>{self.hidden(revision)}
                  <input type='hidden' name='candidate_id' value='{safe(candidate['id'])}'>
                  <label>Corrected original line<input name='suggested_text' required maxlength='1000' placeholder='For example: Glucosa 92 mg/dL'></label>
                  <label>Why?<textarea name='reason' required maxlength='1000'></textarea></label>
                  <label>Reviewer<input name='reviewer' required maxlength='80'></label>
                  <button>Record proposed correction</button></form>
              </details>
            </article>""")
        if not cards:
            cards.append("<div class='card'><h2>No lab candidates found</h2><p>The current parser only recognizes three fictional Spanish lab labels in PDFs. You can propose attributed statements from other source blocks below; nothing is inferred automatically.</p></div>")
        fact_cards = []
        for fact in state["facts"]:
            original = next(b["text"] for b in store.documents[fact["source_id"]]["blocks"] if b["id"] == fact["block_id"])
            fact_cards.append(f"""<article class='card'><div class='topline'><span class='eyebrow'>{safe(fact['category'])} · {safe(fact['id'])}</span><span class='status {safe(fact['action'])}'>{safe(fact['action'])}</span></div>
              <h3>{safe(fact['statement'])}</h3><div class='source'><span class='eyebrow'>Original · {safe(fact['block_id'])}</span><blockquote>{safe(original)}</blockquote>
              <a href='/original/{safe(fact['source_id'])}' target='_blank' rel='noopener'>Inspect original</a> · <a href='/source/{safe(fact['source_id'])}'>Source extraction</a></div>
              <p class='muted'>Attribution: {safe(fact['attribution'])} · Date: {safe(fact['event_date'] or 'unknown')} ({safe(fact['date_precision'])}) · Proposed by {safe(fact['proposer'])}</p>
              {self.fact_review_form(fact, revision) if fact['action'] == 'pending' else '<p class="muted">Reviewed by ' + safe(fact['reviewer']) + ': ' + safe(fact['review_note']) + '</p>'}</article>""")
        source_forms = []
        coverage_by_id = {item["id"]: item for item in state["coverage"]["items"]}
        for doc in store.documents.values():
            blocks = [block for block in doc["blocks"] if block["type"] != "image"
                      and not (block["type"] == "cell" and block.get("formula") and block.get("cached_value") is None)]
            excerpt = "".join(f"<article class='source' style='padding:8px 0 8px 14px'><div class='eyebrow'>{safe(block['id'])}</div><div>{safe(block['text'][:280])}{'…' if len(block['text']) > 280 else ''}</div>{self.fact_proposal_form(doc, block, revision)}{self.coverage_form(coverage_by_id[block['id']], revision)}</article>" for block in blocks)
            remaining = [item for item in state["coverage"]["items"] if item["source_id"] == doc["source_id"] and (item["type"] == "warning" or item["block_type"] == "image" or (item["type"] == "block" and item["id"] not in {b["id"] for b in blocks}))]
            warnings = "".join(f"<article class='source'><strong>{safe(item['type'])} · {safe(item['state'])}</strong><p>{safe(item['text'])}</p>{self.coverage_form(item, revision)}</article>" for item in remaining)
            source_forms.append(f"<details class='card'><summary><strong>{safe(doc['filename'])}</strong> · {len(blocks)} text/cell blocks</summary><p class='muted'>Propose a statement or account for a source block. Warnings and unreadable content must stay visible; they cannot be turned into normal findings.</p>{excerpt}{warnings}</details>")
        conflicts = "".join(f"<li>{safe(c['analyte'])} · {safe(c['date'] or 'date unknown')} — {len(c['candidate_ids'])} differing candidate values</li>" for c in state["conflicts"])
        versions = "".join(f"<li><a href='/case/version/{i}'>Version {i}</a></li>" for i in range(1, state["case_version"] + 1)) or "<li>No committed version yet</li>"
        corrections = ""
        ledger = store.root / "corrections.jsonl"
        if ledger.is_file():
            events = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
            proposals = [e for e in events if e.get("action") == "propose"]
            resolutions = {e["proposal_id"]: e["decision"] for e in events if e.get("action") == "review"}
            for event in proposals:
                corrections += f"<li><code>{safe(event['event_id'])}</code> · {safe(resolutions.get(event['event_id'], 'pending'))}</li>"
        correction_form = f"<form method='post' action='/correction/review'>{self.hidden(revision)}<label>Proposal ID<input name='proposal_id' required placeholder='COR-…'></label><label>Decision<select name='decision'><option value='accepted'>Accept transcription</option><option value='rejected'>Reject transcription</option></select></label><label>Reviewer<input name='reviewer' maxlength='80' required></label><label>Why?<textarea name='reason' required maxlength='1000'></textarea></label><button>Save correction review</button></form>"
        comparison_link = ("<p><a href='/abstractions'>Compare two independent AI abstractions →</a></p>"
                           if (store.root / "dual_abstraction.json").is_file() else "")
        return f"""
        <header><small>Evidence review / {safe(store.case_id)} · synthetic materials only</small><h1>Make every fact traceable.</h1>
          <p class='intro'>This screen does not diagnose. Compare every candidate with the original file, resolve transcription issues separately, and then create a reviewed case version.</p></header>
        <div class='shell'><main class='stack'><div class='topline'><h2>Candidate observations</h2><span class='eyebrow'>Revision {revision}</span></div>
          {''.join(cards)}<div><h2>Proposed source statements</h2><p class='muted'>Histories, medications and report wording can be proposed from PDF, Word and spreadsheet blocks. Attribution and source location remain visible; no model infers findings from text here.</p></div>
          {''.join(fact_cards) if fact_cards else '<p class="muted">No source statements proposed yet.</p>'}
          <div><h2>Browse extracted blocks</h2>{comparison_link}</div>{''.join(source_forms)}</main>
          <aside class='stack'>
            <section class='card rail'><div class='step'>01 / Review</div><h3>{len(state['candidates']) - state['unreviewed']} of {len(state['candidates'])} labs assessed</h3><p class='muted'>{len(state['facts']) - state['pending_facts']} of {len(state['facts'])} source statements reviewed</p>
              <p class='muted'>“Verified” means a named reviewer checked a source. It does not establish a diagnosis.</p></section>
            <section class='card rail'><div class='step'>02 / Reconcile</div><h3>Different values</h3><ul>{conflicts or '<li>None detected by the limited parser</li>'}</ul>
              <p class='muted'>Different measurements on the same day may both be real. Defer them until specimen times and context are known; never exclude a real result just to make this check pass.</p></section>
            <section class='card rail'><div class='step'>03 / Commit</div><h3>Case version {state['case_version']}</h3>
              <ul>{versions}</ul><p class='muted'>Committed versions remain a reviewed subset, never ready for diagnosis.</p>
              <form method='post' action='/commit'>{self.hidden(revision)}<button {'disabled' if not state['can_commit'] else ''}>Create reviewed case version</button></form></section>
            <section class='card rail'><div class='step'>04 / Source coverage</div><h3>{state['coverage']['accounted_for']} of {state['coverage']['total']} blocks/warnings accounted for</h3>
              <p class='muted'>Outstanding: {state['coverage']['outstanding']}. This checks extracted blocks only, not whether the records are complete.</p>
              {'<p class="notice">Unresolved extraction limits or unreviewed blocks remain. No diagnostic run is enabled.</p>' if not state['coverage']['complete'] else '<p class="muted">Every extracted block is accounted for; clinical completeness is still unverified.</p>'}</section>
            <section class='card'><h3>Transcription corrections</h3><p class='muted'>Proposals do not change the source. A second review accepts or rejects them; candidate verification is still separate.</p>
              <ul>{corrections or '<li>None proposed</li>'}</ul><a href='/corrections'>Read correction log</a>{correction_form}</section>
        </aside></div><div class='foot'>{safe(store.case_id)} only · unverified source extraction · localhost prototype · no patient data</div>"""

    def abstraction_page(self):
        store = self.server.store
        store.verify_snapshot()
        file = store.root / "dual_abstraction.json"
        if file.is_symlink() or not file.is_file():
            raise ValueError("no independent abstraction comparison is available")
        result = json.loads(file.read_text(encoding="utf-8"))
        if (result.get("case_id") != store.case_id or result.get("status") != "unverified_independent_abstractions"
                or result.get("source_snapshot_sha256") != store.snapshot_hash
                or result.get("promoted_to_case") is not False):
            raise ValueError("independent abstraction metadata does not match this case")
        lookup = {block["id"]: doc for doc in store.documents.values() for block in doc["blocks"]}
        cards = []
        for group in result["comparisons"]:
            block_id = group["block_id"]
            if block_id not in lookup or group["status"] not in ("same_wording", "different_or_multiple", "one_sided", "omitted_by_both"):
                raise ValueError("abstractor cited a missing source block")
            doc = lookup[block_id]
            block = next(item for item in doc["blocks"] if item["id"] == block_id)
            views = []
            for role in ("abstractor-a", "abstractor-b"):
                entries = group["proposals"].get(role, [])
                listing = "".join(f"<blockquote>{safe(item['statement'])}</blockquote><p class='muted'>Date: {safe(item['event_date'] or 'unknown')} ({safe(item['date_precision'])}) · {safe(item['attribution'])}</p>" for item in entries)
                missing = "<p class='muted'>No proposal for this block.</p>"
                views.append(f"<section class='card' style='box-shadow:none'><span class='eyebrow'>{safe(role)}</span>{listing or missing}</section>")
            cards.append(f"<article class='card'><span class='eyebrow'>{safe(group['status'])} · {safe(group['category'])}</span><h2>{safe(doc['filename'])}</h2><code>{safe(block_id)}</code><div class='source'><strong>Original</strong><blockquote>{safe(block['text'])}</blockquote><a href='/original/{safe(doc['source_id'])}'>Inspect original</a></div><div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px'>{''.join(views)}</div><p class='notice'>Agreement or shared omission is not evidence. Compare the original, then make a separate human proposal and review on the main screen.</p></article>")
        empty = "<div class='card'>Neither model proposed a statement. Inspect originals for omissions.</div>"
        return f"<header><small>Two independent passes · {safe(store.case_id)}</small><h1>Agreement is not evidence.</h1><p class='intro'>Two models read the same fictional source blocks independently. Differences are visible; nothing here updates reviewed facts.</p><a href='/'>← Back to source review</a></header><main class='shell' style='display:block'><div class='stack'>{''.join(cards) or empty}</div></main>"

    def fact_proposal_form(self, doc, block, revision):
        categories = ("symptom", "history", "medication", "prior_diagnosis", "imaging_report", "pathology_report", "family_history", "test_report", "exposure")
        choices = "".join(f"<option value='{value}'>{value.replace('_', ' ')}</option>" for value in categories)
        return f"""<details><summary>Propose a source-grounded statement</summary>
        <form method='post' action='/fact/propose'>{self.hidden(revision)}
          <input type='hidden' name='source_id' value='{safe(doc['source_id'])}'><input type='hidden' name='block_id' value='{safe(block['id'])}'>
          <label>Kind<select name='category'>{choices}</select></label>
          <label>Statement (clearly attributed; do not invent findings)<textarea name='statement' maxlength='1200' required></textarea></label>
          <label>Where did this statement come from?<select name='attribution'><option value='patient_reported'>Patient/family report</option><option value='clinician_documented'>Clinician-documented statement</option><option value='source_text'>Source text only (not confirmed)</option></select></label>
          <label>Event date (YYYY, YYYY-MM or YYYY-MM-DD; blank if unknown)<input name='event_date' placeholder='2024, 2024-02 or 2024-02-15'></label>
          <label>Date certainty<select name='date_precision'><option value='unknown'>Unknown</option><option value='exact'>Exact</option><option value='approximate'>Approximate</option></select></label>
          <label>Proposed by<input name='proposer' maxlength='80' required></label><button>Propose statement for review</button>
        </form></details>"""

    def fact_review_form(self, fact, revision):
        return f"""<form method='post' action='/fact/review'>{self.hidden(revision)}
          <input type='hidden' name='fact_id' value='{safe(fact['id'])}'>
          <label>Decision<select name='action'><option value='verify'>Verify source statement</option><option value='exclude'>Exclude</option><option value='defer'>Defer</option></select></label>
          <label>Independent reviewer<input name='reviewer' maxlength='80' required></label>
          <label>Why?<textarea name='note' maxlength='1000' required></textarea></label><button>Save statement review</button>
        </form>"""

    def coverage_form(self, item, revision):
        if item["state"] == "linked_to_reviewed_observation":
            return "<p class='muted'>Linked to an explicitly reviewed observation.</p>"
        if item["blocking"]:
            selection = "<option value='defer'>Defer: source needs re-extraction or inspection</option>"
        elif item["type"] == "warning":
            selection = "<option value='acknowledged'>Acknowledged after inspecting original</option><option value='defer'>Defer</option>"
        else:
            selection = "<option value='reviewed_context'>Reviewed context; not promoted as a fact</option><option value='defer'>Defer</option>"
        return f"""<details><summary>Coverage: {safe(item['state'])}</summary>
          <form method='post' action='/coverage'>{self.hidden(revision)}
            <input type='hidden' name='item_id' value='{safe(item['id'])}'>
            <label>Disposition<select name='action'>{selection}</select></label>
            <label>Reviewer<input name='reviewer' maxlength='80' required></label>
            <label>How was the original checked? Why is no case fact proposed?<textarea name='note' maxlength='1000' required></textarea></label>
            <button>Record coverage decision</button>
          </form></details>"""


def serve(root, password, port=8765):
    if not password or len(password) < 20:
        raise ValueError("REVIEW_APP_PASSWORD must contain at least 20 characters")
    store = ReviewStore(Path(root))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.store = store
    server.password = password
    server.csrf = secrets.token_urlsafe(32)
    try:
        print(f"Synthetic evidence review: http://127.0.0.1:{server.server_port}/ (username: reviewer)", flush=True)
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.fixture_root, os.getenv("REVIEW_APP_PASSWORD"), args.port)


if __name__ == "__main__":
    main()
