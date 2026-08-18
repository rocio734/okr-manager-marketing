"""
Follow-up a leads en etapa Contactado del Intel Dashboard.
Enviado 3 días después del primer contacto (lunes 10 ago).
Gancho personalizado por sector. Subject: Re: [subject original].
"""
import json, os, time, requests
from pathlib import Path
from datetime import datetime, timezone
from outreach_tracking_helper import load_tracking, save_tracking, update_stage, get_emails_sent, print_summary

# ── .env ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
ENV  = ROOT.parent.parent / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

N8N_WEBHOOK = "https://n8n.labs.etendo.cloud/webhook/a3f7c821-5d04-4b9e-8c31-0e72b49d6f15"

SUPABASE_URL = "https://cbescsuieiebxbptrqnh.supabase.co"
SUPABASE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNiZXNjc3VpZWllYnhicHRycW5oIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NzkwNTc4OCwiZXhwIjoyMDkzNDgxNzg4fQ.n13uXxHu95PU0QrzoE4NxGYKi-xDuVd1cKmmu_kViqY"
)
SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

CONTACTADO_STAGE = "5f230c21-f6e8-4a1d-8b85-4a49655a1a5d"
LOG_FILE = ROOT / "followup_sent_contactados.json"

# ── Ganchos por sector ────────────────────────────────────────────────────────
SECTOR_HOOK = {
    "Consultoría ERP": (
        "Para consultoras como la vuestra, eso significa poder ofrecer a vuestros clientes "
        "un ERP que se opera directamente desde Claude — sin formación extra, sin pantallas, "
        "solo lenguaje natural. Es lo que ya están pidiendo."
    ),
    "Logística": (
        "Para empresas de logística, eso significa gestionar pedidos, stock y expediciones "
        "diciéndole a Claude lo que necesitás — el sistema actúa solo, sin abrir el ERP."
    ),
    "Construcción": (
        "Para empresas de construcción, eso significa aprobar pedidos de obra, consultar "
        "costes o gestionar subcontratas hablándole a Claude directamente, sin pantallas."
    ),
    "Industrial": (
        "Para empresas industriales, eso significa controlar producción, compras y almacén "
        "con lenguaje natural — sin tocar la interfaz del ERP."
    ),
    "Servicios": (
        "Para empresas de servicios, eso significa que el equipo ejecuta tareas en el ERP "
        "hablándole a Claude — menos fricción, menos errores manuales."
    ),
}
DEFAULT_HOOK = (
    "Para vuestro equipo, eso significa operar el ERP desde Claude en lenguaje natural "
    "— sin tocar la interfaz, el sistema actúa solo."
)


def get_hook(sector: str) -> str:
    for key, hook in SECTOR_HOOK.items():
        if key.lower() in sector.lower():
            return hook
    return DEFAULT_HOOK


def build_body(empresa: str, sector: str) -> str:
    hook = get_hook(sector)
    return f"""\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.7;color:#1a1a1a;max-width:560px;">
<p>Hola,</p>

<p>Te escribí el lunes sobre Etendo, el ERP que se opera desde Claude en lenguaje natural. {hook}</p>

<p>Por si te es útil, podés ver más en <a href="https://etendo.software" style="color:#E85D04;">etendo.software</a></p>

<p>¿Tiene sentido hablar 20 minutos esta semana?</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — <a href="https://etendo.software" style="color:#E85D04;">etendo.software</a></p>
</div>"""


# ── Supabase helpers ──────────────────────────────────────────────────────────
def fetch_contactados():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/deals?stage_id=eq.{CONTACTADO_STAGE}"
        f"&select=id,contact_id&limit=100",
        headers=SB_HEADERS, timeout=10
    )
    deals = r.json() if r.status_code == 200 else []

    leads = []
    for d in deals:
        cid = d.get("contact_id")
        if not cid:
            continue
        rc = requests.get(
            f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{cid}"
            f"&select=id,nombre,email,empresa,custom_fields",
            headers=SB_HEADERS, timeout=10
        )
        if rc.status_code != 200 or not rc.json():
            continue
        c = rc.json()[0]
        email = c.get("email", "—")
        if not email or email == "—":
            continue
        if email == "rocio.altamirano@smfconsulting.es":
            continue  # skip test
        cf = c.get("custom_fields") or {}
        if isinstance(cf, str):
            try:
                cf = json.loads(cf)
            except Exception:
                cf = {}
        leads.append({
            "deal_id":    d["id"],
            "contact_id": cid,
            "empresa":    c.get("empresa") or c.get("nombre") or "?",
            "email":      email,
            "sector":     cf.get("sector", ""),
            "subject":    cf.get("outreach_subject", "Etendo ERP"),
            "attempts":   cf.get("attempts", 0),
            "cf":         cf,
        })
    return leads


def mark_sent(contact_id: str, cf: dict):
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cf["attempts"] = cf.get("attempts", 0) + 1
    cf["last_sent_at"] = now_iso
    cf["followup_sent_at"] = now_iso
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}",
        headers={**SB_HEADERS, "Prefer": "return=minimal"},
        json={"custom_fields": cf},
        timeout=10,
    )


# ── Log helpers ───────────────────────────────────────────────────────────────
def load_sent():
    """Carga emails ya con followup desde el tracking centralizado (etapa >= 3)."""
    tracking = load_tracking()
    already = get_emails_sent(tracking, etapa=3)
    # compatibilidad con log viejo
    if LOG_FILE.exists():
        old = set(json.loads(LOG_FILE.read_text()).get("sent_emails", []))
        already |= old
    return already, tracking


def save_sent(tracking: dict) -> None:
    save_tracking(tracking)


# ── Email sender ──────────────────────────────────────────────────────────────
def send_email(to_email: str, subject: str, html_body: str, dry_run=False) -> bool:
    if dry_run:
        print(f"    [DRY] → {to_email} | Re: {subject[:55]}")
        return True
    try:
        resp = requests.post(
            N8N_WEBHOOK,
            json={"to": to_email, "subject": f"Re: {subject}", "html": html_body},
            timeout=20,
        )
        if resp.status_code not in (200, 201):
            print(f"    ✗ n8n error {resp.status_code}: {resp.text[:120]}")
            return False
        return True
    except Exception as e:
        print(f"    ✗ Error: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main(dry_run=False):
    print("Cargando leads en etapa Contactado...")
    leads = fetch_contactados()
    sent_emails, tracking = load_sent()

    pending = [l for l in leads if l["email"] not in sent_emails]

    print(f"  Contactados totales:  {len(leads)}")
    print(f"  Ya con follow-up:     {len(sent_emails)}")
    print(f"  Pendientes de enviar: {len(pending)}")
    print()

    ok, fail = 0, 0
    for lead in pending:
        empresa = lead["empresa"]
        sector  = lead["sector"]
        email   = lead["email"]
        subject = lead["subject"]
        body    = build_body(empresa, sector)

        print(f"  → {empresa[:35]:35s} [{sector[:20]:20s}] {email}")
        if dry_run:
            print(f"       Gancho: {get_hook(sector)[:80]}...")
        success = send_email(email, subject, body, dry_run=dry_run)

        if success:
            ok += 1
            if not dry_run:
                update_stage(
                    tracking, email, nueva_etapa=3,
                    canal="email",
                    subject=f"Re: {subject}",
                    enviado_por="victoria.miguez@smfconsulting.es",
                    empresa=empresa,
                    sector=sector,
                    fuente="supabase_crm",
                )
                mark_sent(lead["contact_id"], lead["cf"])
                time.sleep(3)
        else:
            fail += 1

    if not dry_run:
        save_sent(tracking)
        print_summary(tracking)

    print()
    print(f"{'[DRY RUN] ' if dry_run else ''}✅ Enviados: {ok}  ✗ Fallidos: {fail}")


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    if dry:
        print("=== DRY RUN — no se envía nada ===\n")
    main(dry_run=dry)
