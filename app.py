"""
app.py - FastAPI server for the ZUGFeRD / Factur-X invoicing tool.

An invoice (PDF/DOCX/DOC) comes in via the Windows "Open" dialog, by dragging it
onto the .exe, or by drag & drop into the browser. Its fields are read out, can be
corrected in the UI, and a hybrid ZUGFeRD PDF is produced and saved as
<source name>_Zug.pdf (see output.py / ZUGFeRD-Studio.ini).

Working files live in WORK_DIR/storage/<session-uuid>/ (user temp folder) and are
purged after SESSION_TTL_HOURS; only the finished PDF is written anywhere else.
"""

import json
import re
import secrets
import shutil
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from extractor import parse_uploaded_file
from invoice_logic import (
    compute_totals, has_errors, split_issues, totals_to_json, validate_invoice,
)
from output import ensure_config, output_name, pick_file_dialog, save_output
from paths import RESOURCE_DIR, WORK_DIR
from pdf_renderer import ConversionError, convert_docx_to_pdf, create_invoice_pdf_from_data
from schematron import check_business_rules
from zugferd_generator import build_zugferd_xml, validate_and_create_zugferd_pdf

STORAGE_DIR = WORK_DIR / "storage"
STATIC_DIR = RESOURCE_DIR / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SESSION_TTL_HOURS = 24
PROFILES = {"en16931", "basic"}
SUPPORTED = (".pdf", ".docx", ".doc")

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
ensure_config()

# Files handed over by the launcher (invoice dragged onto the .exe). The token
# protects /api/queue-file: only a launcher that can read the instance file may use it.
INSTANCE_TOKEN = secrets.token_urlsafe(24)
_pending: deque = deque()
_pending_lock = threading.Lock()

app = FastAPI(
    title="ZUGFeRD Invoicing Tool",
    description="Converts PDF and DOCX invoices into ZUGFeRD / Factur-X electronic invoices.",
    version="2.1.0",
)


@app.middleware("http")
async def reject_foreign_origins(request: Request, call_next):
    """Local-only tool: a state-changing request from another website is refused."""
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and not re.fullmatch(r"https?://(127\.0\.0\.1|localhost)(:\d+)?", origin):
            return JSONResponse(status_code=403, content={"detail": "Fremder Ursprung nicht erlaubt."})
    return await call_next(request)


class GenerateRequest(BaseModel):
    session_id: str
    profile: str = "en16931"
    invoice_data: Dict[str, Any]


class CalculateRequest(BaseModel):
    invoice_data: Dict[str, Any]


class LocalFileRequest(BaseModel):
    profile: str = "en16931"


class QueueRequest(BaseModel):
    path: str


# ------------------------------------------------------------------ session helpers

def _purge_old_sessions() -> None:
    cutoff = time.time() - SESSION_TTL_HOURS * 3600
    for folder in STORAGE_DIR.iterdir():
        try:
            if folder.is_dir() and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


_purge_old_sessions()


def _session_dir(session_id: str, must_exist: bool = True) -> Path:
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Sitzung nicht gefunden.")
    folder = STORAGE_DIR / session_id
    if must_exist and not folder.is_dir():
        raise HTTPException(status_code=404, detail="Sitzung nicht gefunden.")
    return folder


def _read_meta(folder: Path) -> Dict[str, Any]:
    try:
        return json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_meta(folder: Path, meta: Dict[str, Any]) -> None:
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def _safe_filename(name: str, default: str = "rechnung.pdf") -> str:
    """Strip any path components / odd characters from a client-supplied filename."""
    base = Path((name or "").replace("\\", "/")).name
    base = re.sub(r"[^\w.\- ()äöüÄÖÜß]", "_", base).strip(" .")
    return base or default


def _profile(value: str) -> str:
    return value.lower() if (value or "").lower() in PROFILES else "en16931"


# ------------------------------------------------------------------ core pipeline

def _build_response(session_id: str, folder: Path, *, status: str, profile: str, data: Dict[str, Any],
                    xml: str = "", notes: Optional[list] = None, existing: bool = False,
                    business_rules: Optional[dict] = None, output: Optional[dict] = None) -> Dict[str, Any]:
    errors, warnings = split_issues(validate_invoice(data))
    has_pdf = (folder / "zugferd_invoice.pdf").exists()
    meta = _read_meta(folder)
    return {
        "session_id": session_id,
        "filename": meta.get("filename", ""),
        "source_path": meta.get("source_path"),
        "status": status,
        "profile": profile,
        "is_existing_zugferd": existing,
        "invoice_data": data,
        "xml_content": xml,
        "totals": totals_to_json(compute_totals(data)),
        "issues": {"errors": errors, "warnings": warnings},
        "extraction_notes": notes or [],
        "ai_review_attempted": bool(meta.get("ai_review_attempted", False)),
        "ai_review_used": bool(meta.get("ai_review_used", False)),
        "ai_model": meta.get("ai_model"),
        "business_rules": business_rules or {"status": "unavailable", "failures": [], "detail": ""},
        "output": output,
        "pdf_download_url": f"/api/download/pdf/{session_id}" if has_pdf else None,
        "xml_download_url": f"/api/download/xml/{session_id}" if (folder / "factur-x.xml").exists() else None,
        "preview_url": f"/api/preview/{session_id}",
    }


def _generate(folder: Path, base_pdf: bytes, data: Dict[str, Any], profile: str):
    """Validate, build XML, embed it, save <source>_Zug.pdf. Returns (xml, rules, output)."""
    xml = build_zugferd_xml(data, profile=profile)
    pdf = validate_and_create_zugferd_pdf(base_pdf, xml, profile=profile, data=data)
    (folder / "zugferd_invoice.pdf").write_bytes(pdf)
    (folder / "factur-x.xml").write_text(xml, encoding="utf-8")
    rules = check_business_rules(xml, profile)
    meta = _read_meta(folder)
    output = None
    if not meta.get("sample"):
        output = save_output(pdf, meta.get("filename") or "rechnung.pdf", meta.get("source_path"))
    return xml, rules, output


def _base_pdf(folder: Path) -> bytes:
    path = folder / "base_document.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Ausgangsdokument nicht gefunden.")
    return path.read_bytes()


def _status_for(rules: dict) -> str:
    return "business_rules_failed" if rules["status"] == "failed" else "valid_zugferd"


def _process_document(contents: bytes, filename: str, profile: str, auto_generate: bool = True,
                      source_path: Optional[str] = None) -> Dict[str, Any]:
    """Shared pipeline for browser upload, Windows dialog and files dragged onto the .exe."""
    filename = _safe_filename(filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(status_code=400,
                            detail="Ungültiges Dateiformat. Bitte eine PDF- oder Word-Datei (.docx, .doc) wählen.")
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Datei zu groß (maximal 25 MB).")
    if not contents:
        raise HTTPException(status_code=400, detail="Die Datei ist leer.")
    profile = _profile(profile)

    session_id = str(uuid.uuid4())
    folder = _session_dir(session_id, must_exist=False)
    folder.mkdir(parents=True)
    (folder / filename).write_bytes(contents)
    _write_meta(folder, {"filename": filename, "source_path": source_path,
                         "created_at": datetime.now().isoformat()})
    notes: list = []
    if not source_path:
        notes.append("Der Ordner der Datei ist dem Browser nicht bekannt – die ZUGFeRD-PDF wird im "
                     "Ausgabeordner aus der Config bzw. neben ZUGFeRD-Studio.exe gespeichert. "
                     "Mit 'Datei öffnen…' wird sie neben der Ursprungsrechnung abgelegt.")

    try:
        if suffix in (".docx", ".doc"):
            try:
                pdf_bytes, used_word = convert_docx_to_pdf(str(folder / filename))
            except ConversionError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            if not used_word:
                notes.append("Microsoft Word nicht verfügbar – das Dokument wurde vereinfacht neu gesetzt. "
                             "Für das Originallayout als PDF verwenden.")
        else:
            pdf_bytes = contents
        (folder / "base_document.pdf").write_bytes(pdf_bytes)

        try:
            parsed = parse_uploaded_file(pdf_bytes, "document.pdf")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"PDF konnte nicht gelesen werden: {exc}")
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise

    data = parsed["invoice_data"]
    notes += parsed["extraction_notes"]
    existing = parsed["existing_zugferd_info"]
    meta = _read_meta(folder)
    meta.update({
        "invoice_id": data.get("invoice_id", ""),
        "profile": profile,
        "ai_review_attempted": parsed["ai_review_attempted"],
        "ai_review_used": parsed["ai_review_used"],
        "ai_model": parsed["ai_model"],
    })

    if existing:
        # Already a hybrid invoice: show what is inside, write nothing new.
        (folder / "zugferd_invoice.pdf").write_bytes(pdf_bytes)
        (folder / "factur-x.xml").write_text(existing["xml"], encoding="utf-8")
        level = existing["level"] if existing["level"] in PROFILES else "en16931"
        meta["profile"] = level
        _write_meta(folder, meta)
        return _build_response(session_id, folder, status="already_zugferd", profile=level, data=data,
                               xml=existing["xml"], notes=notes, existing=True,
                               business_rules=check_business_rules(existing["xml"], level))

    _write_meta(folder, meta)
    if auto_generate and not parsed["ai_review_needed"] and not has_errors(validate_invoice(data)):
        try:
            xml, rules, output = _generate(folder, pdf_bytes, data, profile)
            return _build_response(session_id, folder, status=_status_for(rules), profile=profile,
                                   data=data, xml=xml, notes=notes, business_rules=rules, output=output)
        except Exception as exc:
            notes.append(f"Automatische Erzeugung fehlgeschlagen: {exc}")
    return _build_response(session_id, folder, status="needs_review", profile=profile, data=data, notes=notes)


def _process_local_path(path: str, profile: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"Datei nicht gefunden: {p}")
    if p.suffix.lower() not in SUPPORTED:
        raise HTTPException(status_code=400, detail="Bitte eine PDF- oder Word-Datei (.docx, .doc) wählen.")
    if p.stat().st_size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Datei zu groß (maximal 25 MB).")
    return _process_document(p.read_bytes(), p.name, profile, source_path=str(p.resolve()))


def queue_file(path: str) -> None:
    """Called by the launcher for an invoice dragged onto the .exe."""
    with _pending_lock:
        _pending.append(path)


# ------------------------------------------------------------------ routes

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>ZUGFeRD Tool Server Running</h1>")


@app.get("/api/ping")
def ping():
    return {"app": "zugferd-studio"}


@app.post("/api/upload")
def upload_document(file: UploadFile = File(...), profile: str = Form("en16931"),
                    auto_generate: bool = Form(True)):
    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    return _process_document(contents, file.filename or "", profile, auto_generate)


@app.post("/api/pick-file")
def pick_file(req: LocalFileRequest):
    """Windows 'Open' dialog on this machine; the folder of the chosen file is known."""
    try:
        path = pick_file_dialog()
    except Exception as exc:
        raise HTTPException(status_code=501, detail=f"Dateidialog nicht verfügbar: {exc}")
    if not path:
        return {"cancelled": True}
    return _process_local_path(path, req.profile)


@app.post("/api/pending")
def take_pending(req: LocalFileRequest):
    """Process the next invoice that was dragged onto the .exe (if any)."""
    with _pending_lock:
        path = _pending.popleft() if _pending else None
    if not path:
        return {"empty": True}
    return _process_local_path(path, req.profile)


@app.post("/api/queue-file")
def queue_file_endpoint(req: QueueRequest, request: Request):
    """Second launcher instance hands its file to the running one."""
    if not secrets.compare_digest(request.headers.get("x-instance-token", ""), INSTANCE_TOKEN):
        raise HTTPException(status_code=403, detail="Nicht erlaubt.")
    queue_file(req.path)
    return {"queued": True}


@app.post("/api/generate")
def generate_custom_zugferd(req: GenerateRequest):
    """(Re-)generate the ZUGFeRD invoice from the (possibly corrected) form data."""
    folder = _session_dir(req.session_id)
    profile = _profile(req.profile)

    errors, warnings = split_issues(validate_invoice(req.invoice_data))
    if errors:
        return JSONResponse(status_code=422, content={
            "detail": "Die Rechnungsdaten sind unvollständig: " + " ".join(e["message"] for e in errors),
            "issues": {"errors": errors, "warnings": warnings},
        })
    try:
        xml, rules, output = _generate(folder, _base_pdf(folder), req.invoice_data, profile)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Fehler bei ZUGFeRD-Generierung: {exc}")

    meta = _read_meta(folder)
    meta.update({"invoice_id": req.invoice_data.get("invoice_id", ""), "profile": profile})
    _write_meta(folder, meta)
    return _build_response(req.session_id, folder, status=_status_for(rules), profile=profile,
                           data=req.invoice_data, xml=xml, business_rules=rules, output=output)


@app.post("/api/calculate")
def calculate(req: CalculateRequest):
    """Live totals + validation for the editor (no files are written)."""
    errors, warnings = split_issues(validate_invoice(req.invoice_data))
    return {"totals": totals_to_json(compute_totals(req.invoice_data)),
            "issues": {"errors": errors, "warnings": warnings}}


@app.get("/api/download/pdf/{session_id}")
def download_pdf(session_id: str):
    folder = _session_dir(session_id)
    path = folder / "zugferd_invoice.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="ZUGFeRD-PDF nicht gefunden.")
    name = output_name(_read_meta(folder).get("filename") or "rechnung.pdf")
    return FileResponse(path, filename=name, media_type="application/pdf")


@app.get("/api/download/xml/{session_id}")
def download_xml(session_id: str):
    folder = _session_dir(session_id)
    path = folder / "factur-x.xml"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Factur-X XML nicht gefunden.")
    name = Path(_read_meta(folder).get("filename") or "rechnung").stem + "_Zug.xml"
    return FileResponse(path, filename=name, media_type="application/xml")


@app.get("/api/preview/{session_id}")
def preview_pdf(session_id: str):
    folder = _session_dir(session_id)
    path = folder / "zugferd_invoice.pdf"
    if not path.exists():
        path = folder / "base_document.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Keine PDF-Vorschau verfügbar.")
    return Response(content=path.read_bytes(), media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=preview.pdf"})


@app.get("/api/sample")
def create_sample_invoice():
    """A made-up sample invoice (all names and numbers are fictitious). Not saved to disk."""
    session_id = str(uuid.uuid4())
    folder = _session_dir(session_id, must_exist=False)
    folder.mkdir(parents=True)

    now = datetime.now()
    data = {
        "invoice_id": f"RE-{now:%Y%m}-4092",
        "issue_date": f"{now:%d.%m.%Y}",
        "due_date": (now + timedelta(days=14)).strftime("%d.%m.%Y"),
        "delivery_date": f"{now:%d.%m.%Y}",
        "currency": "EUR",
        "note": "Muster-Rechnung (alle Angaben fiktiv)",
        "buyer_reference": "",
        "seller": {"name": "CloudNova Systems GmbH", "street": "Maximilianstraße 35", "postcode": "80539",
                   "city": "München", "country": "DE", "vat_id": "DE329841102", "tax_number": "",
                   "email": "rechnung@cloudnova-systems.example", "phone": "+49 89 123456-0"},
        "buyer": {"name": "Digital Retail Solutions AG", "street": "Friedrichstraße 176", "postcode": "10117",
                  "city": "Berlin", "country": "DE", "vat_id": "DE284759301", "email": "", "phone": ""},
        "payment": {"iban": "DE89370400440532013000", "bic": "COBADEFFXXX",
                    "account_holder": "CloudNova Systems GmbH",
                    "terms": "Zahlbar innerhalb von 14 Tagen ohne Abzug.", "skonto_days": 0, "skonto_percent": 0},
        "items": [
            {"name": "Implementierung ZUGFeRD E-Rechnungs-Schnittstelle", "quantity": 12, "unit": "HUR",
             "unit_price": 125.00, "tax_percent": 19},
            {"name": "Cloud Infrastruktur Setup & Monitoring", "quantity": 1, "unit": "C62",
             "unit_price": 450.00, "tax_percent": 19},
            {"name": "Technischer Support & Wartungspaket", "quantity": 2, "unit": "MON",
             "unit_price": 190.00, "tax_percent": 19},
        ],
        "prepaid_amount": 0, "preceding_invoices": [], "stated": {},
    }

    pdf_bytes = create_invoice_pdf_from_data(data)
    (folder / "base_document.pdf").write_bytes(pdf_bytes)
    _write_meta(folder, {"filename": "muster_rechnung.pdf", "invoice_id": data["invoice_id"],
                         "profile": "en16931", "sample": True, "created_at": now.isoformat()})
    xml, rules, _ = _generate(folder, pdf_bytes, data, "en16931")
    return _build_response(session_id, folder, status=_status_for(rules), profile="en16931",
                           data=data, xml=xml, business_rules=rules)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000)
