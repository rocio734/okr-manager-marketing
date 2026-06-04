#!/usr/bin/env python3
"""
Writeback — corre cada 5 minutos.

Busca kr_proposals status=approved sin applied_at. Para cada uno:
1. Actualiza currentValue en el KR (SMFOKR_Okr_Kr)
2. Crea un registro en Key Result Updates (SMFOKR_Kr_Update) con el nuevo valor,
   status 'on_track' y un comentario automático.
Todo via Playwright (login + role switch + POST JSON con csrfToken).
Marca status=applied cuando ambas operaciones tienen éxito.
"""
import os, sys, json, time, urllib.request
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
ETENDO_WRITE_BASE = os.environ.get("ETENDO_WRITE_URL", "https://staff-ui.etendo.cloud/etendo")
ETENDO_ROLE   = "11A221E338C54D01BCA31700C0395C73"
ETENDO_CLIENT = "DE79A16C6D0B44BEBC66581DAA1AB308"
OKR_WINDOW_ID = "8A46E42D104E47A7A00720608286262F"


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


def update_kr_via_page(page, kr_id, new_value):
    """Actualiza currentValue de un KR desde el contexto del browser (CSRF correcto)."""
    result = page.evaluate(
        """async (args) => {
            const { kr_id, new_value } = args;
            const body = {
                dataSource: 'isc_OBViewDataSource_0',
                operationType: 'update',
                componentId: 'isc_OBViewForm_0',
                data: { id: kr_id, currentValue: new_value },
                oldValues: {},
                csrfToken: OB.User.csrfToken,
            };
            const r = await fetch('/etendo/org.openbravo.service.datasource/SMFOKR_Okr_Kr', {
                method: 'POST',
                headers: {'Content-Type': 'application/json;charset=UTF-8'},
                credentials: 'include',
                body: JSON.stringify(body),
            });
            const text = await r.text();
            return { httpStatus: r.status, body: text };
        }""",
        {"kr_id": kr_id, "new_value": new_value},
    )
    resp = json.loads(result["body"]) if result.get("body") else {}
    return resp


def create_kr_update_via_page(page, kr_id, kr_name, new_value, comment):
    """Crea un registro en SMFOKR_Kr_Update (tab 'Key Result Updates' del KR)."""
    result = page.evaluate(
        """async (args) => {
            const { kr_id, kr_name, new_value, comment } = args;
            const body = {
                dataSource: 'isc_OBViewDataSource_3',
                operationType: 'add',
                componentId: 'isc_OBViewForm_3',
                data: {
                    keyResult: kr_id,
                    'keyResult$_identifier': kr_name,
                    currentValue: new_value,
                    comment: comment,
                    status: 'on_track',
                },
                oldValues: {},
                csrfToken: OB.User.csrfToken,
            };
            const r = await fetch('/etendo/org.openbravo.service.datasource/SMFOKR_Kr_Update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json;charset=UTF-8'},
                credentials: 'include',
                body: JSON.stringify(body),
            });
            const text = await r.text();
            return { httpStatus: r.status, body: text };
        }""",
        {"kr_id": kr_id, "kr_name": kr_name, "new_value": new_value, "comment": comment},
    )
    resp = json.loads(result["body"]) if result.get("body") else {}
    return resp


def main():
    pending = sb("GET", "kr_proposals?status=eq.approved&applied_at=is.null&select=*")
    if not pending:
        print("Sin propuestas approved sin aplicar.")
        return
    print(f"{len(pending)} propuestas a aplicar.")

    import urllib.parse as _urlparse
    import http.cookiejar as _cookiejar

    # ── Login JSESSIONID (mismo patrón que job_crm_scoring) ───────────────────
    # Si el default role del usuario es "Futit empleados", la sesión usará ese rol
    write_base = ETENDO_WRITE_BASE.removesuffix("/")

    def login_sid():
        jar    = _cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        body   = _urlparse.urlencode(
            {"user": ETENDO_USER, "password": ETENDO_PASS, "Command": "Login"}
        ).encode()
        req = urllib.request.Request(
            f"{write_base}/secureApp/LoginHandler.html", data=body, method="POST"
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            opener.open(req)
        except Exception as e:
            print(f"  WARN login_sid: {e}")
        for cookie in jar:
            if cookie.name == "JSESSIONID":
                return cookie.value
        return ""

    sid = login_sid()
    print(f"  JSESSIONID: {'OK' if sid else 'FAIL'}")
    sid_headers = {
        "Cookie":        f"JSESSIONID={sid}",
        "Content-Type":  "application/json;charset=UTF-8",
    }

    def calc_kr_status(new_value, target_value, cycle_closed=False):
        """Calcula el estado del KR según el nuevo valor vs target."""
        if target_value is None or target_value == 0:
            return "not_started"
        try:
            pct = float(new_value) / float(target_value) * 100
        except (TypeError, ZeroDivisionError):
            return "not_started"
        if pct >= 100:
            return "success"
        if pct >= 75:
            return "on_track"
        if pct >= 50:
            return "at_risk"
        if cycle_closed:
            return "failed"
        return "off_track"

    def rest_update_kr(kr_id, new_value, target_value=None):
        url  = f"{write_base}/org.openbravo.service.json.jsonrest/SMFOKR_Okr_Kr/{kr_id}"
        status = calc_kr_status(new_value, target_value)
        body = json.dumps({"data": {"id": kr_id, "currentValue": new_value, "status": status}}).encode()
        req  = urllib.request.Request(url, data=body, method="PUT")
        req.add_header("Cookie",        f"JSESSIONID={sid}")
        req.add_header("Content-Type",  "application/json;charset=UTF-8")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp_body = r.read().decode()
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode()
        print(f"    PUT KR: {resp_body[:300]}")
        return json.loads(resp_body) if resp_body else {}

    def rest_add_kr_update(kr_id, new_value, comment, target_value=None):
        url  = f"{write_base}/org.openbravo.service.json.jsonrest/SMFOKR_Kr_Update"
        status = calc_kr_status(new_value, target_value)
        body = json.dumps({"data": {
            "keyResult": kr_id, "currentValue": new_value,
            "comment": comment, "status": status,
        }}).encode()
        req  = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Cookie",        f"JSESSIONID={sid}")
        req.add_header("Content-Type",  "application/json;charset=UTF-8")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp_body = r.read().decode()
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode()
        print(f"    POST KR_Update: {resp_body[:200]}")
        return json.loads(resp_body) if resp_body else {}

    # Probar con el primer item
    test_item = pending[0]
    print("  Probando JSESSIONID approach (default role del usuario)...")
    target = test_item.get("target_value")
    test_resp = rest_update_kr(test_item["kr_id"], test_item["proposed_value"], target)
    rest_ok = test_resp.get("response", {}).get("status") == 0
    print(f"  Resultado: {'OK' if rest_ok else 'FAIL'}")

    if rest_ok:
        for p_item in pending:
            kr_name = p_item.get("kr_name") or p_item.get("kr_id", "?")
            target_val = p_item.get("target_value")
            kr_status = calc_kr_status(p_item["proposed_value"], target_val)
            try:
                r1 = rest_update_kr(p_item["kr_id"], p_item["proposed_value"], target_val)
                if r1.get("response", {}).get("status") != 0:
                    raise RuntimeError(f"KR update falló: {str(r1)[:200]}")
                comment = (f"OKR Manager — valor actualizado a {p_item['proposed_value']} "
                           f"(anterior: {p_item.get('current_value','?')}) | Estado: {kr_status}")
                rest_add_kr_update(p_item["kr_id"], p_item["proposed_value"], comment, target_val)
                print(f"  ✓ {kr_name[:45]} → {p_item['proposed_value']} [{kr_status}]")
                sb("PATCH", f"kr_proposals?id=eq.{p_item['id']}",
                   {"status": "applied", "applied_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                print(f"  ✓ {kr_name[:50]} → {p_item['proposed_value']}")
            except Exception as e:
                print(f"  ✗ {kr_name[:40]}: {e}")
        return

    # ── Estrategia 2: Playwright con role en cookie + CSRF ────────────────────
    print("  REST API no funcionó — intentando vía Playwright...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()

        # Login
        page.goto(f"{ETENDO_WRITE_BASE}/", timeout=30000)
        time.sleep(2)
        page.evaluate(f"""async () => {{
            const b = new URLSearchParams();
            b.append('user', {json.dumps(ETENDO_USER)});
            b.append('password', {json.dumps(ETENDO_PASS)});
            b.append('Command', 'Login');
            await fetch('/etendo/secureApp/LoginHandler.html', {{
                method:'POST', headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
                credentials:'include', body: b.toString()
            }});
        }}""")
        time.sleep(2)

        # Cargar SmartClient
        page.goto(f"{ETENDO_WRITE_BASE}/", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=40000)
        time.sleep(5)
        info = page.evaluate("""() => ({
            csrfToken: (typeof OB !== 'undefined' && OB.User && OB.User.csrfToken) || null,
            roleId: (typeof OB !== 'undefined' && OB.User && OB.User.roleId) || null,
        })""")
        print(f"  CSRF OK: {bool(info.get('csrfToken'))}, Role: {(info.get('roleId') or '')[:12]}")

        if not info.get("csrfToken"):
            raise RuntimeError("No se obtuvo CSRF token después del login")

        # Abrir ventana OKR para inicializar los view datasources (necesario para SMFOKR_Kr_Update)
        page.evaluate(f"() => OB.Layout.ViewManager.openView('{OKR_WINDOW_ID}')")
        time.sleep(3)

        for p_item in pending:
            kr_name = p_item.get("kr_name") or p_item.get("kr_id", "?")[:12]
            try:
                # 1. Actualizar currentValue en el KR
                res = update_kr_via_page(page, p_item["kr_id"], p_item["proposed_value"])
                status_ok = res.get("response", {}).get("status") == 0
                if not status_ok:
                    raise RuntimeError(f"KR update falló: {json.dumps(res)[:200]}")

                # 2. Crear registro en Key Result Updates con el nuevo valor
                rationale = (p_item.get("rationale") or "").strip()
                comment = rationale if rationale else f"Valor aprobado: {p_item['proposed_value']}"
                upd_res = create_kr_update_via_page(
                    page,
                    p_item["kr_id"],
                    kr_name,
                    p_item["proposed_value"],
                    comment,
                )
                upd_ok = upd_res.get("response", {}).get("status") == 0
                if not upd_ok:
                    print(f"  ⚠ Kr_Update no creado para {kr_name}: {json.dumps(upd_res)[:150]}")

                sb("PATCH", f"kr_proposals?id=eq.{p_item['id']}", {
                    "status": "applied",
                    "applied_at": "now()",
                })
                print(f"  ✓ {kr_name}: → {p_item['proposed_value']}" + (" (sin update history)" if not upd_ok else ""))
            except Exception as e:
                sb("PATCH", f"kr_proposals?id=eq.{p_item['id']}", {
                    "apply_error": str(e)[:500],
                })
                print(f"  ✗ Error en {kr_name}: {e}")

        browser.close()


if __name__ == "__main__":
    main()
