"""
auto_outreach_daily.py — Envío automático diario de outreach a leads score-3.

Corre cada día laborable después de que fetch_intel.py actualiza intel_data.json.
Lee los leads score-3 nuevos (no enviados aún), envía hasta MAX_PER_DAY emails
y actualiza outreach_tracking.json + Supabase contacts.

Crontab sugerido (lun–vie a las 09:30, después del scraping de 07:30):
  30 9 * * 1-5 python3 /home/rocio/prueba/okr_manager_site/intel-dashboard/auto_outreach_daily.py >> /home/rocio/prueba/logs/auto_outreach_$(date +%Y%m%d).log 2>&1

Flags:
  --dry-run    Muestra qué se enviaría sin enviar nada
  --limit N    Cambia el cap diario (default: 10)
"""
import json, os, sys, time, urllib.request, urllib.parse, requests
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

# ── Config ────────────────────────────────────────────────────────────────────
MAX_PER_DAY  = 10          # cap de seguridad diario
SLEEP_BETWEEN = 4          # segundos entre envíos
N8N_WEBHOOK  = "https://n8n.labs.etendo.cloud/webhook/a3f7c821-5d04-4b9e-8c31-0e72b49d6f15"
GMAIL_USER   = os.environ.get("GMAIL_USER", "victoria.miguez@smfconsulting.es")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OUTREACH_SOURCE = "intel_dashboard"

FAKE_EMAILS  = {"su@email.com", "test@test.com", "example@example.com"}
SKIP_DOMAINS = {"gmail.com", "hotmail.com", "yahoo.com", "outlook.com"}

SUBJECT_DK = "Tu ERP actual tiene fecha de caducidad"
SUBJECT_MS = "Una alternativa a Dynamics que tus equipos agradecerán"

SECTOR_LINE = {
    "Consultoría":   "Para consultoras como la vuestra, eso significa ofrecer a vuestros clientes un ERP que se opera en lenguaje natural — sin formación extra.",
    "Logística":     "Para empresas de logística, eso significa gestionar pedidos, stock y expediciones diciéndole a Claude lo que necesitás — sin abrir pantallas.",
    "Construcción":  "Para empresas de construcción, eso significa aprobar pedidos de obra y gestionar subcontratas hablándole directamente a Claude.",
    "Industrial":    "Para empresas industriales, eso significa controlar producción, compras y almacén con lenguaje natural.",
    "Distribución":  "Para empresas de distribución, eso significa gestionar pedidos y rutas diciéndole a Claude lo que necesitás.",
    "Fabricación":   "Para empresas de fabricación, eso significa controlar órdenes de producción y compras con lenguaje natural — el sistema actúa solo.",
    "Servicios":     "Para empresas de servicios, eso significa que el equipo ejecuta tareas en el ERP hablándole a Claude — menos fricción, menos errores.",
    "Retail":        "Para empresas de retail, eso significa gestionar stock, pedidos a proveedor y facturación desde Claude, sin abrir el ERP.",
}
DEFAULT_SECTOR_LINE = "Para vuestro equipo, eso significa operar el ERP desde Claude en lenguaje natural — sin tocar la interfaz, el sistema actúa solo."

def get_sector_line(sector: str) -> str:
    if not sector:
        return DEFAULT_SECTOR_LINE
    for key, line in SECTOR_LINE.items():
        if key.lower() in sector.lower():
            return line
    return DEFAULT_SECTOR_LINE

BODY_DK = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.7;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>
<p>Detectamos que vuestra empresa trabaja con DistritoK o TeamSystem. Son herramientas que funcionan, pero tienen un techo claro: no están pensadas para conectarse con el resto del stack tecnológico ni para automatizar procesos con IA.</p>
<p>Etendo es un ERP de código abierto diseñado exactamente para eso. Se integra con cualquier herramienta que ya uséis y permite conectar agentes de IA externos — como Claude o GPT — para automatizar tareas dentro del propio ERP. {sector_line}</p>
<p>Si os interesa ver cómo funciona en 20 minutos, con un caso de vuestro sector, estoy disponible esta semana.</p>
<p>Un saludo,<br><strong>Victoria Miguez</strong><br>Etendo — <a href="https://etendo.software" style="color:#E85D04;">etendo.software</a></p>
</div>"""

BODY_MS = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.7;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>
<p>Muchas empresas que trabajan con Microsoft Dynamics nos contactan porque el coste de licencias y la dependencia del partner se va haciendo insostenible a medida que crecen.</p>
<p>Etendo es un ERP de código abierto — sin licencias por usuario, sin bloqueos de proveedor — y está diseñado para conectarse con agentes de IA externos. {sector_line}</p>
<p>Si tenéis curiosidad, podemos hacer una demo corta enfocada en vuestro sector. Sin compromiso.</p>
<p>Un saludo,<br><strong>Victoria Miguez</strong><br>Etendo — <a href="https://etendo.software" style="color:#E85D04;">etendo.software</a></p>
</div>"""


# ── Helpers Supabase ──────────────────────────────────────────────────────────
def _sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

def supabase_upsert_contact(email, empresa, sector, signal, domain):
    """Crea el contacto en Supabase contacts si no existe."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        # Verificar si ya existe
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/contacts?email=eq.{urllib.parse.quote(email)}&select=id",
            headers=_sb_headers(), timeout=8)
        if r.status_code == 200 and r.json():
            return  # ya existe
        # Crear
        requests.post(
            f"{SUPABASE_URL}/rest/v1/contacts",
            headers=_sb_headers(),
            json={
                "email":   email,
                "nombre":  empresa,
                "empresa": empresa,
                "sector":  sector,
                "dominio": domain,
                "signal":  signal,
                "fuente":  OUTREACH_SOURCE,
            },
            timeout=8,
        )
    except Exception as e:
        print(f"  ⚠️  Supabase contact error: {e}")


# ── Tracking local ────────────────────────────────────────────────────────────
def load_tracking():
    tf = ROOT / "outreach_tracking.json"
    if tf.exists():
        data = json.loads(tf.read_text(encoding="utf-8"))
        return data.get("contactos", data) if "contactos" in data else data
    return {}

def save_tracking(tracking):
    tf = ROOT / "outreach_tracking.json"
    if tf.exists():
        data = json.loads(tf.read_text(encoding="utf-8"))
        if "contactos" in data:
            data["contactos"] = tracking
            tf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return
    tf.write_text(json.dumps(tracking, indent=2, ensure_ascii=False), encoding="utf-8")

def already_sent(tracking):
    """Emails que ya tienen etapa >= 2 (enviado) o == 99 (descartado)."""
    return {e for e, c in tracking.items() if c.get("etapa", 0) >= 2 or c.get("etapa") == 99}

def mark_sent(tracking, email, empresa, domain, sector, signal, subject):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tracking[email] = {
        "email":       email,
        "empresa":     empresa,
        "dominio":     domain,
        "sector":      sector,
        "signal":      signal,
        "fuente":      OUTREACH_SOURCE,
        "etapa":       2,
        "etapa_label": "email1_enviado",
        "contactos": [{
            "etapa":       2,
            "etapa_label": "email1_enviado",
            "canal":       "email_n8n",
            "subject":     subject,
            "fecha":       ts,
            "enviado_por": GMAIL_USER,
            "notas":       "Enviado automáticamente por auto_outreach_daily.py",
        }],
        "notas": "",
    }


# ── Envío por n8n ─────────────────────────────────────────────────────────────
def send_email(to_email, subject, html_body, dry_run=False):
    if dry_run:
        print(f"    [DRY RUN] → {to_email}")
        return True
    try:
        r = requests.post(N8N_WEBHOOK, json={
            "to":      to_email,
            "from":    GMAIL_USER,
            "subject": subject,
            "html":    html_body,
        }, timeout=20)
        return r.status_code in (200, 201, 202)
    except Exception as e:
        print(f"    ❌ Error enviando a {to_email}: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    dry_run   = "--dry-run" in sys.argv
    limit_arg = next((int(sys.argv[i+1]) for i, a in enumerate(sys.argv)
                      if a == "--limit" and i+1 < len(sys.argv)), None)
    max_hoy   = limit_arg or MAX_PER_DAY

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*60}")
    print(f"  AUTO OUTREACH DIARIO — {now}")
    print(f"  Cap: {max_hoy} emails | dry_run: {dry_run}")
    print(f"{'='*60}\n")

    # 1. Cargar leads score-3 de intel_data.json
    intel_file = ROOT / "intel_data.json"
    if not intel_file.exists():
        print("❌ intel_data.json no encontrado. Ejecutá fetch_intel.py primero.")
        return

    with open(intel_file, encoding="utf-8") as f:
        data = json.load(f)

    leads = data.get("leads_history", [])
    score3 = [
        l for l in leads
        if l.get("score", 0) == 3
        and l.get("email", "—") not in ("—", "", None)
        and l.get("email", "") not in FAKE_EMAILS
        and l.get("email", "").split("@")[-1] not in SKIP_DOMAINS
    ]

    # Dedup por email
    seen, score3_dedup = set(), []
    for l in score3:
        if l["email"] not in seen:
            seen.add(l["email"])
            score3_dedup.append(l)

    # 2. Filtrar los ya enviados
    tracking    = load_tracking()
    sent_emails = already_sent(tracking)
    pending     = [l for l in score3_dedup if l["email"] not in sent_emails]

    print(f"Leads score-3 con email : {len(score3_dedup)}")
    print(f"Ya enviados/descartados : {len(sent_emails)}")
    print(f"Pendientes hoy          : {len(pending)}")
    print(f"Enviando hoy (cap {max_hoy})   : {min(len(pending), max_hoy)}")
    print()

    # ── Fallback: leads en etapa 0 del tracking que no están en intel_data ──
    tracking_emails_etapa0 = [
        (email, c) for email, c in tracking.items()
        if c.get("etapa", 0) == 0
        and email not in sent_emails
        and email not in {l["email"] for l in score3_dedup}
        and email not in FAKE_EMAILS
        and email.split("@")[-1] not in SKIP_DOMAINS
    ]
    if tracking_emails_etapa0:
        print(f"Fallback tracking etapa 0   : {len(tracking_emails_etapa0)} leads sin contactar")
        for email, c in tracking_emails_etapa0:
            pending.append({
                "email":        email,
                "company":      c.get("empresa", email.split("@")[1]),
                "domain":       c.get("dominio", email.split("@")[1]),
                "sector":       c.get("sector", ""),
                "signal_label": c.get("signal", "DistritoK"),
                "score":        3,
            })

    if not pending:
        print("✅ No hay leads nuevos pendientes hoy.")
        return

    # 3. Enviar hasta max_hoy
    lote  = pending[:max_hoy]
    ok, fail = 0, 0

    for lead in lote:
        email   = lead["email"]
        signal  = lead.get("signal_label", "")
        domain  = lead.get("domain", "")
        sector  = lead.get("sector", "—")
        empresa = (lead.get("company") or domain).strip() or domain

        sl      = get_sector_line(sector)
        subject = SUBJECT_MS if "MS Dynamics" in signal else SUBJECT_DK
        body    = (BODY_MS if "MS Dynamics" in signal else BODY_DK).replace("{empresa}", empresa).replace("{sector_line}", sl)

        print(f"  → {empresa[:38]:38s} | {email}")
        success = send_email(email, subject, body, dry_run=dry_run)

        if success:
            ok += 1
            if not dry_run:
                mark_sent(tracking, email, empresa, domain, sector, signal, subject)
                supabase_upsert_contact(email, empresa, sector, signal, domain)
        else:
            fail += 1

        if not dry_run:
            time.sleep(SLEEP_BETWEEN)

    # 4. Guardar tracking actualizado
    if not dry_run and ok:
        save_tracking(tracking)

    pendientes_restantes = len(pending) - max_hoy
    print(f"\n{'─'*60}")
    print(f"  ✅ Enviados: {ok}  |  ❌ Fallidos: {fail}")
    if pendientes_restantes > 0:
        print(f"  📋 Quedan {pendientes_restantes} para días siguientes")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    main()
