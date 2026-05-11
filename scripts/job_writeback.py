#!/usr/bin/env python3
"""
Writeback — corre cada 5 minutos.

Busca kr_proposals status=approved sin applied_at. Para cada uno escribe el nuevo
valor en Etendo via SmartClient (CSRF token vía Playwright login). Marca status=applied.

Reusa la lógica de scripts/okr_load_q2_2026_smartclient.py.
"""
import os, sys, json, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ENV  = ROOT.parent / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ETENDO_USER  = os.environ["ETENDO_USERNAME"]
ETENDO_PASS  = os.environ["ETENDO_PASSWORD"]
ETENDO_LOGIN_URL = os.environ.get("ETENDO_LOGIN_URL", "https://staff-ui.etendo.cloud/etendo/security/Login.html")
ETENDO_BASE_UI   = os.environ.get("ETENDO_BASE_UI",   "https://staff-ui.etendo.cloud/etendo")

def sb(method, path, body=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        t = r.read().decode()
        return json.loads(t) if t else None

def login_and_get_session():
    """Loguea con Playwright, devuelve (jsessionid, csrf_token, cookies)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(ETENDO_LOGIN_URL, wait_until="networkidle")
        page.fill('input[name="user"]', ETENDO_USER)
        page.fill('input[name="password"]', ETENDO_PASS)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        # Extraer CSRF token
        csrf = page.evaluate("() => (window.OB && OB.User && OB.User.csrfToken) || null")
        cookies = ctx.cookies()
        jsess = next((c["value"] for c in cookies if c["name"] == "JSESSIONID"), None)
        browser.close()
    if not csrf or not jsess:
        raise RuntimeError(f"No se obtuvieron csrf/jsess: csrf={csrf!r} jsess={jsess!r}")
    return jsess, csrf

def update_kr(jsess, csrf, kr_id, new_value):
    """Update via SmartClient datasource."""
    url = f"{ETENDO_BASE_UI}/org.openbravo.service.datasource/Okr_Kr"
    payload = {
        "operationType": "update",
        "data": {
            "id": kr_id,
            "currentvalue": new_value,
        },
        "csrfToken": csrf,
    }
    body = ("dataSource=Okr_Kr&operationType=update&data=" +
            urllib.parse.quote(json.dumps(payload["data"])) +
            f"&csrfToken={urllib.parse.quote(csrf)}").encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Cookie": f"JSESSIONID={jsess}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def main():
    pending = sb("GET", "kr_proposals?status=eq.approved&applied_at=is.null&select=*")
    if not pending:
        print("Sin propuestas approved sin aplicar.")
        return
    print(f"{len(pending)} propuestas a aplicar.")
    jsess, csrf = login_and_get_session()
    for p in pending:
        try:
            res = update_kr(jsess, csrf, p["kr_id"], p["proposed_value"])
            sb("PATCH", f"kr_proposals?id=eq.{p['id']}", {
                "status": "applied",
                "applied_at": "now()",
            })
            print(f"  ✓ KR {p['kr_name']}: {p['current_value']} → {p['proposed_value']}")
        except Exception as e:
            sb("PATCH", f"kr_proposals?id=eq.{p['id']}", {
                "apply_error": str(e)[:500],
            })
            print(f"  ✗ Error en {p['kr_name']}: {e}")

if __name__ == "__main__":
    import urllib.parse
    main()
