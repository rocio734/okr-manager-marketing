"""
outreach_tracking_helper.py
───────────────────────────
Módulo compartido para registrar y consultar el estado de outreach.

Etapas:
  0  sin_contacto          — Lead detectado, no contactado aún
  1  whatsapp_enviado      — Victoria envió WhatsApp
  2  email1_enviado        — Primer email enviado (send_outreach_score3.py)
  3  followup_email_enviado — Segundo email / followup enviado
  4  reunion_agendada      — Respuesta positiva, reunión confirmada
  5  oportunidad_abierta   — Deal abierto en CRM
  99 descartado            — No contactar más

Uso:
    from outreach_tracking_helper import load_tracking, save_tracking, update_stage, get_summary

    tracking = load_tracking()
    update_stage(tracking, email="x@y.com", nueva_etapa=2,
                 canal="email", subject="Tu ERP...", enviado_por="victoria.miguez@smfconsulting.es")
    save_tracking(tracking)
"""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT       = Path(__file__).resolve().parent
TRACK_FILE = ROOT / "outreach_tracking.json"

ETAPAS = {
    0:  "sin_contacto",
    1:  "whatsapp_enviado",
    2:  "email1_enviado",
    3:  "followup_email_enviado",
    4:  "reunion_agendada",
    5:  "oportunidad_abierta",
    99: "descartado",
}


def load_tracking() -> dict:
    """Carga outreach_tracking.json. Si no existe, devuelve estructura vacía."""
    if TRACK_FILE.exists():
        data = json.loads(TRACK_FILE.read_text(encoding="utf-8"))
        return data.get("contactos", {})
    return {}


def save_tracking(contactos: dict) -> None:
    """Guarda el dict de contactos en outreach_tracking.json."""
    payload = {
        "version": 1,
        "etapas":  ETAPAS,
        "contactos": contactos,
    }
    TRACK_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def ensure_lead(tracking: dict, email: str, **meta) -> None:
    """Crea entrada base si el email no existe aún."""
    if email not in tracking:
        tracking[email] = {
            "email":       email,
            "empresa":     meta.get("empresa", "—"),
            "dominio":     meta.get("dominio", email.split("@")[-1] if "@" in email else "—"),
            "sector":      meta.get("sector", "—"),
            "signal":      meta.get("signal", "—"),
            "score_intel": meta.get("score_intel", "—"),
            "fuente":      meta.get("fuente", "manual"),
            "etapa":       0,
            "etapa_label": ETAPAS[0],
            "contactos":   [],
            "notas":       "",
        }


def update_stage(tracking: dict, email: str, nueva_etapa: int,
                 canal: str = "", subject: str = "", enviado_por: str = "",
                 fecha: str = "", notas: str = "", **meta) -> None:
    """
    Registra un nuevo contacto y actualiza la etapa si es mayor a la actual.
    Llama a ensure_lead automáticamente si el email es nuevo.
    """
    ensure_lead(tracking, email, **meta)
    lead = tracking[email]

    ts = fecha or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lead["contactos"].append({
        "etapa":       nueva_etapa,
        "etapa_label": ETAPAS.get(nueva_etapa, str(nueva_etapa)),
        "canal":       canal,
        "subject":     subject,
        "enviado_por": enviado_por,
        "fecha":       ts,
        "notas":       notas,
    })

    # Solo avanzar etapa, nunca retroceder
    if nueva_etapa > lead.get("etapa", 0):
        lead["etapa"]       = nueva_etapa
        lead["etapa_label"] = ETAPAS.get(nueva_etapa, str(nueva_etapa))


def get_by_etapa(tracking: dict, etapa: int) -> list:
    """Devuelve lista de leads en una etapa específica."""
    return [v for v in tracking.values() if v.get("etapa") == etapa]


def get_emails_sent(tracking: dict, etapa: int) -> set:
    """Set de emails que YA alcanzaron o superaron la etapa indicada."""
    return {email for email, v in tracking.items() if v.get("etapa", 0) >= etapa}


def get_summary(tracking: dict) -> dict:
    """Resumen de cuántos leads hay en cada etapa."""
    counts = {v: 0 for v in ETAPAS.values()}
    for lead in tracking.values():
        label = lead.get("etapa_label", "sin_contacto")
        counts[label] = counts.get(label, 0) + 1
    return counts


def print_summary(tracking: dict) -> None:
    """Imprime el resumen de etapas en consola."""
    total = len(tracking)
    summary = get_summary(tracking)
    print(f"\n{'─'*50}")
    print(f"  OUTREACH TRACKING — {total} contactos totales")
    print(f"{'─'*50}")
    for etapa_id, etapa_label in ETAPAS.items():
        count = summary.get(etapa_label, 0)
        if count > 0:
            bar = "█" * min(count, 30)
            print(f"  [{etapa_id:2d}] {etapa_label:30s} {count:3d}  {bar}")
    print(f"{'─'*50}\n")
