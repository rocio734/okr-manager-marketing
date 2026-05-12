#!/usr/bin/env python3
"""
Writeback — corre cada 5 minutos.

Busca kr_proposals status=approved sin applied_at. Para cada uno escribe el nuevo
valor en Etendo via SmartClient (JSESSIONID + CSRF obtenidos con Playwright).
Marca status=applied.
"""
import os, sys, json, urllib.request, urllib.parse
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

SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_SERVICE_KEY"]
ETENDO_USER   = os.environ["ETENDO_USERNAME"]
ETENDO_PASS   = os.environ["ETENDO_PASSWORD"]
# URL del staff UI clásico (SmartClient) — diferente al API base
ETENDO_WRITE_BASE = os.environ.get("ETENDO_WRITE_URL", "https://staff-ui.etendo.cloud/etendo")


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
    """Loguea en staff UI con Playwright, devuelve (jsessionid, csrf_token)."""
    login_url = f"{ETENDO_WRITE_BASE}/security/Login"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
        page.fill('input[name="user"]', ETENDO_USER, timeout=30000)
        page.fill('input[name="password"]', ETENDO_PASS, timeout=10000)
        # Click submit — puede ser button o input[type=submit]
        page.evaluate("""() => {
            const btn = document.querySelector('button[type="submit"]') ||
                        document.querySelector('input[type="submit"]') ||
                        document.querySelector('button');
            if (btn) btn.click();
        }""")
        page.wait_for_load_state("networkidle", timeout=60000)
        csrf = page.evaluate(
            "() => (window.OB && OB.User && OB.User.csrfToken) || null"
        )
        cookies = ctx.cookies()
        jsess = next((c["value"] for c in cookies if c["name"] == "JSESSIONID"), None)
        browser.close()
    if not jsess:
        raise RuntimeError(f"No se obtuvo JSESSIONID. csrf={csrf!r}")
    if not csrf:
        raise RuntimeError(f"No se obtuvo CSRF token. jsess={jsess!r}")
    return jsess, csrf


def update_kr(jsess, csrf, kr_id, new_value):
    """Actualiza currentValue de un KR via SmartClient datasource."""
    body = urllib.parse.urlencode({
        "_operationType": "update",
        "id": kr_id,
        "currentValue": str(new_value),
        "csrfToken": csrf,
    }).encode()
    req = urllib.request.Request(
        f"{ETENDO_WRITE_BASE}/org.openbravo.service.datasource/SMFOKR_Okr_Kr",
        data=body, method="POST",
    )
    req.add_header("Cookie", f"JSESSIONID={jsess}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    pending = sb("GET", "kr_proposals?status=eq.approved&applied_at=is.null&select=*")
    if not pending:
        print("Sin propuestas approved sin aplicar.")
        return
    print(f"{len(pending)} propuestas a aplicar.")

    jsess, csrf = login_and_get_session()
    print("  Login staff UI OK")

    for p in pending:
        kr_name = p.get("kr_name") or p.get("kr_id", "?")[:8]
        try:
            res = update_kr(jsess, csrf, p["kr_id"], p["proposed_value"])
            status_ok = res.get("response", {}).get("status") == 0
            if not status_ok:
                raise RuntimeError(f"Respuesta inesperada: {json.dumps(res)[:200]}")
            sb("PATCH", f"kr_proposals?id=eq.{p['id']}", {
                "status": "applied",
                "applied_at": "now()",
            })
            print(f"  ✓ KR {kr_name}: → {p['proposed_value']}")
        except Exception as e:
            sb("PATCH", f"kr_proposals?id=eq.{p['id']}", {
                "apply_error": str(e)[:500],
            })
            print(f"  ✗ Error en {kr_name}: {e}")


if __name__ == "__main__":
    main()
