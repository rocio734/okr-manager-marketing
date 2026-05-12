#!/usr/bin/env python3
"""
Writeback — corre cada 5 minutos.

Busca kr_proposals status=approved sin applied_at. Para cada uno escribe el nuevo
valor en Etendo via JWT API (sin Playwright). Marca status=applied.
"""
import os, sys, json, urllib.request, urllib.parse
from pathlib import Path

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
ETENDO_BASE   = os.environ.get("ETENDO_BASE_URL") or os.environ.get("ETENDO_BASE", "https://futit-staff.etendo.cloud")
ETENDO_ROLE   = os.environ.get("ETENDO_WRITEBACK_ROLE_ID", "11A221E338C54D01BCA31700C0395C73")


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


def etendo_login():
    body = json.dumps({"username": ETENDO_USER, "password": ETENDO_PASS, "role": ETENDO_ROLE}).encode()
    req  = urllib.request.Request(f"{ETENDO_BASE}/api/auth/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["token"]


def update_kr(jwt, kr_id, new_value):
    """Actualiza currentValue de un KR via REST PUT (sin CSRF)."""
    body = json.dumps({"id": kr_id, "currentValue": new_value}).encode()
    req = urllib.request.Request(
        f"{ETENDO_BASE}/api/datasource/SMFOKR_Okr_Kr",
        data=body, method="PUT",
    )
    req.add_header("Authorization", f"Bearer {jwt}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode()
        return json.loads(text) if text else {"response": {"status": 0}}


def main():
    pending = sb("GET", "kr_proposals?status=eq.approved&applied_at=is.null&select=*")
    if not pending:
        print("Sin propuestas approved sin aplicar.")
        return
    print(f"{len(pending)} propuestas a aplicar.")

    jwt = etendo_login()
    print("  Login Etendo OK")

    for p in pending:
        kr_name = p.get("kr_name") or p.get("kr_id", "?")[:8]
        try:
            res = update_kr(jwt, p["kr_id"], p["proposed_value"])
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
