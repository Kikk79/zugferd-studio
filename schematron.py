"""
schematron.py - Optional EN 16931 business-rule validation (BR-CO-*, BR-S-*, ...).

Runs the official Factur-X schematron (compiled XSLT shipped with the factur-x
package) through Saxon-HE on a local Java runtime. If Java or tools/saxon-he.jar
is missing, the check reports "unavailable" instead of failing - the XSD
validation in zugferd_generator.py always runs.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict

import facturx
from paths import RESOURCE_DIR
from lxml import etree

SAXON_JAR = RESOURCE_DIR / "tools" / "saxon-he.jar"
XSLT_BY_PROFILE = {"en16931": "facturx-en16931/FACTUR-X_EN16931.xslt"}


def check_business_rules(xml_str: str, profile: str = "en16931") -> Dict[str, Any]:
    """Returns {status: passed|failed|unavailable, failures (fatal rules), warnings, detail}."""
    xslt_rel = XSLT_BY_PROFILE.get((profile or "").lower())
    if not xslt_rel:
        return {"status": "unavailable", "failures": [],
                "detail": "Geschäftsregeln werden nur für das Profil EN 16931 geprüft."}
    java = shutil.which("java")
    xslt = Path(facturx.__file__).parent / "xsd_and_schematron" / xslt_rel
    if not java or not SAXON_JAR.exists() or not xslt.exists():
        return {"status": "unavailable", "failures": [],
                "detail": "Java oder tools/saxon-he.jar nicht gefunden – Geschäftsregeln nicht geprüft."}

    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "invoice.xml", Path(tmp) / "result.svrl"
        src.write_text(xml_str, encoding="utf-8")
        try:
            proc = subprocess.run(
                [java, "-cp", str(SAXON_JAR), "net.sf.saxon.Transform",
                 f"-s:{src}", f"-xsl:{xslt}", f"-o:{out}"],
                capture_output=True, text=True, timeout=90)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"status": "unavailable", "failures": [], "detail": f"Prüfung nicht ausführbar: {exc}"}
        if proc.returncode != 0 or not out.exists():
            return {"status": "unavailable", "failures": [],
                    "detail": (proc.stderr or "Saxon-Fehler").strip()[:300]}
        svrl = out.read_text(encoding="utf-8")

    failures, warnings = [], []
    ns = {"svrl": "http://purl.oclc.org/dsdl/svrl"}
    for node in etree.fromstring(svrl.encode("utf-8")).iterfind(".//svrl:failed-assert", ns):
        text = node.findtext("svrl:text", default="", namespaces=ns)
        entry = {"id": node.get("id", "?"), "text": " ".join(text.split())}
        (warnings if node.get("flag") == "warning" else failures).append(entry)

    return {"status": "failed" if failures else "passed", "failures": failures, "warnings": warnings, "detail": ""}
