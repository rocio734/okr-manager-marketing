#!/usr/bin/env python3
"""
send_outreach_partners.py — Outreach a partners de competidores (canal de reventa).

Target: consultoras/integradores que implementan Holded, Sage, Odoo, Ahora ERP, SAP.
Ángulo: propuesta de canal — ampliar portfolio con Etendo para clientes que necesitan
        más potencia o integración con IA.

Envía desde victoria.miguez@smfconsulting.es via n8n webhook.
Cap: MAX_PER_DAY emails/día, con seguimiento en outreach_tracking.json.

Uso:
  python3 send_outreach_partners.py [--dry-run] [--limit N]

Flags:
  --dry-run    Muestra qué se enviaría sin enviar
  --limit N    Cap manual (default: MAX_PER_DAY)
"""
import json, os, sys, time, requests
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

# ── Config ────────────────────────────────────────────────────────────────────
MAX_PER_DAY   = 15
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

SUBJECT = "Amplía tu portfolio con Etendo — ERP open source con IA"

# ── Copy por competidor ───────────────────────────────────────────────────────
# {empresa} = nombre de la consultora partner
# {competidor} = ERP que implementan (Holded, Sage, Odoo, etc.)

UTM_BASE = "utm_source=email&utm_medium=outreach&utm_campaign=partners_reventa"

def etendo_link(utm_content: str) -> str:
    return f"https://etendo.software?{UTM_BASE}&utm_content={utm_content}"

BODY_GENERICO = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>He visto que trabajáis como partners de {competidor}. Os escribo porque hay un segmento de clientes que cada vez nos llega más: empresas que buscan una alternativa con más flexibilidad técnica y sin coste de licencias por usuario.</p>

<p>Etendo es un ERP de código abierto que varios integradores ya tienen en su portfolio junto a otros ERPs. Lo que lo hace diferente es que se conecta con agentes de IA externos — como Claude o GPT — para que operen dentro del ERP: crear pedidos, consultar stock, gestionar facturas desde lenguaje natural. No es una funcionalidad de marketing: es la arquitectura real del sistema.</p>

<p>La idea es simple: si tenéis clientes que piden más integración con IA o que el coste de licencias se ha vuelto un problema, Etendo puede ser la respuesta que ya tenéis en portfolio.</p>

<p>¿Tendríais 20 minutos esta semana para ver si encaja con algún perfil de cliente vuestro?</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — Canal de partners<br>
<a href="{link_generico}" style="color:#E85D04;">etendo.software</a></p>
</div>"""

BODY_ODOO = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>He visto que trabajáis con Odoo. Os escribo porque hay un perfil de cliente que cada vez aparece más: empresas que quieren las capacidades de un ERP robusto pero sin los costes de licencia que escalan con el tamaño del equipo.</p>

<p>Etendo es 100% open source — sin licencias por usuario — y está diseñado para conectarse con agentes de IA como Claude o GPT, que operan dentro del ERP en lenguaje natural. Varios partners de Odoo ya lo tienen en portfolio para ese segmento específico.</p>

<p>No es competencia directa con Odoo: es un complemento para los casos donde el cliente pide más control sobre el código o más integración nativa con IA.</p>

<p>Si os interesa explorar si hay clientes vuestros donde podría encajar, estoy disponible esta semana para una llamada corta.</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — Canal de partners<br>
<a href="{link_odoo}" style="color:#E85D04;">etendo.software</a></p>
</div>"""

BODY_HOLDED = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>He visto que sois Solution Partners de Holded. Os escribo porque hay un perfil de cliente que Holded no siempre cubre bien: empresas medianas que necesitan más personalización en sus procesos o que quieren integrar IA directamente en el ERP.</p>

<p>Etendo es un ERP de código abierto con una arquitectura diseñada para conectar agentes de IA externos — Claude, GPT, cualquier LLM — que operan dentro del sistema: crean documentos, consultan datos, gestionan flujos desde lenguaje natural. Sin licencias por usuario y con el código completamente accesible.</p>

<p>Algunos partners lo posicionan como la evolución natural para clientes que han crecido más allá de lo que Holded puede ofrecerles.</p>

<p>¿Tendríais 20 minutos para ver si hay algún cliente vuestro donde podría encajar?</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — Canal de partners<br>
<a href="{link_holded}" style="color:#E85D04;">etendo.software</a></p>
</div>"""

BODY_SAGE = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>He visto que sois partners de Sage. Os escribo porque detectamos un patrón: hay clientes de Sage que en algún momento preguntan por alternativas sin coste de licencia o con más flexibilidad para integraciones.</p>

<p>Etendo es un ERP de código abierto — sin royalties, sin licencias por usuario — que varios integradores tienen en su portfolio junto a Sage para ese segmento específico. Lo que lo diferencia técnicamente es que está diseñado para conectarse con agentes de IA externos que operan dentro del propio ERP.</p>

<p>No es sustituir lo que ya hacéis con Sage. Es tener una respuesta para los clientes que os piden algo diferente.</p>

<p>Si os interesa explorar, podemos hacer una llamada corta esta semana.</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — Canal de partners<br>
<a href="{link_sage}" style="color:#E85D04;">etendo.software</a></p>
</div>"""

BODY_SAP = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>He visto que trabajáis con el ecosistema SAP. Os escribo porque hay clientes medianos que buscan la robustez de un ERP empresarial pero sin la estructura de costes de SAP — y es un segmento que está creciendo.</p>

<p>Etendo es un ERP de código abierto con arquitectura modular y conexión nativa con agentes de IA como Claude o GPT, que operan dentro del sistema en lenguaje natural. Sin licencias por usuario, con código accesible y con un modelo de partner que permite márgenes reales.</p>

<p>Si tenéis clientes mid-market donde SAP es demasiado para su momento actual, Etendo puede ser la alternativa que ya tenéis en portfolio.</p>

<p>¿Tendríais 20 minutos esta semana para explorar si hay fit?</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — Canal de partners<br>
<a href="{link_sap}" style="color:#E85D04;">etendo.software</a></p>
</div>"""

COPY_BY_COMPETITOR = {
    "Holded":    BODY_HOLDED,
    "Sage":      BODY_SAGE,
    "SAP":       BODY_SAP,
    "Odoo":      BODY_ODOO,
    "Ahora ERP": BODY_GENERICO,
}


PIXEL_BASE = "https://etendo-dashboard-api.onrender.com/pixel"

def pixel_tag(email: str) -> str:
    """Pixel de apertura 1x1 — mismo endpoint que el outreach de leads."""
    import urllib.parse
    return f'<img src="{PIXEL_BASE}/{urllib.parse.quote(email)}.gif" width="1" height="1" style="display:none" alt="">'


def get_competitor(lead: dict) -> str:
    """Extrae el nombre del competidor del snippet o source_type."""
    snippet = lead.get("snippet", "")
    for comp in ["Holded", "Sage", "SAP", "Odoo", "Ahora ERP", "DistritoK", "TeamSystem"]:
        if comp.lower() in snippet.lower():
            return comp
    return "ERP"


def send_via_n8n(to_email: str, subject: str, body_html: str, company: str) -> bool:
    """Envía el email via n8n webhook."""
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
    print(f"  PARTNER OUTREACH")
    print(f"  dry_run: {DRY_RUN}  |  límite: {LIMIT}")
    print(f"{'='*60}\n")

    data   = fi.load_data()
    leads  = data["leads_history"]
    tracking = load_tracking()
    already_sent = get_emails_sent(tracking, etapa=2)

    # Partners con email no enviados aún
    candidates = [
        l for l in leads
        if l.get("source_type", "") in fi.PARTNER_SOURCES
        and l.get("email", "—") not in ("—", "", None, "None")
        and l.get("email", "").split("@")[-1] not in SKIP_DOMAINS
        and l.get("email", "").split("@")[-1] not in FAKE_DOMAINS
        and l.get("email", "") not in FAKE_EMAILS
        and l.get("email", "") not in already_sent
    ]

    print(f"Partners con email:      {sum(1 for l in leads if l.get('source_type','') in fi.PARTNER_SOURCES and l.get('email','—') not in ('—','',None,'None'))}")
    print(f"Ya enviados (tracking):  {len(already_sent)}")
    print(f"Candidatos nuevos:       {len(candidates)}")
    print(f"Enviando hoy (cap={LIMIT}): {min(LIMIT, len(candidates))}\n")

    if not candidates:
        print("✅ Nada nuevo que enviar — todos los partners con email ya fueron contactados.")
        return

    enviados = 0
    for lead in candidates[:LIMIT]:
        email   = lead.get("email", "")
        raw_company = lead.get("company", "").strip()
        domain      = lead.get("domain", "")
        # Limpiar nombres que son dominios (www.xxx.com) o direcciones físicas
        if (raw_company.startswith("www.") or
                (raw_company.count(".") >= 1 and " " not in raw_company)):
            # Es un dominio — usar dominio limpio capitalizado
            company = domain.replace("www.", "").split(".")[0].capitalize()
        elif len(raw_company) > 80 or raw_company[:3].isdigit():
            # Parece una dirección física
            company = domain.replace("www.", "").split(".")[0].capitalize()
        else:
            company = raw_company or domain
        comp    = get_competitor(lead)
        body    = COPY_BY_COMPETITOR.get(comp, BODY_GENERICO)

        utm_content = comp.lower().replace(" ", "_")
        body_rendered = (body
            .replace("{empresa}", company)
            .replace("{competidor}", comp)
            .replace("{link_generico}", etendo_link(utm_content))
            .replace("{link_holded}",  etendo_link("holded"))
            .replace("{link_sage}",    etendo_link("sage"))
            .replace("{link_sap}",     etendo_link("sap"))
            .replace("{link_odoo}",    etendo_link("odoo"))
        ) + pixel_tag(email)

        print(f"  → {company[:40]:40s} | {email:35s} | [{comp}]")

        if not DRY_RUN:
            ok = send_via_n8n(email, SUBJECT, body_rendered, company)
            if ok:
                update_stage(tracking, email, 2,
                             canal="email", subject=SUBJECT,
                             enviado_por=GMAIL_USER,
                             fecha=datetime.now(timezone.utc).isoformat(),
                             empresa=company, dominio=lead.get("domain",""),
                             fuente="partner_outreach")
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
