#!/usr/bin/env python3
"""
CRM Reply Checker — Etendo Revenue Org
Lee respuestas al CRM Pulse, parsea con Claude y actualiza el CRM.

Corre diariamente (o a demanda). Busca vía IMAP emails con
"Re: [CRM Pulse]" en el asunto que estén sin leer.
Obtiene los leads actuales del CRM para contexto de parsing.

Env vars requeridas:
  IMAP_USER / IMAP_PASS   (o GMAIL_USER / GMAIL_PASSWORD como fallback)
  ETENDO_BASE_URL / ETENDO_USERNAME / ETENDO_PASSWORD / ETENDO_ROLE_ID
  ETENDO_WRITE_URL
  ANTHROPIC_API_KEY
  SMTP_USER / SMTP_PASS   (para emails de confirmación)
"""

import email as email_lib
import email.header
import email.utils
import http.cookiejar
import imaplib
import json
import os
import re
import smtplib
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────────
BASE_URL   = os.getenv("ETENDO_BASE_URL",  "https://futit-staff.etendo.cloud")
WRITE_URL  = os.getenv("ETENDO_WRITE_URL", "https://staff-ui.etendo.cloud/etendo")
USERNAME   = os.getenv("ETENDO_USERNAME",  "Rocio Altamirano")
PASSWORD   = os.getenv("ETENDO_PASSWORD",  "tecnicia")
ROLE_ID    = os.getenv("ETENDO_ROLE_ID",   "8351131DFF384725AB08E06773FE6144")

IMAP_HOST  = os.getenv("IMAP_HOST",  "imap.gmail.com")
IMAP_PORT  = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER  = os.getenv("IMAP_USER",  os.getenv("GMAIL_USER",     "victoria.miguez@smfconsulting.es"))
IMAP_PASS  = os.getenv("IMAP_PASS",  os.getenv("GMAIL_PASSWORD", ""))

SMTP_HOST  = os.getenv("SMTP_HOST",  "smtp.gmail.com")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER  = os.getenv("SMTP_USER",  os.getenv("GMAIL_USER",     "victoria.miguez@smfconsulting.es"))
SMTP_PASS  = os.getenv("SMTP_PASS",  os.getenv("GMAIL_PASSWORD", ""))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

HOT_STATUSES = {
    "new", "in_progress", "meeting_scheduled", "meeting_pending",
    "proposal_sent", "follow_up", "negotiation", "hot",
    "cold_archived", "lost_deal", "won_deal",  # include all for matching
}

LOG = lambda msg: print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ── CRM Auth ──────────────────────────────────────────────────────────────────
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


def get_all_leads(jwt):
    all_leads, seen, start = [], set(), 0
    while True:
        params = urllib.parse.urlencode(
            {"_startRow": start, "_endRow": start + 100, "_orderBy": "creationDate desc"}
        )
        req = urllib.request.Request(f"{BASE_URL}/api/datasource/ECLM_Lead?{params}")
        req.add_header("Authorization", f"Bearer {jwt}")
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
        page = data.get("response", {}).get("data", [])
        if not page:
            break
        added = 0
        for lead in page:
            lid = lead.get("id")
            if lid and lid not in seen:
                seen.add(lid)
                all_leads.append(lead)
                added += 1
        if added == 0:
            break
        start += 100
    return all_leads


def get_single_lead(jwt, lead_id):
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/datasource/ECLM_Lead/{lead_id}")
        req.add_header("Authorization", f"Bearer {jwt}")
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
        return data.get("response", {}).get("data", [None])[0]
    except Exception:
        return None


def update_lead(lead_id, payload, sid):
    url = f"{WRITE_URL}/org.openbravo.service.json.jsonrest/ECLM_Lead/{lead_id}"
    req = urllib.request.Request(
        url, data=json.dumps({"data": payload}).encode(), method="PUT"
    )
    req.add_header("Cookie", f"JSESSIONID={sid}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:300]
        return None, f"HTTP {e.code}: {err}"
    except Exception as e:
        return None, str(e)


# ── OpenAI parsing ───────────────────────────────────────────────────────────
def parse_reply(reply_text, leads_snapshot):
    if not OPENAI_API_KEY:
        LOG("WARN: OPENAI_API_KEY no disponible — usando parsing básico")
        return _parse_basic(reply_text, leads_snapshot)

    leads_list = "\n".join(
        f"- {l['name']} | {l['company']} | ID: {l['id']} | estado: {l['status']}"
        for l in leads_snapshot
    )

    prompt = f"""Sos asistente de CRM de Etendo. Un vendedor respondió el email semanal de CRM Pulse.

Leads activos en el CRM:
{leads_list}

Texto de la respuesta del vendedor:
---
{reply_text[:3000]}
---

Identificá todos los leads mencionados y el tipo de actualización para cada uno.
Para cada lead mencionado, devolvé un JSON object con estos campos exactos:
- "lead_id": ID del lead de la lista (string, o null si no lo podés identificar)
- "lead_name": nombre del lead identificado (string)
- "company": empresa del lead (string)
- "new_status": nuevo estado en inglés o null si no cambia. Valores válidos:
  new, in_progress, meeting_scheduled, meeting_pending, proposal_sent,
  negotiation, won_deal, lost_deal, cold_archived, follow_up
- "note": descripción breve de la actualización, máx 200 caracteres (string)
- "confidence": "alta", "media" o "baja" según qué tan seguro estás de la identificación

Reglas de mapeo de estado:
- "demo realizada / tuve demo / hice demo" → meeting_scheduled
- "propuesta enviada / mandé presupuesto" → proposal_sent
- "reunión agendada / le di un turno" → meeting_scheduled
- "negociando / en negociación" → negotiation
- "firmó / ganado / won / cerrado positivo" → won_deal
- "no responde / perdido / descartado / cold / archivado" → lost_deal o cold_archived
- "llamé / escribí / contacté sin respuesta" → follow_up

Devolvé SOLO un JSON array válido, sin texto adicional ni explicaciones.
Si no hay leads identificables, devolvé [].
"""

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps({
            "model":       "gpt-4o-mini",
            "max_tokens":  1024,
            "temperature": 0,
            "messages":    [{"role": "user", "content": prompt}],
        }).encode(),
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {OPENAI_API_KEY}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read())
        raw = resp["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        LOG(f"ERROR OpenAI: {e} — fallback a parsing básico")
        return _parse_basic(reply_text, leads_snapshot)


def _parse_basic(reply_text, leads_snapshot):
    """Fallback parser: match by name/company, detect action keywords."""
    STATUS_KEYWORDS = {
        "demo":         "meeting_scheduled",
        "demostración": "meeting_scheduled",
        "reunión":      "meeting_scheduled",
        "propuesta":    "proposal_sent",
        "presupuesto":  "proposal_sent",
        "negociación":  "negotiation",
        "won":          "won_deal",
        "ganado":       "won_deal",
        "firmó":        "won_deal",
        "firmo":        "won_deal",
        "perdido":      "lost_deal",
        "lost":         "lost_deal",
        "cold":         "cold_archived",
        "descartado":   "lost_deal",
    }
    reply_lower = reply_text.lower()
    updates = []

    for lead in leads_snapshot:
        name_lower    = lead["name"].lower()
        company_lower = (lead["company"] or "").lower()
        name_match    = len(name_lower) > 3 and bool(re.search(r'\b' + re.escape(name_lower) + r'\b', reply_lower))
        company_match = len(company_lower) > 4 and bool(re.search(r'\b' + re.escape(company_lower) + r'\b', reply_lower))
        if name_match or company_match:
            new_status = next(
                (v for k, v in STATUS_KEYWORDS.items() if k in reply_lower), None
            )
            updates.append({
                "lead_id":    lead["id"],
                "lead_name":  lead["name"],
                "company":    lead["company"],
                "new_status": new_status,
                "note":       reply_text[:200],
                "confidence": "baja",
            })

    return updates


# ── IMAP ──────────────────────────────────────────────────────────────────────
def extract_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                cs = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(cs, errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                cs = part.get_content_charset() or "utf-8"
                html = part.get_payload(decode=True).decode(cs, errors="replace")
                return re.sub(r"<[^>]+>", " ", html)
    else:
        cs = msg.get_content_charset() or "utf-8"
        return msg.get_payload(decode=True).decode(cs, errors="replace")
    return ""


def strip_quoted(text):
    """Remove quoted reply content, keeping only the top new reply text."""
    # Cut inline Gmail attribution that appears on the same line as the reply
    # e.g. "...semana. El jue, 21 may 2026 a las 12:09, Lucía escribió:"
    text = re.sub(
        r'\s+(El\s+\w+[,.]?\s+\d{1,2}\s+\w+\s+\d{4}|On\s+\w+[,.]?\s+\w+\s+\d{1,2}[,.]?\s+\d{4}).*',
        '', text, flags=re.IGNORECASE | re.DOTALL
    )

    QUOTE_STARTS = (">", "On ", "De:", "From:", "Enviado:", "Sent:", "Para:", "To:", "Asunto:", "Subject:")
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            break
        if any(stripped.startswith(p) for p in QUOTE_STARTS[1:]):
            break
        if stripped.lower().startswith("el ") and "escribió:" in stripped.lower():
            break
        if re.match(r"^[-_]{3,}$", stripped):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def fetch_crm_pulse_replies():
    """Fetch unread emails replying to [CRM Pulse] from last 14 days."""
    replies = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USER, IMAP_PASS)
        mail.select("INBOX")
    except Exception as e:
        LOG(f"ERROR IMAP: {e}")
        return replies, None

    # Search all CRM Pulse emails not yet processed (UNANSWERED flag tracks processed state)
    status, data = mail.search(None, '(SUBJECT "CRM Pulse" UNANSWERED)')
    if status != "OK":
        mail.logout()
        return replies, None

    msg_nums = data[0].split()
    LOG(f"  {len(msg_nums)} emails CRM Pulse sin procesar")

    for num in msg_nums:
        status, msg_data = mail.fetch(num, "(RFC822)")
        if status != "OK":
            continue

        msg     = email_lib.message_from_bytes(msg_data[0][1])
        subject = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
        sender  = email.utils.parseaddr(msg.get("From", ""))[1]

        # Mark non-replies as answered so they don't show up next run
        if "Re:" not in subject and "RE:" not in subject:
            mail.store(num, "+FLAGS", "\\Answered")
            LOG(f"  Skipping non-reply: {subject[:60]}")
            continue

        # Skip our own confirmation emails (sent back to the rep after processing)
        if "Actualización recibida" in subject or "actualizacion recibida" in subject.lower():
            mail.store(num, "+FLAGS", "\\Answered")
            LOG(f"  Skipping confirmation email: {subject[:60]}")
            continue

        body = strip_quoted(extract_text(msg))
        if not body or len(body.strip()) < 5:
            mail.store(num, "+FLAGS", "\\Answered")
            LOG(f"  Skipping empty reply from {sender}")
            continue

        LOG(f"  Respuesta de {sender}: {subject[:60]}")
        replies.append({
            "imap_num": num,
            "sender":   sender,
            "subject":  subject,
            "body":     body,
        })

    return replies, mail


def mark_processed(mail, imap_num):
    try:
        mail.store(imap_num, "+FLAGS", "\\Answered")
    except Exception as e:
        LOG(f"WARN mark_processed: {e}")


# ── Confirmation email ────────────────────────────────────────────────────────
def send_confirmation(to_addr, updates):
    if not updates:
        return
    items_html = ""
    for u in updates:
        icon  = "✅" if u.get("write_ok") else "⏳"
        name  = u.get("lead_name", "—")
        co    = u.get("company", "")
        st    = u.get("new_status") or "(sin cambio de estado)"
        note  = u.get("note", "")
        items_html += (
            f"<li style='margin-bottom:8px;'>{icon} <strong>{name}</strong>"
            f"{f' ({co})' if co else ''} → {st}<br>"
            f"<span style='color:#6b7280;font-size:12px;'>{note}</span></li>"
        )

    html = f"""<html><body style='font-family:Arial,sans-serif;padding:20px;'>
<p style='font-size:15px;'>Hola, recibí tu actualización del CRM 👋</p>
<p>Esto es lo que procesé:</p>
<ul style='line-height:1.8;'>{items_html}</ul>
<p style='font-size:12px;color:#9ca3af;margin-top:24px;'>
  Lucía · CRM Etendo · Actualización automática</p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Re: [CRM Pulse] — Actualización recibida ✅"
    msg["From"]    = email.utils.formataddr(("Lucía | CRM Etendo", SMTP_USER))
    msg["To"]      = to_addr
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(SMTP_USER, [to_addr], msg.as_bytes())
        LOG(f"  Confirmación enviada a {to_addr}")
    except Exception as e:
        LOG(f"  WARN confirmación: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    LOG("=== CRM Reply Checker ===")

    # Fetch live leads from CRM for context
    LOG("Login JWT…")
    jwt = login_jwt()
    if not jwt:
        LOG("ERROR: JWT login fallido"); return

    LOG("Cargando leads del CRM para contexto de parsing…")
    all_leads = get_all_leads(jwt)
    leads_by_id = {l["id"]: l for l in all_leads if l.get("id")}
    leads_snapshot = [
        {
            "id":      l.get("id", ""),
            "name":    (
                f"{(l.get('firstName') or '').strip()} "
                f"{(l.get('lastName') or '').strip()}"
            ).strip() or "(sin nombre)",
            "company": l.get("company") or "",
            "status":  l.get("leadStatus") or "",
            "summary": l.get("summary") or "",
        }
        for l in all_leads
        if l.get("id")
    ]
    LOG(f"  {len(leads_snapshot)} leads cargados")

    # Fetch IMAP replies
    LOG("Buscando respuestas en IMAP…")
    replies, imap_conn = fetch_crm_pulse_replies()

    if not replies:
        LOG("No hay respuestas nuevas.")
        if imap_conn:
            imap_conn.logout()
        return

    LOG(f"Procesando {len(replies)} respuesta(s)…")

    # CRM write session
    sid = login_sid()
    if not sid:
        LOG("WARN: no se obtuvo JSESSIONID — updates serán solo log")

    for reply in replies:
        sender = reply["sender"]
        body   = reply["body"]

        LOG(f"  Parseando respuesta de {sender}…")
        updates = parse_reply(body, leads_snapshot)

        if not updates:
            LOG(f"  Sin leads identificados en la respuesta.")
            mark_read_and_label(reply["imap_num"])
            continue

        LOG(f"  {len(updates)} update(s) identificados")

        for upd in updates:
            lead_id    = upd.get("lead_id")
            lead_name  = upd.get("lead_name", "?")
            new_status = upd.get("new_status")
            note       = upd.get("note", "")
            confidence = upd.get("confidence", "?")

            LOG(f"    [{confidence}] {lead_name} → {new_status or '(sin cambio)'}: {note[:80]}")

            if not lead_id:
                LOG(f"    SKIP: lead_id no identificado")
                upd["write_ok"] = False
                continue

            # Build payload
            payload = {"id": lead_id}
            if new_status:
                payload["leadStatus"] = new_status

            # Prepend note to existing summary (use already-loaded lead data)
            if note:
                existing = leads_by_id.get(lead_id, {})
                old = (existing.get("summary") or "").strip()
                ts  = datetime.now().strftime("%d/%m/%Y")
                payload["summary"] = (
                    f"[{ts} — {sender}] {note}\n{old}" if old
                    else f"[{ts} — {sender}] {note}"
                )[:2000]

            # Attempt CRM write
            if sid:
                result, err = update_lead(lead_id, payload, sid)
                if err:
                    LOG(f"    WARN CRM write: {err}")
                    upd["write_ok"] = False
                else:
                    LOG(f"    ✓ CRM actualizado: {lead_name}")
                    upd["write_ok"] = True
            else:
                LOG(f"    SKIP write: sin JSESSIONID")
                upd["write_ok"] = False

        send_confirmation(sender, updates)
        mark_processed(imap_conn, reply["imap_num"])

    if imap_conn:
        imap_conn.logout()
    LOG("Done.")


if __name__ == "__main__":
    main()
