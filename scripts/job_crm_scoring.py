#!/usr/bin/env python3
"""
CRM Daily Scoring — Etendo Revenue Org
Calcula y actualiza scores de todos los leads.
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
BASE_URL      = os.getenv("ETENDO_BASE_URL",   "https://futit-staff.etendo.cloud")
WRITE_URL     = os.getenv("ETENDO_WRITE_URL",  "https://staff-ui.etendo.cloud/etendo")
USERNAME      = os.getenv("ETENDO_USERNAME",   "Rocio Altamirano")
PASSWORD      = os.getenv("ETENDO_PASSWORD",   "tecnicia")
ROLE_ID       = os.getenv("ETENDO_ROLE_ID",    "8351131DFF384725AB08E06773FE6144")

GMAIL_USER    = os.getenv("GMAIL_USER",        "victoria.miguez@smfconsulting.es")
GMAIL_PASS    = os.getenv("GMAIL_PASSWORD",    "oyuuywtiypdrinhe")
RECIPIENTS    = ["rocio.altamirano@smfconsulting.es", "victoria.miguez@smfconsulting.es"]

GENERIC_DOMAINS = {"gmail.com","hotmail.com","yahoo.com","outlook.com","hotmail.es","yahoo.es","gmail.es"}

LOG = lambda msg: print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ── Auth ───────────────────────────────────────────────────────────────────────
def login_jwt():
    req = urllib.request.Request(
        f"{BASE_URL}/api/auth/login",
        data=json.dumps({"username": USERNAME, "password": PASSWORD, "role": ROLE_ID}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read()).get("token", "")


def login_sid():
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
        with opener.open(req) as r:
            pass
    except Exception as e:
        LOG(f"WARN login_sid: {e}")
    for cookie in jar:
        if cookie.name == "JSESSIONID":
            return cookie.value
    return ""


# ── CRM ────────────────────────────────────────────────────────────────────────
def get_all_leads(jwt):
    all_leads = {}
    start = 0
    while True:
        params = urllib.parse.urlencode({
            "_startRow": start, "_endRow": start + 100, "_orderBy": "creationDate desc"
        })
        req = urllib.request.Request(f"{BASE_URL}/api/datasource/ECLM_Lead?{params}")
        req.add_header("Authorization", f"Bearer {jwt}")
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
        page = data.get("response", {}).get("data", [])
        new = sum(1 for l in page if l.get("id") and l["id"] not in all_leads)
        for l in page:
            if l.get("id"):
                all_leads[l["id"]] = l
        if new == 0:
            break
        start += 100
    return all_leads


def update_lead(lead_id, payload, sid):
    url = f"{WRITE_URL}/org.openbravo.service.json.jsonrest/ECLM_Lead/{lead_id}"
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


# ── Scoring ────────────────────────────────────────────────────────────────────
def calc_scores(lead):
    si = 0
    si_parts = []

    fn      = (lead.get("firstName") or "").strip()
    ln      = (lead.get("lastName") or "").strip()
    email   = (lead.get("email") or "").strip().lower()
    phone   = (lead.get("phone") or "").strip()
    company = (lead.get("company") or "").strip()
    country = (lead.get("country") or lead.get("country$_identifier") or "").strip()
    industry= (lead.get("industry") or lead.get("industry$_identifier") or "").strip()
    cu      = lead.get("concurrentUsers") or 0
    budget  = (lead.get("budget") or "").strip()
    sol     = (lead.get("currentSolution") or "").strip()
    interest= (lead.get("interest") or "").strip()
    summary = (lead.get("summary") or "").strip()

    TEST_NAMES     = {"unknown","test","prueba","devops","demo","testing","admin"}
    TEST_COMPANY_KEYWORDS = ["prueba","test","demo","testing","etendo test","devops"]

    is_test_lead = (
        fn.lower() in TEST_NAMES or
        any(k in company.lower() for k in TEST_COMPANY_KEYWORDS) or
        email.startswith("test") or email.startswith("prueba") or email.startswith("devops")
    )

    if fn and fn.lower() not in TEST_NAMES:                            si += 5;  si_parts.append("firstName+5")
    if ln:                                                             si += 5;  si_parts.append("lastName+5")
    domain = email.split("@")[-1] if "@" in email else ""
    if domain and domain not in GENERIC_DOMAINS and not domain.endswith("etendo.software"):
        si += 15; si_parts.append("emailCorp+15")
    elif domain in GENERIC_DOMAINS:
        si += 5;  si_parts.append("emailGeneric+5")
    if phone:                                                          si += 10; si_parts.append("phone+10")
    if company and company.lower() not in ("sin dato","sin empresa","test") and \
       not any(k in company.lower() for k in TEST_COMPANY_KEYWORDS):
        si += 10; si_parts.append("company+10")
    if country:                                                        si += 5;  si_parts.append("country+5")
    if industry:                                                       si += 10; si_parts.append("industry+10")
    if cu and int(cu) > 0:                                             si += 10; si_parts.append("concUsers+10")
    if budget:                                                         si += 15; si_parts.append("budget+15")
    if sol:                                                            si += 10; si_parts.append("currSol+10")
    if interest:                                                       si += 10; si_parts.append("interest+10")
    if len(summary) > 80:                                              si += 5;  si_parts.append("summary+5")

    txt = (interest + " " + summary).lower()
    if is_test_lead or email.endswith("@etendo.software") or "busca trabajo" in txt:
        spi = -1
    elif any(k in txt for k in ["demo","presupuesto","urgente","quiero avanzar","implementar ya"]):
        spi = 3
    elif any(k in txt for k in ["verifactu","almacen","inventario","manufactura","erp","mrp","contabilidad","finanzas","facturacion","bi "]):
        spi = 2
    elif any(k in txt for k in ["informaci","conocer","explorar","opciones","interesado"]):
        spi = 1
    elif fn or phone or company:
        spi = 1
    else:
        spi = 0

    score = si * spi

    is_junk     = is_test_lead or (not fn and not company and not phone and domain in GENERIC_DOMAINS)
    status      = (lead.get("leadStatus") or "").lower()
    has_company = bool(company and company.lower() not in ("sin dato", "sin empresa", "test")
                       and not any(k in company.lower() for k in TEST_COMPANY_KEYWORDS))
    is_engaged  = status in ("in_progress","meeting_scheduled","meeting_pending","new") and has_company

    ERP_KEYWORDS = ["erp","inventario","finanzas","manufactura","contabilidad","mrp","bi",
                    "verifactu","almacen","gestion empresarial","facturacion","compras","ventas"]
    has_erp = any(k in txt for k in ERP_KEYWORDS)

    if spi == -1 or is_junk:
        fit = "strategic_fit_no"
    elif spi >= 2 or has_erp or is_engaged or (has_company and domain not in GENERIC_DOMAINS):
        fit = "strategic_fit_yes"
    else:
        fit = "strategic_fit_no"

    areas = [a for a in ["erp","inventario","finanzas","manufactura","contabilidad","mrp","bi","verifactu","almacen"] if a in txt]
    desc  = (" | áreas: " + ", ".join(areas) if areas else "") + \
            f" | idx:{si}pts ({', '.join(si_parts)})" if si_parts else f"idx:{si}pts"
    return si, spi, score, fit, desc.lstrip(" | ")


# ── Email ──────────────────────────────────────────────────────────────────────
def send_report_email(report_text, today, updated, lost_updated, processed, hot, errors):
    subject = f"Scoring Diario CRM — {today} | {updated} actualizados | {len(hot)} calientes"
    hot_html = "".join(f"<tr><td>{r.strip().lstrip('- ')}</td></tr>" for r in hot) \
               if hot else "<tr><td>Ninguno</td></tr>"
    body_html = f"""
<h2>Scoring Diario CRM — {today}</h2>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
  <tr><td><b>Leads procesados</b></td><td>{processed}</td></tr>
  <tr><td><b>Actualizados</b></td><td>{updated}</td></tr>
  <tr><td><b>Lost/Won descritos</b></td><td>{lost_updated}</td></tr>
  <tr><td><b>Errores</b></td><td>{errors}</td></tr>
</table>
<h3>🔥 Señales calientes (SPI≥2): {len(hot)}</h3>
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
    LOG("=== CRM Daily Scoring ===")
    jwt = login_jwt()
    sid = login_sid()
    LOG(f"Auth — JWT: {'OK' if jwt else 'FAIL'} | SID: {'OK' if sid else 'FAIL'}")

    all_leads = get_all_leads(jwt)
    LOG(f"Total leads: {len(all_leads)}")

    updated = lost_updated = no_change = errors = sid_uses = 0
    hot, discard = [], []

    for lead in all_leads.values():
        status  = lead.get("leadStatus") or ""
        lead_id = lead["id"]

        if status in ("lost_deal", "won_deal"):
            fn_l  = (lead.get("firstName") or "").strip()
            co_l  = (lead.get("company") or "").strip()
            ph_l  = (lead.get("phone") or "").strip()
            em_l  = (lead.get("email") or "").strip().lower()
            dm_l  = em_l.split("@")[-1] if "@" in em_l else ""
            is_junk = not fn_l and not co_l and not ph_l and dm_l in GENERIC_DOMAINS
            msg_l = "Lead sin analizar score por ser lost_deal"
            cur_fit = lead.get("strategicFit") or ""
            new_fit = "strategic_fit_no" if is_junk else cur_fit
            if (lead.get("scoreDescription") or "") != msg_l or new_fit != cur_fit:
                payload = {"id": lead_id, "scoreDescription": msg_l}
                if new_fit and new_fit != cur_fit:
                    payload["strategicFit"] = new_fit
                result, err = update_lead(lead_id, payload, sid)
                sid_uses += 1
                if sid_uses % 80 == 0:
                    sid = login_sid()
                if err:
                    LOG(f"  ERROR {lead_id}: {err}")
                    errors += 1
                elif (result or {}).get("response", {}).get("status") == 0:
                    lost_updated += 1
                else:
                    errors += 1
            continue

        si, spi, score, fit, desc = calc_scores(lead)
        try:
            cur_si    = int(lead.get("scoreIndex") or 0)
            cur_spi   = lead.get("scorePurchaseIntention")
            cur_score = int(lead.get("score") or 0)
            cur_fit   = lead.get("strategicFit") or ""
        except Exception:
            cur_si = cur_spi = cur_score = 0; cur_fit = ""

        name  = f"{lead.get('firstName') or ''} {lead.get('lastName') or ''}".strip()
        comp  = (lead.get("company") or "sin empresa").strip()
        em    = (lead.get("email") or "").strip().lower()

        if spi >= 2 and not em.endswith("@smfconsulting.es"):
            hot.append((name, em, comp, spi, score))
        if spi == -1:
            reason = (lead.get("interest") or lead.get("summary") or em or "sin motivo")[:60]
            discard.append(f"  - {name} | {comp} | {reason}")

        if cur_si == si and str(cur_spi) == str(spi) and cur_score == score and cur_fit == fit:
            no_change += 1
            continue

        payload = {"id": lead_id, "scoreIndex": si, "scorePurchaseIntention": spi,
                   "score": score, "strategicFit": fit, "scoreDescription": desc}
        result, err = update_lead(lead_id, payload, sid)
        sid_uses += 1
        if sid_uses % 80 == 0:
            sid = login_sid()
        if err:
            LOG(f"  ERROR {lead_id}: {err}")
            errors += 1
        elif (result or {}).get("response", {}).get("status") == 0:
            updated += 1
        else:
            errors += 1

    # Deduplicar hot
    seen_n, seen_e, hot_deduped = set(), set(), []
    for (name, em, comp, spi, score) in hot:
        k_n, k_e = name.lower().strip(), em.lower().strip()
        if k_n in seen_n or (k_e and k_e in seen_e):
            continue
        seen_n.add(k_n); seen_e.add(k_e) if k_e else None
        hot_deduped.append(f"  - {name} | {comp} | SPI={spi} | Score={score}")
    hot = hot_deduped

    processed = len([l for l in all_leads.values()
                     if (l.get("leadStatus") or "") not in ("lost_deal","won_deal")])
    today = datetime.now().strftime("%Y-%m-%d")

    report = f"""=== REPORTE DIARIO ===
Fecha: {today}
Leads procesados: {processed}
Actualizados: {updated}
Lost/won descritos: {lost_updated}
Sin cambios: {no_change}
Errores: {errors}
Señales calientes (SPI>=2): {len(hot)}
{chr(10).join(hot) if hot else '  (ninguna)'}
Candidatos a descartar (SPI=-1): {len(discard)}
{chr(10).join(discard) if discard else '  (ninguno)'}
=== FIN ==="""

    LOG(report)

    try:
        send_report_email(report, today, updated, lost_updated, processed, hot, errors)
    except Exception as e:
        LOG(f"WARN email: {e}")

    LOG("=== Done ===")


if __name__ == "__main__":
    main()
