"""
Central config. All values come from environment variables so nothing
sensitive is hard-coded.
"""
import os

from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(os.path.dirname(_HERE), ".env"))
load_dotenv(os.path.join(_HERE, ".env"), override=True)


def _env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(value).strip().strip('"')


def _normalize_odoo_url(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith("/odoo"):
        url = url[: -len("/odoo")]
    return url


ODOO_URL = _normalize_odoo_url(_env("ODOO_URL"))
ODOO_DB = _env("ODOO_DB")
ODOO_USERNAME = _env("ODOO_USERNAME")
ODOO_API_KEY = _env("ODOO_API_KEY")

def _optional_int(name: str):
    raw = os.getenv(name)
    if not raw or not str(raw).strip():
        return None
    return int(str(raw).strip().strip('"'))


ODOO_JOURNAL_ID = int(_env("ODOO_JOURNAL_ID"))
ODOO_SALES_ACCOUNT_ID = int(_env("ODOO_SALES_ACCOUNT_ID"))
ODOO_TAX_ID = int(_env("ODOO_TAX_ID"))
ODOO_WALKIN_PARTNER_ID = int(_env("ODOO_WALKIN_PARTNER_ID"))

# Only required when running with --pay (bank/cash journal for payment registration).
ODOO_PAYMENT_JOURNAL_ID = _optional_int("ODOO_PAYMENT_JOURNAL_ID")
