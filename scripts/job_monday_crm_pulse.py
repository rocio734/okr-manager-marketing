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
    REPO_ROOT / "assets" / "lucia_avatar_animated.gif",
    REPO_ROOT / "assets" / "lucia_avatar.jpeg",
    REPO_ROOT.parent / "influencer" / "Stack fotos" /
        "Cinematic_portrait_photograph,_front-facing,_0°_202605181023.jpeg",
    Path(os.getenv("LUCIA_AVATAR_PATH", "/nonexistent")),
]
AVATAR_PATH = next((p for p in _AVATAR_CANDIDATES if p.exists()), None)

HOT_STATUSES = {"New", "Contacted", "Qualified"}

STATUS_LABELS = {
    "New":       "Nuevo",
    "Contacted": "Contactado",
    "Qualified": "Calificado (Hot)",
}

STATUS_ORDER = ["Qualified", "Contacted", "New"]

LOG = lambda msg: print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ── CRM ────────────────────────────────────────────────────────────────────────
def _fetch_lead_notes(token):
    """Devuelve {lead_id: latest_note_text} con la nota más reciente de ETCRM_Lead_Note por lead."""
    from _etendo import ETENDO_BASE
    all_notes = []
    start = 0
    while True:
        body = urllib.parse.urlencode({
            "_operationType": "fetch",
            "_startRow":       str(start),
            "_endRow":         str(start + 499),
            "_noActiveFilter": "true",
        }).encode()
        req = urllib.request.Request(
            f"{ETENDO_BASE}/api/datasource/ETCRM_Lead_Note",
            data=body, method="POST"
        )
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                page = json.loads(r.read()).get("response", {}).get("data", [])
        except Exception as e:
            LOG(f"WARN fetch_lead_notes: {e}")
            break
        if not page:
            break
        all_notes.extend(page)
        if len(page) < 500:
            break
        start += 500

    # Agrupar por lead y quedarse con la nota más reciente (creationDate desc)
    best = {}
    for n in all_notes:
        lid  = n.get("lead")
        text = (n.get("note") or "").strip()
        date = n.get("creationDate") or ""
        if not lid or not text:
            continue
        if lid not in best or date > best[lid]["date"]:
            best[lid] = {"date": date, "note": text}
    return {lid: v["note"] for lid, v in best.items()}


def get_hot_leads():
    """Lee todos los leads via JWT Comercial — no depende del rol activo de Rocío."""
    from _etendo import etendo_login, etendo_fetch
    token = etendo_login(ROLE_ID)
    if not token:
        LOG("ERROR get_hot_leads: JWT login falló")
        return [], []
    raw = etendo_fetch(token, "ETCRM_Lead")
    all_leads = list({l["id"]: l for l in raw if l.get("id")}.values())

    # Adjuntar la nota más reciente de ETCRM_Lead_Note a cada lead
    notes_map = _fetch_lead_notes(token)
    for l in all_leads:
        crm_note = notes_map.get(l.get("id"), "")
        if crm_note:
            l["_crm_note"] = crm_note

    TEST_KEYWORDS = ["prueba", "test", "demo", "testing", "devops"]

    def is_test(lead):
        company = (lead.get("company") or "").lower()
        fn      = (lead.get("firstName") or "").lower()
        email   = (lead.get("email") or "").lower()
        return (
            any(k in company for k in TEST_KEYWORDS) or
            fn in {"test", "prueba", "demo", "devops", "unknown"} or
            email.startswith(("test", "prueba", "devops"))
        )

    DISCARD_KEYWORDS = ["descartado", "descartar", "no interesa", "no le interesa",
                        "no está interesado", "no interesado", "sin interés",
                        "no continúa", "no continua", "archivado", "cold"]

    def is_discarded_by_note(lead):
        latest = (
            lead.get("_crm_note")
            or _latest_note((lead.get("description") or ""), (lead.get("interest") or ""))
        ).lower()
        return any(k in latest for k in DISCARD_KEYWORDS)

    hot = [l for l in all_leads
           if (l.get("leadStatus$_identifier") or "") in HOT_STATUSES
           and not is_test(l)
           and not is_discarded_by_note(l)]

    def sort_key(l):
        s = (l.get("leadStatus$_identifier") or "")
        order = STATUS_ORDER.index(s) if s in STATUS_ORDER else 99
        prob  = float(l.get("successProbability") or 0)
        return (order, -prob)

    hot.sort(key=sort_key)

    # Detectar leads que necesitan seguimiento:
    # 1. Sin actualización en 30+ días
    # 2. Última nota menciona acción futura que ya pasó (llamada viernes, meet agendada, etc.)
    from datetime import timezone
    now = datetime.now(timezone.utc)
    stale = []
    seen_stale = set()

    for l in hot:
        lid = l.get("id")
        updated_str = l.get("updated") or l.get("updatedAt") or ""
        days_old = 0
        if updated_str:
            try:
                from datetime import datetime as _dt
                updated = _dt.fromisoformat(updated_str.replace("Z", "+00:00"))
                days_old = (now - updated).days
            except Exception:
                pass

        # Motivo 1: sin mover 30+ días
        if days_old >= 30 and lid not in seen_stale:
            stale.append((l, days_old, f"Sin actualización hace {days_old} días"))
            seen_stale.add(lid)
            continue

        # Motivo 2: última nota tiene acción pendiente no resuelta
        # Preferimos _crm_note (ETCRM_Lead_Note) sobre description/interest
        if lid not in seen_stale:
            latest = (
                l.get("_crm_note")
                or _latest_note((l.get("description") or ""), (l.get("interest") or ""))
            )
            latest_lower = latest.lower()
            PENDING_KEYWORDS = [
                "viernes", "lunes", "martes", "miércoles", "miercoles",
                "jueves", "sábado", "sabado", "domingo",
                "próxima semana", "proxima semana", "esta semana",
                "la semana que viene", "próximo", "proximo",
                "agendada", "agendado", "programada", "programado",
                "pendiente enviar", "pendiente respuesta", "queda pendiente",
                "voy a enviar", "voy a mandar", "mando esta", "envío esta",
                "llamada para", "call para", "reunión para", "reunion para",
                "meet para", "demo para", "propuesta para", "pospuesta para",
            ]
            has_pending = any(k in latest_lower for k in PENDING_KEYWORDS)
            if has_pending:
                # Intentar parsear fecha al inicio de la nota (ej: "14/5 —")
                note_days_old = days_old  # fallback: usar updated
                m = _re.search(r"(\d{1,2})/(\d{1,2})", latest[:20])
                if m:
                    try:
                        day, month = int(m.group(1)), int(m.group(2))
                        year = now.year if month <= now.month else now.year - 1
                        from datetime import datetime as _dt2
                        note_date = _dt2(year, month, day, tzinfo=timezone.utc)
                        note_days_old = (now - note_date).days
                    except Exception:
                        pass
                if note_days_old >= 5:
                    stale.append((l, note_days_old, "Acción pendiente sin resolver"))
                    seen_stale.add(lid)

    return hot, stale


# ── Email HTML ────────────────────────────────────────────────────────────────
import re as _re

def _latest_note(summary, interest=""):
    """
    Devuelve la nota más reciente del campo summary.
    Soporta dos formatos:
      - Cronológico (más viejo primero): '20/4 — texto\n22/4 — texto\n12/5 — texto'
        → toma la ÚLTIMA línea con contenido
      - Inverso (reply_checker, más nuevo primero): '[21/05/2026 — sender] texto\nanterior'
        → toma la PRIMERA línea (empieza con '[')
    Si no hay summary, usa interest.
    """
    text = summary.strip()
    if not text:
        text = interest.strip()
    if not text:
        return ""

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ""

    # Formato inverso: primera línea empieza con '['
    if lines[0].startswith("["):
        return lines[0]

    # Formato cronológico: última línea con contenido es la más reciente
    return lines[-1]


def lead_name(lead):
    fn = (lead.get("firstname") or lead.get("firstName") or "").strip()
    ln = (lead.get("lastname") or lead.get("lastName") or "").strip()
    name = f"{fn} {ln}".strip()
    if not name:
        ident = (lead.get("_identifier") or "").replace(" - ", " ").strip()
        name = ident
    return name if name else "(sin nombre)"


def _note_days_ago(note_text, lead_updated=""):
    """Días transcurridos desde la fecha de la nota más reciente (o del campo updated)."""
    now = datetime.now(timezone.utc)
    if note_text:
        m = _re.search(r"\[?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", note_text[:30])
        if m:
            try:
                day, month = int(m.group(1)), int(m.group(2))
                yr = m.group(3)
                year = int(yr) + (2000 if int(yr) < 100 else 0) if yr else now.year
                if month > now.month or (month == now.month and day > now.day):
                    year -= 1
                note_date = datetime(year, month, day, tzinfo=timezone.utc)
                return (now - note_date).days
            except Exception:
                pass
    if lead_updated:
        try:
            updated = datetime.fromisoformat(lead_updated.replace("Z", "+00:00"))
            return (now - updated).days
        except Exception:
            pass
    return None


def _days_badge(days):
    """HTML badge de días desde última actualización."""
    if days is None:
        return ""
    if days <= 7:
        color, icon = "#166534", f"hace {days}d"
    elif days <= 14:
        color, icon = "#b45309", f"⚠ hace {days}d"
    else:
        color, icon = "#991b1b", f"🔴 hace {days}d"
    return f'<br><span style="font-size:10px;font-weight:600;color:{color};">{icon}</span>'


def build_html(leads, today_str, stale=None):
    stale = stale or []
    rows_html = ""
    badge_colors = {
        "Qualified": ("#166534", "#dcfce7"),
        "Contacted": ("#1e40af", "#dbeafe"),
        "New":       ("#374151", "#f3f4f6"),
    }

    for i, lead in enumerate(leads):
        bg          = "#ffffff" if i % 2 == 0 else "#f8f9fb"
        status_raw  = (lead.get("leadStatus$_identifier") or "")
        label       = STATUS_LABELS.get(status_raw, status_raw or "—")
        prob        = int(float(lead.get("successProbability") or 0))
        company     = lead.get("company") or "—"
        name        = lead_name(lead)
        crm_note    = lead.get("_crm_note") or ""
        desc        = (lead.get("description") or "").strip()
        interest    = (lead.get("interest") or "").strip()
        latest      = _latest_note(crm_note, "") if crm_note else _latest_note(desc, interest)
        summary     = latest[:120] + ("…" if len(latest) > 120 else "") if latest else "—"
        txt_c, bg_c = badge_colors.get(status_raw, ("#374151", "#f3f4f6"))
        days        = _note_days_ago(latest, lead.get("updated") or "")
        badge       = _days_badge(days)

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
            {prob}%</td>
          <td style="padding:10px 12px;font-size:12px;color:#6b7280;
                     border-bottom:1px solid #e5e7eb;">
            {summary or "—"}{badge}</td>
        </tr>"""

    count = len(leads)
    avatar_src = "cid:lucia_avatar" if AVATAR_PATH else \
        "https://ui-avatars.com/api/?name=Lucia&background=0e6df6&color=fff&size=72"

    # Sección de leads desactualizados
    if stale:
        stale_rows = ""
        for lead, days, reason in stale:
            name    = lead_name(lead)
            company = lead.get("company") or "—"
            status  = STATUS_LABELS.get((lead.get("leadStatus") or "").lower(), "—")
            crm_note = lead.get("_crm_note") or ""
            desc     = (lead.get("description") or "").strip()
            interest = (lead.get("interest") or "").strip()
            latest   = _latest_note(crm_note, "") if crm_note else _latest_note(desc, interest)
            note    = latest[:100] + ("…" if len(latest) > 100 else "") if latest else "—"
            stale_rows += f"""
            <tr>
              <td style="padding:8px 12px;font-size:13px;color:#111827;border-bottom:1px solid #fde68a;">
                <strong>{name}</strong><br>
                <span style="font-size:11px;color:#92400e;">{company}</span>
              </td>
              <td style="padding:8px 12px;font-size:11px;color:#92400e;border-bottom:1px solid #fde68a;
                         text-align:center;white-space:nowrap;">{days}d sin update</td>
              <td style="padding:8px 12px;font-size:12px;color:#92400e;border-bottom:1px solid #fde68a;
                         text-align:center;">{status}</td>
              <td style="padding:8px 12px;font-size:11px;color:#78350f;border-bottom:1px solid #fde68a;">
                {note}<br>
                <span style="color:#b45309;font-style:italic;font-size:10px;">⚠ {reason}</span>
              </td>
            </tr>"""
        stale_section = f"""
  <tr><td style="padding:0 32px 24px;">
    <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;overflow:hidden;">
      <div style="background:#fef3c7;padding:10px 16px;border-bottom:1px solid #fde68a;">
        <span style="font-size:13px;font-weight:700;color:#92400e;">
          ⚠️ Requieren actualización urgente ({len(stale)} leads sin mover hace +30 días)
        </span>
      </div>
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr style="background:#fffbeb;">
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#92400e;
                     text-transform:uppercase;">Lead</th>
          <th style="padding:8px 12px;text-align:center;font-size:11px;color:#92400e;
                     text-transform:uppercase;">Sin mover</th>
          <th style="padding:8px 12px;text-align:center;font-size:11px;color:#92400e;
                     text-transform:uppercase;">Estado</th>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#92400e;
                     text-transform:uppercase;">Última nota</th>
        </tr>
        {stale_rows}
      </table>
      <div style="padding:10px 16px;font-size:12px;color:#92400e;">
        👉 Respondé este email indicando qué pasó con cada uno o si hay que descartarlos.
      </div>
    </div>
  </td></tr>"""
    else:
        stale_section = ""

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
      <td width="120" valign="middle" style="padding-right:20px;">
        <img src="{avatar_src}" width="100" height="128"
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
                     border-bottom:2px solid #e5e7eb;">Prob.</th>
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

  {stale_section}

  <tr><td style="background:#f8f9fb;padding:16px 32px;border-top:1px solid #e5e7eb;">
    <p style="margin:0;font-size:12px;color:#9ca3af;text-align:center;">
      CRM Pulse automático · {today_str} · Etendo Revenue Org<br>
      Este email se genera cada lunes a las 9am.
    </p>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""


def send_pulse(leads, today_str, stale=None):
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
    related.attach(MIMEText(build_html(leads, today_str, stale), "html", "utf-8"))

    if AVATAR_PATH:
        mime_type = "gif" if AVATAR_PATH.suffix.lower() == ".gif" else "jpeg"
        filename  = f"lucia.{mime_type}"
        with open(AVATAR_PATH, "rb") as f:
            img = MIMEImage(f.read(), mime_type)
        img.add_header("Content-ID", "<lucia_avatar>")
        img.add_header("Content-Disposition", "inline", filename=filename)
        related.attach(img)
        LOG(f"Avatar adjuntada como CID inline ({mime_type})")
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

    LOG("Cargando leads activos del CRM…")
    leads, stale = get_hot_leads()
    LOG(f"  {len(leads)} leads activos | {len(stale)} desactualizados (+30 días)")

    if not leads:
        LOG("Sin leads activos — no se envía email."); return

    send_pulse(leads, today_str, stale)
    LOG(f"Done. {len(leads)} leads, {len(stale)} stale, {len(RECIPIENTS)} destinatario(s).")


if __name__ == "__main__":
    main()
