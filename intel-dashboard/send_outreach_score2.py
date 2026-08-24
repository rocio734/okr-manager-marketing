#!/usr/bin/env python3
"""
send_outreach_score2.py — Outreach a leads de score 2 (señal genérica ERP, temperatura fría).

Target: empresas españolas en sectores ERP-intensivos detectadas en Empresite / Apollo.
Ángulo: plantar semilla — ERP Agéntico, sin asumir pain concreto ni ERP actual.

Envía desde victoria.miguez@smfconsulting.es via n8n webhook.
Cap: MAX_PER_DAY emails/día, con seguimiento en outreach_tracking.json.

Uso:
  python3 send_outreach_score2.py [--dry-run] [--limit N]
"""
import json, os, sys, time, requests, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV  = ROOT.parent.parent / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(ROOT))
from outreach_tracking_helper import load_tracking, save_tracking, update_stage, get_emails_sent, print_summary
import fetch_intel as fi

# ── Config ─────────────────────────────────────────────────────────────────
MAX_PER_DAY   = 50
SLEEP_BETWEEN = 4
N8N_WEBHOOK   = "https://n8n.labs.etendo.cloud/webhook/a3f7c821-5d04-4b9e-8c31-0e72b49d6f15"
GMAIL_USER    = os.environ.get("GMAIL_USER", "victoria.miguez@smfconsulting.es")

DRY_RUN = "--dry-run" in sys.argv
LIMIT   = int(next((sys.argv[sys.argv.index("--limit")+1]
                    for i, a in enumerate(sys.argv) if a == "--limit"), MAX_PER_DAY))

FAKE_EMAILS  = {"su@email.com", "test@test.com", "example@example.com",
                "contacto@tuempresa.com", "info@tuempresa.com", "email@tudominio.com",
                "johnsmith@example.com", "jeff@paige.black"}
FAKE_DOMAINS = {"example.com", "mailinator.com", "guerrillamail.com"}
SKIP_DOMAINS = {"gmail.com", "hotmail.com", "yahoo.com", "outlook.com",
                "yahoo.es", "hotmail.es", "live.com"}

SUBJECT = "Cuántas horas pierde tu equipo haciendo lo mismo"

# ── UTM ────────────────────────────────────────────────────────────────────
UTM_BASE = "utm_source=email&utm_medium=outreach&utm_campaign=leads_score2"

def etendo_link() -> str:
    return f"https://etendo.software?{UTM_BASE}"

# ── Pixel ──────────────────────────────────────────────────────────────────
PIXEL_BASE = "https://etendo-dashboard-api.onrender.com/pixel"

def pixel_tag(email: str) -> str:
    return f'<img src="{PIXEL_BASE}/{urllib.parse.quote(email)}.gif" width="1" height="1" style="display:none" alt="">'

# ── Copy ───────────────────────────────────────────────────────────────────
BODY = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>Os escribo porque trabajáis en un sector donde el ERP es crítico y los procesos cambian rápido — y cada vez más empresas nos preguntan lo mismo: cómo hacer que el ERP trabaje solo, sin depender de integraciones caras o de cambiar todo el sistema.</p>

<p>Etendo es un ERP Agéntico que permite conectar agentes de IA externos — como Claude o GPT — directamente en los flujos del negocio. Cualquier proceso repetitivo en vuestra empresa podría hacerse solo con indicaciones en lenguaje natural y con menos de la mitad del tiempo que lleva hoy en día.</p>

<p>No es una propuesta de cambio inmediato. Es para que lo tengáis en el radar si en algún momento el ERP actual se queda corto.</p>

<p>¿Tendríais 20 minutos para ver una demo rápida?</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — Canal Comercial<br>
<a href="{link}" style="color:#E85D04;">etendo.software</a></p>
</div>"""


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
    print(f"\n{'='*60}")
    print(f"  OUTREACH SCORE 2 — leads fríos")
    print(f"  dry_run: {DRY_RUN}  |  límite: {LIMIT}")
    print(f"{'='*60}\n")

    data     = fi.load_data()
    leads    = data["leads_history"]
    tracking = load_tracking()
    already_sent = get_emails_sent(tracking, etapa=2)

    PARTNER_SOURCES = fi.PARTNER_SOURCES

    candidates = [
        l for l in leads
        if l.get("source_type", "") not in PARTNER_SOURCES
        and l.get("score", 0) == 2
        and l.get("email", "—") not in ("—", "", None, "None")
        and l.get("email", "").split("@")[-1] not in SKIP_DOMAINS
        and l.get("email", "").split("@")[-1] not in FAKE_DOMAINS
        and l.get("email", "") not in FAKE_EMAILS
        and l.get("email", "") not in already_sent
    ]

    print(f"Score 2 con email:       {sum(1 for l in leads if l.get('source_type','') not in PARTNER_SOURCES and l.get('score',0)==2 and l.get('email','—') not in ('—','',None,'None'))}")
    print(f"Ya enviados (tracking):  {len(already_sent)}")
    print(f"Candidatos nuevos:       {len(candidates)}")
    print(f"Enviando hoy (cap={LIMIT}): {min(LIMIT, len(candidates))}\n")

    if not candidates:
        print("✅ Nada nuevo que enviar.")
        return

    enviados = 0
    for lead in candidates[:LIMIT]:
        email   = lead.get("email", "")
        company = lead.get("company", "").strip() or email.split("@")[0]
        sector  = lead.get("sector", "—")

        body_rendered = (BODY
            .replace("{empresa}", company)
            .replace("{link}", etendo_link())
        ) + pixel_tag(email)

        print(f"  → {company[:40]:40s} | {email:35s} | [{sector}]")

        if not DRY_RUN:
            ok = send_via_n8n(email, SUBJECT, body_rendered, company)
            if ok:
                update_stage(tracking, email, 2,
                             canal="email", subject=SUBJECT,
                             enviado_por=GMAIL_USER,
                             fecha=datetime.now(timezone.utc).isoformat(),
                             empresa=company,
                             dominio=lead.get("domain", ""),
                             fuente="score2_outreach")
                enviados += 1
                print(f"    ✓ enviado")
            else:
                print(f"    ✗ fallo n8n")
            time.sleep(SLEEP_BETWEEN)
        else:
            enviados += 1

    print(f"\n{'─'*60}")
    print(f"  {'[DRY RUN] ' if DRY_RUN else ''}Enviados: {enviados}")
    print(f"{'─'*60}\n")

    if not DRY_RUN:
        save_tracking(tracking)
        print_summary(tracking)


if __name__ == "__main__":
    main()
