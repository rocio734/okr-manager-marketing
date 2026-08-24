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
MAX_PER_DAY   = 50
SLEEP_BETWEEN = 4
N8N_WEBHOOK   = "https://n8n.labs.etendo.cloud/webhook/a3f7c821-5d04-4b9e-8c31-0e72b49d6f15"
GMAIL_USER    = os.environ.get("GMAIL_USER", "victoria.miguez@smfconsulting.es")

DRY_RUN = "--dry-run" in sys.argv
LIMIT   = int(next((sys.argv[sys.argv.index("--limit")+1]
                    for i, a in enumerate(sys.argv) if a == "--limit"), MAX_PER_DAY))

FAKE_EMAILS  = {"su@email.com", "test@test.com", "example@example.com",
                "contacto@tuempresa.com", "info@tuempresa.com", "email@tudominio.com",
                "johnsmith@example.com", "jeff@paige.black",
                "nombre@example.com", "example@mail.com"}
FAKE_DOMAINS = {"example.com", "mailinator.com", "guerrillamail.com"}
SKIP_DOMAINS = {"gmail.com", "hotmail.com", "yahoo.com", "outlook.com",
                "yahoo.es", "hotmail.es", "live.com"}

# Dominios de grandes empresas globales — no son partners reales de Etendo
ENTERPRISE_DOMAINS = {
    "bain.com", "bcg.com", "kearney.com", "accenture.com", "atos.net",
    "capgemini.com", "cognizant.com", "deloitte.com", "dxc.com", "ey.com",
    "fujitsu.com", "hcltech.com", "alibabacloud.com", "citrix.com", "dell.com",
    "esri.com", "hitachivantara.com", "hpe.com", "huaweicloud.com", "intel.com",
    "lenovo.com", "lg.com", "es.nec.com", "nec.com", "netapp.com", "nutanix.com",
    "nvidia.com", "purestorage.com", "redhat.com", "supermicro.com", "vmware.com",
    "ibm.com", "microsoft.com", "oracle.com", "sap.com", "salesforce.com",
    "servicenow.com", "workday.com", "amazon.com", "google.com", "meta.com",
}

# Prefijos de email corporativo que no son la persona correcta para contactar
SKIP_EMAIL_PREFIXES = {
    "investor.relations", "investor", "pr@", "analystrelations", "inquiry@",
    "beian@", "centrodecontacto", "sitemanager", "hclfederal",
    "contact@", "contact.us", "info@nvidia", "name@",
}

def is_valid_partner_email(email: str, domain: str) -> bool:
    """Descarta emails de grandes empresas o contactos no comerciales."""
    if not email or "." not in email or "@" not in email:
        return False
    # Email que parece un nombre de archivo (ej: pro-new@2x.jpg)
    local, at_domain = email.split("@", 1)
    if any(at_domain.endswith(ext) for ext in (".jpg", ".png", ".gif", ".svg")):
        return False
    # Dominio de empresa gigante (comprobación exacta y subdominio)
    root_domain = ".".join(at_domain.split(".")[-2:])
    if root_domain in ENTERPRISE_DOMAINS or at_domain in ENTERPRISE_DOMAINS:
        return False
    # Dominio que termina en un fake domain (ej: yourcompany.example.com)
    if any(at_domain.endswith("." + fd) or at_domain == fd for fd in FAKE_DOMAINS):
        return False
    # Prefijo de email corporativo genérico
    email_lower = email.lower()
    if any(email_lower.startswith(p) or f"@{p}" in email_lower for p in SKIP_EMAIL_PREFIXES):
        return False
    return True


# Sufijos de tier de partner (Odoo, Holded, etc.) que se cuelan en el nombre
_TIER_SUFFIXES = ("Gold", "Silver", "Bronze", "Platinum", "Ready", "Enterprise",
                  "Partner", "Certified", "Official")

def clean_company_name(raw: str) -> str:
    """Elimina sufijos de tier pegados al nombre de empresa."""
    for suffix in _TIER_SUFFIXES:
        if raw.endswith(suffix):
            raw = raw[:-len(suffix)].strip()
    return raw

SUBJECT = "¿Tenéis clientes que piden poder hablar desde Claude a su ERP?"

# ── Copy por competidor ───────────────────────────────────────────────────────
# {empresa} = nombre de la consultora partner
# {competidor} = ERP que implementan (Holded, Sage, Odoo, etc.)

UTM_BASE = "utm_source=email&utm_medium=outreach&utm_campaign=partners_reventa"

def etendo_link(utm_content: str) -> str:
    return f"https://etendo.software?{UTM_BASE}&utm_content={utm_content}"

BODY_GENERICO = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>Cada vez más clientes lo preguntan: ¿podemos conectar Claude — o GPT — directamente al ERP para que gestione pedidos, facturas o consultas de stock sin entrar al sistema?</p>

<p>Etendo es un ERP Agéntico — diseñado desde la arquitectura para conectar agentes de IA externos directamente en los flujos del negocio. El cliente escribe en lenguaje natural y el agente opera dentro del ERP: crea documentos, consulta datos, gestiona procesos. Sin integraciones ad hoc ni desarrollo a medida.</p>

<p>Si tenéis clientes que empiezan a pedir esto, Etendo es algo que podría encajar en vuestro portfolio sin desplazar lo que ya implementáis.</p>

<p>¿Tendríais 20 minutos para ver una demo real de cómo funciona?</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — Canal de partners<br>
<a href="{link_generico}" style="color:#E85D04;">etendo.software</a></p>
</div>"""

BODY_ODOO = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>Trabajáis con Odoo — y seguro que algún cliente ya os ha preguntado si puede conectar Claude o GPT directamente al ERP para gestionar procesos desde lenguaje natural sin entrar al sistema.</p>

<p>Etendo es un ERP Agéntico diseñado específicamente para eso: conectar agentes de IA externos que operan dentro del ERP en tiempo real. No es una integración encima de Odoo — es una arquitectura diferente, pensada para los clientes que necesitan ese nivel de automatización.</p>

<p>Varios partners lo tienen en portfolio junto a Odoo para ese segmento específico. No es sustituir lo que ya hacéis: es tener respuesta cuando el cliente pide más.</p>

<p>¿Tendríais 20 minutos para ver una demo de cómo funciona en la práctica?</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — Canal de partners<br>
<a href="{link_odoo}" style="color:#E85D04;">etendo.software</a></p>
</div>"""

BODY_HOLDED = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>Sois partners de Holded — y hay un perfil de cliente que cada vez aparece más: empresas que quieren conectar Claude o GPT directamente al ERP para automatizar procesos desde lenguaje natural, algo que Holded no está diseñado para hacer.</p>

<p>Etendo es un ERP Agéntico — arquitectura pensada desde el principio para que agentes de IA externos operen dentro del sistema: crean pedidos, consultan stock, gestionan facturas sin que el usuario entre al ERP. Sin desarrollo a medida, sin integraciones frágiles.</p>

<p>Algunos partners lo posicionan para clientes que han crecido más allá de lo que Holded puede darles. No es reemplazar lo que ya hacéis: es tener respuesta para ese segmento.</p>

<p>¿Tendríais 20 minutos para ver una demo real?</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — Canal de partners<br>
<a href="{link_holded}" style="color:#E85D04;">etendo.software</a></p>
</div>"""

BODY_SAGE = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>Sois partners de Sage — y hay clientes que en algún momento preguntan algo que Sage no resuelve fácil: ¿podemos conectar Claude o GPT al ERP para que gestione procesos desde lenguaje natural sin entrar al sistema?</p>

<p>Etendo es un ERP Agéntico diseñado para eso. Los agentes de IA externos operan dentro del sistema directamente: crean documentos, consultan datos, gestionan flujos. Sin integraciones ad hoc ni desarrollo a medida.</p>

<p>No es sustituir lo que ya hacéis con Sage. Es tener respuesta para los clientes que piden ese nivel de automatización.</p>

<p>Si os interesa explorar, podemos hacer una llamada corta esta semana.</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — Canal de partners<br>
<a href="{link_sage}" style="color:#E85D04;">etendo.software</a></p>
</div>"""

BODY_SAP = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">
<p>Hola {empresa},</p>

<p>Trabajáis con SAP — y hay clientes mid-market donde la pregunta no es solo el coste, sino la capacidad de conectar Claude o GPT directamente al ERP para automatizar procesos desde lenguaje natural. SAP no hace eso de forma nativa ni accesible.</p>

<p>Etendo es un ERP Agéntico con arquitectura modular pensada para eso: agentes de IA externos que operan dentro del sistema, crean documentos, consultan datos y gestionan flujos sin desarrollo a medida. Con un modelo de partner que permite márgenes reales.</p>

<p>Si tenéis clientes donde SAP es demasiado para su momento, o donde la IA en el ERP es ya una conversación real, Etendo puede ser la respuesta que ya tenéis en portfolio.</p>

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
        and is_valid_partner_email(l.get("email", ""), l.get("domain", ""))
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
        raw_company = clean_company_name(lead.get("company", "").strip())
        domain      = lead.get("domain", "")
        # Limpiar nombres que son dominios (www.xxx.com) o direcciones físicas
        if (raw_company.startswith("www.") or
                (raw_company.count(".") >= 1 and " " not in raw_company)):
            company = domain.replace("www.", "").split(".")[0].capitalize()
        elif len(raw_company) > 80 or raw_company[:3].isdigit():
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
