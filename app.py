"""
app.py - FastAPI server for the ZUGFeRD / Factur-X invoicing tool.

Upload a PDF/DOCX invoice -> fields are read out, can be corrected in the UI,
and a hybrid ZUGFeRD PDF (PDF + embedded factur-x.xml) is produced.
Everything runs locally; uploaded files live in ./storage/<session-uuid>/ and are
purged after SESSION_TTL_DAYS days.
"""

import json
import re
import shutil
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from extractor import parse_uploaded_file
from invoice_logic import (
    compute_totals, has_errors, split_issues, totals_to_json, validate_invoice,
)
from paths import DATA_DIR, RESOURCE_DIR
from pdf_renderer import ConversionError, convert_docx_to_pdf, create_invoice_pdf_from_data
from schematron import check_business_rules
from zugferd_generator import build_zugferd_xml, validate_and_create_zugferd_pdf

STORAGE_DIR = DATA_DIR / "storage"
STATIC_DIR = RESOURCE_DIR / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SESSION_TTL_DAYS = 7
PROFILES = {"en16931", "basic"}

STORAGE_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="ZUGFeRD Invoicing Tool",
    description="Converts PDF and DOCX invoices into ZUGFeRD / Factur-X electronic invoices.",
    version="2.0.0",
)


class GenerateRequest(BaseModel):
    session_id: str
    profile: str = "en16931"
    invoice_data: Dict[str, Any]


class CalculateRequest(BaseModel):
    invoice_data: Dict[str, Any]


# ------------------------------------------------------------------ session helpers

def _purge_old_sessions() -> None:
    cutoff = time.time() - SESSION_TTL_DAYS * 86400
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


def _download_name(prefix: str, invoice_id: str, ext: str) -> str:
    inv = re.sub(r"[^\w.\-]", "_", invoice_id or "rechnung")
    return f"{prefix}_{inv}.{ext}"


# ------------------------------------------------------------------ core pipeline

def _build_response(session_id: str, folder: Path, *, status: str, profile: str, data: Dict[str, Any],
                    xml: str = "", notes: Optional[list] = None, existing: bool = False,
                    business_rules: Optional[dict] = None) -> Dict[str, Any]:
    errors, warnings = split_issues(validate_invoice(data))
    has_pdf = (folder / "zugferd_invoice.pdf").exists()
    meta = _read_meta(folder)
    return {
        "session_id": session_id,
        "filename": meta.get("filename", ""),
        "status": status,
        "profile": profile,
        "is_existing_zugferd": existing,
        "invoice_data": data,
        "xml_content": xml,
        "totals": totals_to_json(compute_totals(data)),
        "issues": {"errors": errors, "warnings": warnings},
        "extraction_notes": notes or [],
        "business_rules": business_rules or {"status": "unavailable", "failures": [], "detail": ""},
        "pdf_download_url": f"/api/download/pdf/{session_id}" if has_pdf else None,
        "xml_download_url": f"/api/download/xml/{session_id}" if (folder / "factur-x.xml").exists() else None,
        "preview_url": f"/api/preview/{session_id}",
    }


def _generate(folder: Path, base_pdf: bytes, data: Dict[str, Any], profile: str):
    """Validate, build XML, embed it. Returns (xml, pdf_bytes, business_rules)."""
    xml = build_zugferd_xml(data, profile=profile)
    pdf = validate_and_create_zugferd_pdf(base_pdf, xml, profile=profile, data=data)
    (folder / "zugferd_invoice.pdf").write_bytes(pdf)
    (folder / "factur-x.xml").write_text(xml, encoding="utf-8")
    return xml, pdf, check_business_rules(xml, profile)


def _base_pdf(folder: Path) -> bytes:
    path = folder / "base_document.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Ausgangsdokument nicht gefunden.")
    return path.read_bytes()


def _status_for(rules: dict) -> str:
    return "business_rules_failed" if rules["status"] == "failed" else "valid_zugferd"


# ------------------------------------------------------------------ routes

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>ZUGFeRD Tool Server Running</h1>")


@app.post("/api/upload")
def upload_document(file: UploadFile = File(...), profile: str = Form("en16931"),
                    auto_generate: bool = Form(True)):
    filename = _safe_filename(file.filename or "")
    suffix = Path(filename).suffix.lower()
    if suffix not in (".pdf", ".docx", ".doc"):
        raise HTTPException(status_code=400,
                            detail="Ungültiges Dateiformat. Bitte laden Sie eine PDF- oder DOCX-Datei hoch.")
    profile = profile.lower() if profile.lower() in PROFILES else "en16931"

    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Datei zu groß (maximal 25 MB).")
    if not contents:
        raise HTTPException(status_code=400, detail="Die Datei ist leer.")

    session_id = str(uuid.uuid4())
    folder = _session_dir(session_id, must_exist=False)
    folder.mkdir(parents=True)
    (folder / filename).write_bytes(contents)
    _write_meta(folder, {"filename": filename, "created_at": datetime.now().isoformat()})
    notes: list = []

    try:
        if suffix in (".docx", ".doc"):
            try:
                pdf_bytes, used_word = convert_docx_to_pdf(str(folder / filename))
            except ConversionError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            if not used_word:
                notes.append("Microsoft Word nicht verfügbar – das Dokument wurde vereinfacht neu gesetzt. "
                             "Für das Originallayout als PDF hochladen.")
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
    meta.update({"invoice_id": data.get("invoice_id", ""), "profile": profile})

    if existing:
        # Already a hybrid invoice: keep it untouched, show what is inside.
        (folder / "zugferd_invoice.pdf").write_bytes(pdf_bytes)
        (folder / "factur-x.xml").write_text(existing["xml"], encoding="utf-8")
        level = existing["level"] if existing["level"] in PROFILES else "en16931"
        meta["profile"] = level
        _write_meta(folder, meta)
        return _build_response(session_id, folder, status="already_zugferd", profile=level, data=data,
                               xml=existing["xml"], notes=notes, existing=True,
                               business_rules=check_business_rules(existing["xml"], level))

    _write_meta(folder, meta)
    if auto_generate and not has_errors(validate_invoice(data)):
        try:
            xml, _, rules = _generate(folder, pdf_bytes, data, profile)
            return _build_response(session_id, folder, status=_status_for(rules), profile=profile,
                                   data=data, xml=xml, notes=notes, business_rules=rules)
        except Exception as exc:
            notes.append(f"Automatische Erzeugung fehlgeschlagen: {exc}")
    return _build_response(session_id, folder, status="needs_review", profile=profile, data=data, notes=notes)


@app.post("/api/generate")
def generate_custom_zugferd(req: GenerateRequest):
    """(Re-)generate the ZUGFeRD invoice from the (possibly corrected) form data."""
    folder = _session_dir(req.session_id)
    profile = req.profile.lower() if req.profile.lower() in PROFILES else "en16931"

    errors, warnings = split_issues(validate_invoice(req.invoice_data))
    if errors:
        return JSONResponse(status_code=422, content={
            "detail": "Die Rechnungsdaten sind unvollständig: " + " ".join(e["message"] for e in errors),
            "issues": {"errors": errors, "warnings": warnings},
        })
    try:
        xml, _, rules = _generate(folder, _base_pdf(folder), req.invoice_data, profile)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Fehler bei ZUGFeRD-Generierung: {exc}")

    meta = _read_meta(folder)
    meta.update({"invoice_id": req.invoice_data.get("invoice_id", ""), "profile": profile})
    _write_meta(folder, meta)
    return _build_response(req.session_id, folder, status=_status_for(rules), profile=profile,
                           data=req.invoice_data, xml=xml, business_rules=rules)


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
    name = _download_name("ZUGFeRD", _read_meta(folder).get("invoice_id", ""), "pdf")
    return FileResponse(path, filename=name, media_type="application/pdf")


@app.get("/api/download/xml/{session_id}")
def download_xml(session_id: str):
    folder = _session_dir(session_id)
    path = folder / "factur-x.xml"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Factur-X XML nicht gefunden.")
    name = _download_name("factur-x", _read_meta(folder).get("invoice_id", ""), "xml")
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
    """A made-up sample invoice (all names and numbers are fictitious)."""
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
                         "profile": "en16931", "created_at": now.isoformat()})
    xml, _, rules = _generate(folder, pdf_bytes, data, "en16931")
    return _build_response(session_id, folder, status=_status_for(rules), profile="en16931",
                           data=data, xml=xml, business_rules=rules)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000)
