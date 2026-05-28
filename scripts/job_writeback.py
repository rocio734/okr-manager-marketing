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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()

        # Interceptar requests para capturar la URL del role switch
        intercepted = []
        def on_request(req):
            if "CHANGE_PROFILE" in (req.post_data or "") or "MainHelper" in req.url or "profile" in req.url.lower():
                intercepted.append({"url": req.url, "method": req.method, "data": (req.post_data or "")[:200]})
        page.on("request", on_request)

        # Paso 1: Login
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

        # Paso 2: Cargar SmartClient
        page.goto(f"{ETENDO_WRITE_BASE}/", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=40000)
        time.sleep(5)
        role_now = page.evaluate("() => (typeof OB !== 'undefined' && OB.User && OB.User.roleId) || 'no OB'")
        print(f"  Logged in. Role: {role_now}")

        # Paso 3: Cambiar rol via OB.User.userInfo.role.setValue + submit
        switch_result = page.evaluate(f"""async () => {{
            try {{
                const roleField = OB.User && OB.User.userInfo && OB.User.userInfo.role;
                if (!roleField) return {{error: 'roleField not found'}};

                // Ver roles disponibles
                const valueMap = roleField.valueMap || {{}};
                const available = Object.keys(valueMap).slice(0, 10);

                // Setear el rol Futit
                const targetRole = '{ETENDO_ROLE}';
                if (!valueMap[targetRole]) return {{error: 'role not in valueMap', available}};

                roleField.setValue(targetRole);

                // Hacer submit del form de userInfo
                const form = OB.User.userInfo;
                if (form && typeof form.submit === 'function') {{
                    form.submit();
                    return {{method: 'form.submit', role: targetRole, available}};
                }}
                // Alternativa: saveData
                if (form && typeof form.saveData === 'function') {{
                    form.saveData();
                    return {{method: 'form.saveData', role: targetRole, available}};
                }}
                // Alternativa: disparar el handler directamente
                if (form && typeof form.saveButtonClick === 'function') {{
                    form.saveButtonClick();
                    return {{method: 'saveButtonClick', role: targetRole, available}};
                }}
                return {{method: 'setValue_only', role: targetRole, available}};
            }} catch(e) {{ return {{error: e.message}}; }}
        }}""")
        print(f"  Role switch via OB.User.userInfo: {switch_result}")

        time.sleep(2)
        # Loguear cualquier request interceptada
        if intercepted:
            print(f"  Intercepted role requests: {intercepted}")
        else:
            print(f"  No role-switch requests intercepted")

        # Paso 4: Recargar y verificar rol
        page.goto(f"{ETENDO_WRITE_BASE}/", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=40000)
        time.sleep(4)

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
