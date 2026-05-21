#!/usr/bin/env python3
"""
CRM Monday Pulse — Etendo Revenue Org
Cada lunes envía email al equipo comercial con leads activos
pidiendo actualizaciones. Las respuestas se procesan con job_reply_checker.py

Env vars requeridas:
  ETENDO_BASE_URL / ETENDO_USERNAME / ETENDO_PASSWORD / ETENDO_ROLE_ID
  SMTP_USER / SMTP_PASS   (o GMAIL_USER / GMAIL_PASSWORD como fallback)
  CRM_PULSE_RECIPIENTS    (comma-separated, default: rocio.altamirano@smfconsulting.es)
"""

import base64
import email.utils
import json
import os
import smtplib
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────────
BASE_URL   = os.getenv("ETENDO_BASE_URL",  "https://futit-staff.etendo.cloud")
USERNAME   = os.getenv("ETENDO_USERNAME",  "Rocio Altamirano")
PASSWORD   = os.getenv("ETENDO_PASSWORD",  "tecnicia")
ROLE_ID    = os.getenv("ETENDO_ROLE_ID",   "8351131DFF384725AB08E06773FE6144")

SMTP_HOST  = os.getenv("SMTP_HOST",  "smtp.gmail.com")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER  = os.getenv("SMTP_USER",  os.getenv("GMAIL_USER",     "victoria.miguez@smfconsulting.es"))
SMTP_PASS  = os.getenv("SMTP_PASS",  os.getenv("GMAIL_PASSWORD", ""))

RECIPIENTS = [r.strip() for r in
              os.getenv("CRM_PULSE_RECIPIENTS", "rocio.altamirano@smfconsulting.es").split(",")
              if r.strip()]

SCRIPT_DIR  = Path(__file__).parent
REPO_ROOT   = SCRIPT_DIR.parent

# Avatar: try local dev path first, then repo-relative
_AVATAR_CANDIDATES = [
    REPO_ROOT.parent / "influencer" / "Stack fotos" /
        "Cinematic_portrait_photograph,_front-facing,_0°_202605181023.jpeg",
    REPO_ROOT / "assets" / "lucia_avatar.jpeg",
    Path(os.getenv("LUCIA_AVATAR_PATH", "/nonexistent")),
]
AVATAR_PATH = next((p for p in _AVATAR_CANDIDATES if p.exists()), None)

HOT_STATUSES = {
    "new", "in_progress", "meeting_scheduled", "meeting_pending",
    "proposal_sent", "follow_up", "negotiation", "hot",
}

STATUS_LABELS = {
    "new":               "Nuevo",
    "in_progress":       "En progreso",
    "meeting_scheduled": "Reunión agendada",
    "meeting_pending":   "Reunión pendiente",
    "proposal_sent":     "Propuesta enviada",
    "follow_up":         "Follow-up",
    "negotiation":       "Negociación",
    "hot":               "Hot",
}

STATUS_ORDER = [
    "negotiation", "proposal_sent", "meeting_scheduled",
    "meeting_pending", "in_progress", "follow_up", "hot", "new",
]

LOG = lambda msg: print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ── CRM ────────────────────────────────────────────────────────────────────────
def login_jwt():
    req = urllib.request.Request(
        f"{BASE_URL}/api/auth/login",
        data=json.dumps({"username": USERNAME, "password": PASSWORD, "role": ROLE_ID}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read()).get("token", "")


def get_hot_leads(jwt):
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

    hot = [l for l in all_leads
           if (l.get("leadStatus") or "").lower() in HOT_STATUSES]

    def sort_key(l):
        s = (l.get("leadStatus") or "").lower()
        order = STATUS_ORDER.index(s) if s in STATUS_ORDER else 99
        score = int(l.get("leadScore") or l.get("scoreIndex") or 0)
        return (order, -score)

    hot.sort(key=sort_key)
    return hot


# ── Email HTML ────────────────────────────────────────────────────────────────
def lead_name(lead):
    fn = (lead.get("firstName") or "").strip()
    ln = (lead.get("lastName") or "").strip()
    name = f"{fn} {ln}".strip()
    return name if name else "(sin nombre)"


def build_html(leads, today_str):
    rows_html = ""
    badge_colors = {
        "negotiation":       ("#166534", "#dcfce7"),
        "proposal_sent":     ("#1e40af", "#dbeafe"),
        "meeting_scheduled": ("#92400e", "#fef3c7"),
        "meeting_pending":   ("#92400e", "#fef9c3"),
        "in_progress":       ("#6b21a8", "#f3e8ff"),
        "follow_up":         ("#0e7490", "#cffafe"),
        "hot":               ("#991b1b", "#fee2e2"),
        "new":               ("#374151", "#f3f4f6"),
    }

    for i, lead in enumerate(leads):
        bg          = "#ffffff" if i % 2 == 0 else "#f8f9fb"
        status_raw  = (lead.get("leadStatus") or "").lower()
        label       = STATUS_LABELS.get(status_raw, status_raw or "—")
        spi         = lead.get("scorePurchaseIntention") or "—"
        company     = lead.get("company") or "—"
        name        = lead_name(lead)
        raw_summary = lead.get("summary") or lead.get("interest") or ""
        # Mostrar solo la última actualización (primera línea si hay timestamps [DD/MM/YYYY])
        first_line  = raw_summary.strip().splitlines()[0].strip() if raw_summary.strip() else ""
        summary     = first_line[:120] + ("…" if len(first_line) > 120 else "") if first_line else "—"
        txt_c, bg_c = badge_colors.get(status_raw, ("#374151", "#f3f4f6"))

        rows_html += f"""
        <tr style="background:{bg};">
          <td style="padding:10px 12px;font-size:14px;color:#111827;
                     border-bottom:1px solid #e5e7eb;">
            <strong>{name}</strong><br>
            <span style="font-size:12px;color:#6b7280;">{company}</span>
          </td>
          <td style="padding:10px 12px;text-align:center;
                     border-bottom:1px solid #e5e7eb;">
            <span style="background:{bg_c};color:{txt_c};padding:3px 8px;
                         border-radius:12px;font-size:12px;font-weight:600;">
              {label}</span>
          </td>
          <td style="padding:10px 12px;font-size:13px;text-align:center;
                     border-bottom:1px solid #e5e7eb;color:#374151;">
            SPI {spi}</td>
          <td style="padding:10px 12px;font-size:12px;color:#6b7280;
                     border-bottom:1px solid #e5e7eb;">
            {summary or "—"}</td>
        </tr>"""

    count = len(leads)
    avatar_src = "cid:lucia_avatar" if AVATAR_PATH else \
        "https://ui-avatars.com/api/?name=Lucia&background=0e6df6&color=fff&size=72"

    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>CRM Pulse — {today_str}</title></head>
<body style="margin:0;padding:0;background:#f1f5f9;
             font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"
       style="background:#f1f5f9;padding:24px 0;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0"
       style="background:#fff;border-radius:12px;overflow:hidden;
              box-shadow:0 2px 12px rgba(0,0,0,.08);">

  <tr><td style="background:linear-gradient(135deg,#1e3a5f 0%,#0e6df6 100%);
                 padding:28px 32px;">
    <table cellpadding="0" cellspacing="0" width="100%"><tr>
      <td width="80" valign="middle" style="padding-right:20px;">
        <img src="{avatar_src}" width="72" height="72"
             style="border-radius:50%;border:3px solid rgba(255,255,255,.4);
                    object-fit:cover;display:block;" alt="Lucía">
      </td>
      <td valign="middle">
        <div style="color:#fff;font-size:11px;letter-spacing:1px;
                    text-transform:uppercase;opacity:.8;margin-bottom:4px;">
          Etendo CRM · Pulso Semanal</div>
        <div style="color:#fff;font-size:22px;font-weight:700;line-height:1.2;">
          ¡Hola, soy Lucía!</div>
        <div style="color:rgba(255,255,255,.85);font-size:14px;margin-top:4px;">
          Quien maneja el CRM de Etendo 📋</div>
      </td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:24px 32px 16px;">
    <p style="margin:0;font-size:15px;color:#374151;line-height:1.6;">
      Esta semana tenemos <strong>{count} leads activos</strong> que necesitan atención.
      ¿Tuviste algún contacto o avance con alguno de estos?
      <strong>Respondé este email</strong> con texto libre y yo me encargo de actualizar
      el CRM. 🤖
    </p>
  </td></tr>

  <tr><td style="padding:0 32px 24px;">
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;">
      <thead>
        <tr style="background:#f8f9fb;">
          <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;
                     text-transform:uppercase;letter-spacing:.5px;
                     border-bottom:2px solid #e5e7eb;">Lead</th>
          <th style="padding:10px 12px;text-align:center;font-size:12px;color:#6b7280;
                     text-transform:uppercase;letter-spacing:.5px;
                     border-bottom:2px solid #e5e7eb;">Estado</th>
          <th style="padding:10px 12px;text-align:center;font-size:12px;color:#6b7280;
                     text-transform:uppercase;letter-spacing:.5px;
                     border-bottom:2px solid #e5e7eb;">SPI</th>
          <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;
                     text-transform:uppercase;letter-spacing:.5px;
                     border-bottom:2px solid #e5e7eb;">Notas</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </td></tr>

  <tr><td style="padding:0 32px 24px;">
    <div style="background:#eff6ff;border-left:4px solid #0e6df6;
                border-radius:0 8px 8px 0;padding:16px 20px;">
      <p style="margin:0 0 8px;font-size:14px;font-weight:700;color:#1e3a5f;">
        ¿Cómo actualizar?</p>
      <p style="margin:0;font-size:13px;color:#374151;line-height:1.7;">
        <strong>Respondé este email</strong> con texto libre. Algunos ejemplos:<br>
        <span style="color:#6b7280;font-style:italic;">
          "Tuve demo con Valiente ayer, quedó muy interesado. Mando propuesta esta semana."<br>
          "Martí no contestó — lo paso a cold."<br>
          "Pelliza firmó el contrato. ¡Won!"
        </span><br><br>
        Con el nombre o empresa del lead es suficiente. Yo me encargo del resto. 💪
      </p>
    </div>
  </td></tr>

  <tr><td style="background:#f8f9fb;padding:16px 32px;border-top:1px solid #e5e7eb;">
    <p style="margin:0;font-size:12px;color:#9ca3af;text-align:center;">
      CRM Pulse automático · {today_str} · Etendo Revenue Org<br>
      Este email se genera cada lunes a las 9am.
    </p>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""


def send_pulse(leads, today_str):
    subject = f"[CRM Pulse] {today_str} — {len(leads)} leads activos"

    msg = MIMEMultipart("mixed")
    msg["Subject"]    = subject
    msg["From"]       = email.utils.formataddr(("Lucía | CRM Etendo", SMTP_USER))
    msg["To"]         = ", ".join(RECIPIENTS)
    msg["Date"]       = email.utils.formatdate(localtime=True)

    domain = SMTP_USER.split("@")[-1] if "@" in SMTP_USER else "etendo.software"
    msg_id = f"<crm-pulse-{datetime.now().strftime('%Y%m%d%H%M%S')}@{domain}>"
    msg["Message-ID"] = msg_id

    related = MIMEMultipart("related")
    related.attach(MIMEText(build_html(leads, today_str), "html", "utf-8"))

    if AVATAR_PATH:
        with open(AVATAR_PATH, "rb") as f:
            img = MIMEImage(f.read(), "jpeg")
        img.add_header("Content-ID", "<lucia_avatar>")
        img.add_header("Content-Disposition", "inline", filename="lucia.jpg")
        related.attach(img)
        LOG("Avatar adjuntada como CID inline")
    else:
        LOG("WARN: avatar no encontrada — email sin imagen inline")

    msg.attach(related)

    LOG(f"Enviando a: {RECIPIENTS} via {SMTP_USER}")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
        srv.starttls()
        srv.login(SMTP_USER, SMTP_PASS)
        srv.sendmail(SMTP_USER, RECIPIENTS, msg.as_bytes())

    LOG(f"OK — Message-ID: {msg_id}")
    return msg_id, subject


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    today_str = datetime.now().strftime("%d/%m/%Y")
    LOG("=== CRM Monday Pulse ===")

    jwt = login_jwt()
    if not jwt:
        LOG("ERROR: JWT login fallido"); return

    LOG("Cargando leads activos del CRM…")
    leads = get_hot_leads(jwt)
    LOG(f"  {len(leads)} leads en estado activo")

    if not leads:
        LOG("Sin leads activos — no se envía email."); return

    send_pulse(leads, today_str)
    LOG(f"Done. {len(leads)} leads, {len(RECIPIENTS)} destinatario(s).")


if __name__ == "__main__":
    main()
