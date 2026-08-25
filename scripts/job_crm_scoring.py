#!/usr/bin/env python3
"""
CRM Daily Classification — Etendo Revenue Org
Clasifica leads en SQL/IQL y envía reporte diario.
Módulo nuevo: ETCRM_Lead (com.etendoerp.crm)
  - Sin campos de scoring (scorePurchaseIntention/scoreIndex/score/strategicFit eliminados)
  - Escribe: classification (SQL | IQL) si no está seteado aún
  - Campo notas: description (antes: summary)
  - Statuses: New | Contacted | Qualified | Converted | Dead
Corre diariamente vía GitHub Actions.
"""

import http.cookiejar
import json
import os
import smtplib
import urllib.request
import urllib.parse
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── Config ─────────────────────────────────────────────────────────────────────
WRITE_URL     = os.getenv("ETENDO_WRITE_URL",  "https://staff-ui.etendo.cloud/etendo")
USERNAME      = os.getenv("ETENDO_USERNAME",   "Rocio Altamirano")
PASSWORD      = os.getenv("ETENDO_PASSWORD",   "tecnicia")

GMAIL_USER    = os.getenv("GMAIL_USER",        "victoria.miguez@smfconsulting.es")
GMAIL_PASS    = os.getenv("GMAIL_PASSWORD",    "oyuuywtiypdrinhe")
RECIPIENTS    = ["rocio.altamirano@smfconsulting.es", "victoria.miguez@smfconsulting.es"]

GENERIC_DOMAINS = {"gmail.com","hotmail.com","yahoo.com","outlook.com","hotmail.es","yahoo.es","gmail.es"}

SQL_ID          = "4847D259D5D544778884865219753DB3"
MQL_ID          = "64EB895C7E014E4FAF36CD160120792A"
IQL_ID          = "FBDED8EB276D4EC4B6C6A7AD6DA63BF1"
SERVICES_SQL_ID = "1C2297939247407CAA05F9D3A5862AC7"

# Rol Comercial — requerido para ETCRM_Lead sin depender del rol activo de Rocío
_COMERCIAL_ROLE = "8351131DFF384725AB08E06773FE6144"

LOG = lambda msg: print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ── Auth ───────────────────────────────────────────────────────────────────────
def login_sid():
    """JSESSIONID con selección de rol Comercial — para escrituras en ETCRM_Lead."""
    jar    = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    body   = urllib.parse.urlencode(
        {"user": USERNAME, "password": PASSWORD, "Command": "Login"}
    ).encode()
    req = urllib.request.Request(
        f"{WRITE_URL}/secureApp/LoginHandler.html", data=body, method="POST"
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with opener.open(req):
            pass
    except Exception as e:
        LOG(f"WARN login_sid: {e}")

    sid = ""
    for cookie in jar:
        if cookie.name == "JSESSIONID":
            sid = cookie.value
            break
    if not sid:
        return ""

    # Seleccionar rol Comercial para que las escrituras en ETCRM_Lead sean accesibles
    role_body = urllib.parse.urlencode(
        {"inpRole": _COMERCIAL_ROLE, "Command": "DEFAULT"}
    ).encode()
    role_req = urllib.request.Request(
        f"{WRITE_URL}/secureApp/StartupController", data=role_body, method="POST"
    )
    role_req.add_header("Content-Type", "application/x-www-form-urlencoded")
    role_req.add_header("Cookie", f"JSESSIONID={sid}")
    try:
        with opener.open(role_req):
            pass
    except Exception:
        pass  # Si falla el switch de rol, continuar con el SID original

    for cookie in jar:
        if cookie.name == "JSESSIONID":
            sid = cookie.value
    return sid


# ── CRM ────────────────────────────────────────────────────────────────────────
def get_all_leads():
    """Lee todos los leads via JWT Comercial — no depende del rol activo de Rocío."""
    from _etendo import etendo_login, etendo_fetch
    token = etendo_login(_COMERCIAL_ROLE)
    if not token:
        LOG("ERROR get_all_leads: JWT login falló")
        return {}
    leads = etendo_fetch(token, "ETCRM_Lead")
    return {l["id"]: l for l in leads if l.get("id")}


def update_lead(lead_id, payload, sid):
    url = f"{WRITE_URL}/org.openbravo.service.json.jsonrest/ETCRM_Lead/{lead_id}"
    req = urllib.request.Request(
        url, data=json.dumps({"data": payload}).encode(), method="PUT"
    )
    req.add_header("Cookie",       f"JSESSIONID={sid}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:300]
        return None, f"HTTP {e.code}: {err}"
    except Exception as e:
        return None, str(e)


# ── Classification ─────────────────────────────────────────────────────────────
def calc_classification(lead):
    """
    Determina la clasificación del lead según el funnel IQL → MQL → SQL.

    - IQL (Information Qualified Lead): top de funnel, sin señal de compra ni ICP fit claro.
    - MQL (Marketing Qualified Lead): encaja con ICP (empresa real + email corporativo
      o teléfono o keywords ERP), pero sin presupuesto/urgencia/decisor confirmado.
    - SQL (Sales Qualified Lead): señal explícita de compra — presupuesto, urgencia o
      decisor confirmado. Producto ERP.
    - Services SQL: igual que SQL pero para servicios (no implementado automáticamente aún).

    Devuelve ('SQL'|'MQL'|'IQL'|None, note).
    """
    fn      = (lead.get("firstname") or lead.get("firstName") or "").strip()
    email   = (lead.get("email") or "").strip().lower()
    phone   = (lead.get("phone") or "").strip()
    company = (lead.get("company") or "").strip()
    interest= (lead.get("interest") or "").strip()
    desc    = (lead.get("description") or "").strip()

    TEST_NAMES = {"unknown","test","prueba","devops","demo","testing","admin"}
    TEST_COMPANY_KEYWORDS = ["prueba","test","demo","testing","etendo test","devops"]

    is_test_lead = (
        fn.lower() in TEST_NAMES or
        any(k in company.lower() for k in TEST_COMPANY_KEYWORDS) or
        email.startswith(("test","prueba","devops"))
    )

    txt = (interest + " " + desc).lower()

    if is_test_lead or email.endswith("@etendo.software") or "busca trabajo" in txt:
        return None, "lead de prueba o no comercial"

    # Services SQL: señal explícita de servicios — migración o actualización
    SERVICES_KEYWORDS = ["migracion","migración","actualizacion","actualización",
                         "migrar","actualizar version","upgrade"]
    if any(k in txt for k in SERVICES_KEYWORDS):
        return "Services SQL", "señal de servicios (migración/actualización)"

    # SQL: señal explícita de compra de producto ERP
    SQL_KEYWORDS = ["demo","presupuesto","urgente","quiero avanzar","implementar ya",
                    "quiero contratar","necesito erp","cuanto cuesta"]
    if any(k in txt for k in SQL_KEYWORDS):
        areas = [a for a in ["erp","inventario","finanzas","manufactura","contabilidad",
                              "mrp","bi","verifactu","almacen"] if a in txt]
        note = "señal de compra" + (" | áreas: " + ", ".join(areas) if areas else "")
        return "SQL", note

    domain = email.split("@")[-1] if "@" in email else ""
    has_company = bool(company and company.lower() not in ("sin dato","sin empresa","test")
                       and not any(k in company.lower() for k in TEST_COMPANY_KEYWORDS))
    corp_email = bool(domain and domain not in GENERIC_DOMAINS
                      and not domain.endswith("etendo.software"))

    ERP_KEYWORDS = ["erp","inventario","finanzas","manufactura","contabilidad","mrp","bi",
                    "verifactu","almacen","gestion empresarial","facturacion","compras","ventas"]
    has_erp = any(k in txt for k in ERP_KEYWORDS)

    # MQL: encaja con ICP — empresa real + email corporativo, o teléfono, o keywords ERP
    if has_erp or (has_company and corp_email) or phone:
        areas = [a for a in ["erp","inventario","finanzas","manufactura","contabilidad",
                              "mrp","bi","verifactu","almacen"] if a in txt]
        note = "perfil MQL — ICP fit" + (" | áreas: " + ", ".join(areas) if areas else "")
        return "MQL", note

    # IQL: tiene empresa o email pero sin ICP fit claro — top de funnel
    if has_company or corp_email:
        return "IQL", "perfil IQL — sin ICP fit claro"

    return None, "sin datos suficientes"


# ── Email ──────────────────────────────────────────────────────────────────────
def send_report_email(report_text, today, updated, processed, hot, errors):
    subject = f"Clasificación Diaria CRM — {today} | {updated} actualizados | {len(hot)} hot"
    hot_html = "".join(f"<tr><td>{r.strip().lstrip('- ')}</td></tr>" for r in hot) \
               if hot else "<tr><td>Ninguno</td></tr>"
    body_html = f"""
<h2>Clasificación Diaria CRM — {today}</h2>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
  <tr><td><b>Leads procesados</b></td><td>{processed}</td></tr>
  <tr><td><b>Clasificados (SQL/IQL asignado)</b></td><td>{updated}</td></tr>
  <tr><td><b>Errores</b></td><td>{errors}</td></tr>
</table>
<h3>🔥 Hot leads (Qualified): {len(hot)}</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
  <tr><th>Lead</th></tr>{hot_html}
</table>
<pre style="background:#f5f5f5;padding:12px;font-size:12px">{report_text}</pre>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Lucía CRM Etendo <{GMAIL_USER}>"
    msg["To"]      = ", ".join(RECIPIENTS)
    msg.attach(MIMEText(report_text, "plain"))
    msg.attach(MIMEText(body_html,   "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.ehlo(); s.starttls(); s.login(GMAIL_USER, GMAIL_PASS)
        s.sendmail(GMAIL_USER, RECIPIENTS, msg.as_string())
    LOG(f"Reporte enviado a {', '.join(RECIPIENTS)}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    LOG("=== CRM Daily Classification ===")

    # READ: JWT Comercial — no depende del rol activo de Rocío (evita bloqueo por rol vacaciones)
    all_leads = get_all_leads()
    LOG(f"Total leads: {len(all_leads)}")

    # WRITE: JSESSIONID con rol Comercial seleccionado
    sid = login_sid()
    LOG(f"Auth escritura — SID: {'OK' if sid else 'FAIL'}")

    updated = no_change = errors = sid_uses = 0
    hot = []

    for lead in all_leads.values():
        status_id = (lead.get("leadStatus$_identifier") or "")
        lead_id   = lead["id"]

        # Saltar leads inactivos
        if status_id in ("Dead", "Converted"):
            continue

        name  = f"{lead.get('firstname') or lead.get('firstName') or ''} {lead.get('lastname') or lead.get('lastName') or ''}".strip() or (lead.get("_identifier") or "").replace(" - ", " ").strip()
        comp  = (lead.get("company") or "sin empresa").strip()
        em    = (lead.get("email") or "").strip().lower()

        # Hot lead = Qualified status
        if status_id == "Qualified" and not em.endswith("@smfconsulting.es"):
            hot.append(f"  - {name} | {comp} | Qualified")

        # Solo clasificar si aún no tiene classification asignada
        cur_class_id = (lead.get("classification") or "")
        if cur_class_id:
            no_change += 1
            continue

        classification, note = calc_classification(lead)
        if not classification:
            no_change += 1
            continue

        CLASS_ID_MAP = {"SQL": SQL_ID, "MQL": MQL_ID, "IQL": IQL_ID, "Services SQL": SERVICES_SQL_ID}
        target_id = CLASS_ID_MAP.get(classification, IQL_ID)
        payload = {"id": lead_id, "classification": target_id}
        result, err = update_lead(lead_id, payload, sid)
        sid_uses += 1
        if sid_uses % 80 == 0:
            sid = login_sid()
        if err:
            LOG(f"  ERROR {lead_id}: {err}")
            errors += 1
        elif (result or {}).get("response", {}).get("status") == 0:
            LOG(f"  ✓ {name} → {classification} ({note})")
            updated += 1
        else:
            errors += 1

    processed = len([l for l in all_leads.values()
                     if (l.get("leadStatus$_identifier") or "") not in ("Dead", "Converted")])
    today = datetime.now().strftime("%Y-%m-%d")

    report = f"""=== REPORTE DIARIO ===
Fecha: {today}
Leads procesados (excluye Dead/Converted): {processed}
Clasificados (SQL o IQL asignado hoy): {updated}
Sin cambios (ya clasificados o sin datos): {no_change}
Errores: {errors}
Hot leads (Qualified): {len(hot)}
{chr(10).join(hot) if hot else '  (ninguno)'}
=== FIN ==="""

    LOG(report)

    try:
        send_report_email(report, today, updated, processed, hot, errors)
    except Exception as e:
        LOG(f"WARN email: {e}")

    LOG("=== Done ===")


if __name__ == "__main__":
    main()
