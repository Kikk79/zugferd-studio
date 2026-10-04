import os
import sys
import tempfile
from pathlib import Path

import pytest

# Keep test runs out of the project folder and never open PDF viewers:
# config + fallback output folder and the session scratch space go to temp dirs.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="zugferd-tests-"))
os.environ["ZUGFERD_DATA_DIR"] = str(_TEST_ROOT / "exe-dir")
os.environ["ZUGFERD_WORK_DIR"] = str(_TEST_ROOT / "work")
os.environ["ZUGFERD_NO_OPEN"] = "1"
os.environ["ZUGFERD_AI_ENABLED"] = "false"
(_TEST_ROOT / "exe-dir").mkdir(parents=True, exist_ok=True)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def exe_dir() -> Path:
    """The folder that plays the role of 'next to the .exe' in tests."""
    return Path(os.environ["ZUGFERD_DATA_DIR"])


@pytest.fixture()
def invoice():
    """A small, fully valid invoice."""
    return {
        "invoice_id": "RE-1", "issue_date": "01.10.2026", "currency": "EUR",
        "seller": {"name": "Muster GmbH", "street": "Weg 1", "postcode": "80331", "city": "München",
                   "country": "DE", "vat_id": "DE123456789"},
        "buyer": {"name": "Kunde AG", "street": "Allee 2", "postcode": "10115", "city": "Berlin", "country": "DE"},
        "payment": {"iban": "DE89370400440532013000"},
        "items": [{"name": "Beratung & Support", "quantity": 3, "unit": "HUR", "unit_price": 100, "tax_percent": 19},
                  {"name": "Buch", "quantity": 2, "unit": "C62", "unit_price": 19.99, "tax_percent": 7}],
    }
