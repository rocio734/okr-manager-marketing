#!/usr/bin/env python3
"""
followup_vacaciones.py — Recontacta leads que respondieron con autoreply de vacaciones
                         y cuya fecha de vuelta ya pasó.

Lee outreach_autoreplies.json, filtra tipo='vacaciones' y fecha_vuelta <= hoy,
envía followup vía n8n y actualiza tracking.

Uso:
  python3 followup_vacaciones.py [--dry-run]
"""
import json, os, sys, time, re, requests
from datetime import datetime, timezone, date
from pathlib import Path
import urllib.parse

ROOT = Path(__file__).resolve().parent
ENV  = ROOT.parent.parent / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(ROOT))
from outreach_tracking_helper import load_tracking, save_tracking, update_stage

DRY_RUN    = "--dry-run" in sys.argv
N8N_WEBHOOK = "https://n8n.labs.etendo.cloud/webhook/a3f7c821-5d04-4b9e-8c31-0e72b49d6f15"
GMAIL_USER  = os.environ.get("GMAIL_USER", "victoria.miguez@smfconsulting.es")
PIXEL_BASE  = "https://etendo-dashboard-api.onrender.com/pixel"
SLEEP_BETWEEN = 4

AUTOREPLIES_FILE = ROOT / "outreach_autoreplies.json"
TRACKING_FILE    = ROOT / "outreach_tracking.json"

SUBJECT_FOLLOWUP = "Re: Etendo — te escribo ahora que volviste"

BODY_FOLLOWUP = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>Te escribí hace unos días cuando estabas de vacaciones. Aprovecho ahora que ya estás de vuelta para retomar el mensaje.</p>

<p>Te comentaba sobre Etendo, un ERP de código abierto que permite integrar agentes de IA externos — como Claude o GPT — para operar dentro del propio sistema: crear pedidos, gestionar facturas, consultar stock desde lenguaje natural. Sin licencias por usuario y con código completamente accesible.</p>

<p>¿Tendrías 20 minutos esta semana para ver si encaja con algún perfil de cliente vuestro?</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — Canal de partners<br>
<a href="https://etendo.software?utm_source=email&utm_medium=outreach&utm_campaign=followup_vacaciones" style="color:#E85D04;">etendo.software</a></p>
</div>"""


def parse_fecha_vuelta(raw: str) -> date | None:
    """Intenta parsear fechas como '25/08/2026', '2026-08-25', 'August 25, 2026'."""
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%B %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    # Intento con regex: busca 4 dígitos de año
    m = re.search(r'(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})', raw)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def send_via_n8n(to_email: str, subject: str, body_html: str, company: str) -> bool:
    payload = {
        "to":      to_email,
        "from":    GMAIL_USER,
        "subject": subject,
        "html":    body_html,
        "empresa": company,
    }
    try:
        r = requests.post(N8N_WEBHOOK, json=payload, timeout=15)
        return r.status_code in (200, 201, 202)
    except Exception as e:
        print(f"    ⚠️  n8n error: {e}")
        return False


def main():
    today = date.today()
    print(f"\n{'='*60}")
    print(f"  FOLLOWUP VACACIONES — {today}")
    print(f"  dry_run: {DRY_RUN}")
    print(f"{'='*60}\n")

    # ── Cargar auto-replies: archivo local + Supabase ────────────────────────
    autoreplies = []

    # 1. Archivo local (fallback / manual)
    if AUTOREPLIES_FILE.exists():
        autoreplies = json.loads(AUTOREPLIES_FILE.read_text())
    else:
        print("No existe outreach_autoreplies.json local — usando solo Supabase.")

    # 2. Supabase (fuente principal desde n8n)
    sb_url = os.environ.get("SUPABASE_URL", "")
    sb_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if sb_url and sb_key:
        try:
            import urllib.request as _ureq
            req = _ureq.Request(
                f"{sb_url}/rest/v1/outreach_autoreplies?tipo=eq.vacaciones&select=*",
                headers={
                    "apikey": sb_key,
                    "Authorization": f"Bearer {sb_key}",
                }
            )
            with _ureq.urlopen(req, timeout=10) as resp:
                sb_rows = json.loads(resp.read().decode())
            # Normalizar al mismo formato que el JSON local
            local_emails = {a.get("email","").lower() for a in autoreplies}
            for row in sb_rows:
                em = (row.get("email") or "").lower().strip()
                if em and em not in local_emails:
                    autoreplies.append({
                        "email":        em,
                        "tipo":         row.get("tipo", "vacaciones"),
                        "fecha_vuelta": row.get("fecha_vuelta", ""),
                        "raw_subject":  row.get("raw_subject", ""),
                        "procesado":    row.get("procesado", False),
                        "_sb_id":       row.get("id"),
                    })
            print(f"Supabase: {len(sb_rows)} OOO cargados ({len(autoreplies)} total tras merge)")
        except Exception as e:
            print(f"  ⚠️  Supabase no disponible: {e}")
    else:
        print("  ⚠️  SUPABASE_URL/KEY no configuradas — solo archivo local")

    tracking = load_tracking()

    # Filtrar: tipo=vacaciones, fecha_vuelta ya pasó, no procesado aún como followup
    candidatos = []
    for ar in autoreplies:
        if ar.get("tipo") != "vacaciones":
            continue
        fecha_vuelta = parse_fecha_vuelta(ar.get("fecha_vuelta", ""))
        if not fecha_vuelta:
            print(f"  ⚠️  Sin fecha parseable: {ar.get('email')} — {ar.get('fecha_vuelta')!r}")
            continue
        if fecha_vuelta > today:
            print(f"  ⏳ {ar.get('email')} — vuelve el {fecha_vuelta} (aún no)")
            continue

        email = ar.get("email", "")
        lead  = tracking.get(email, {})
        # Saltar si ya tiene nota de followup_vacaciones en el historial
        ya_enviado = any(
            c.get("etapa_label") == "followup_vacaciones"
            for c in lead.get("contactos", [])
        )
        if ya_enviado:
            print(f"  ✓ {email} — followup vacaciones ya enviado, saltando")
            continue

        candidatos.append(ar)

    print(f"\nCandidatos para followup: {len(candidatos)}\n")

    if not candidatos:
        print("Nada que enviar hoy.")
        return

    enviados = 0
    for ar in candidatos:
        email   = ar.get("email", "")
        company = tracking.get(email, {}).get("empresa", email.split("@")[0])

        pixel = f'<img src="{PIXEL_BASE}/{urllib.parse.quote(email)}.gif" width="1" height="1" style="display:none" alt="">'
        body  = BODY_FOLLOWUP.replace("{empresa}", company) + pixel

        print(f"  → {company[:40]:40s} | {email}")

        if not DRY_RUN:
            ok = send_via_n8n(email, SUBJECT_FOLLOWUP, body, company)
            if ok:
                update_stage(tracking, email, 3,
                             canal="email", subject=SUBJECT_FOLLOWUP,
                             enviado_por=GMAIL_USER,
                             fecha=datetime.now(timezone.utc).isoformat(),
                             notas="followup post-vacaciones")
                enviados += 1
                print(f"    ✓ enviado")
            else:
                print(f"    ✗ fallo n8n")
            time.sleep(SLEEP_BETWEEN)
        else:
            enviados += 1

    print(f"\n{'─'*60}")
    print(f"  {'[DRY RUN] ' if DRY_RUN else ''}Followups enviados: {enviados}")
    print(f"{'─'*60}\n")

    if not DRY_RUN and enviados:
        save_tracking(tracking)
        print("✅ Tracking guardado.")


if __name__ == "__main__":
    main()
