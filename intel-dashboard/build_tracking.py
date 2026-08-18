"""
build_tracking.py — Genera/reconstruye outreach_tracking.json desde cero.
Fuentes: intel_data.json (score-3) + followup_sent_contactados.json
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def build():
    with open(ROOT / "intel_data.json") as f:
        data = json.load(f)
    leads_history = data.get('leads_history', [])
    followup_emails = set(json.loads((ROOT / "followup_sent_contactados.json").read_text()).get('sent_emails', []))

    # Dedup score-3 por email
    score3_map = {}
    for l in leads_history:
        if l.get('score', 0) == 3 and l.get('email', '—') not in ('—', ''):
            email = l['email']
            if email not in score3_map:
                score3_map[email] = l

    tracking = {}

    # Score-3 intel leads → etapa 0 por defecto
    for email, lead in score3_map.items():
        tracking[email] = {
            "email": email,
            "empresa": lead.get('company', '—'),
            "dominio": lead.get('domain', '—'),
            "sector": lead.get('sector', '—'),
            "signal": lead.get('signal_label', '—'),
            "score_intel": lead.get('score', 0),
            "fuente": "intel_dashboard",
            "etapa": 0,
            "etapa_label": "sin_contacto",
            "contactos": [],
            "notas": ""
        }

    # Followup emails → etapa 2
    for email in followup_emails:
        contactos_base = [
            {
                "etapa": 1,
                "canal": "whatsapp",
                "fecha": "desconocida",
                "enviado_por": "Victoria",
                "notas": "Confirmado por estar en lista followup"
            },
            {
                "etapa": 2,
                "canal": "email",
                "fecha": "desconocida",
                "subject": "followup_contactados",
                "enviado_por": "victoria.miguez@smfconsulting.es",
                "notas": "Confirmado via followup_sent_contactados.json"
            }
        ]
        if email in tracking:
            tracking[email]['etapa'] = 2
            tracking[email]['etapa_label'] = "followup_email_enviado"
            tracking[email]['contactos'] = contactos_base
        else:
            tracking[email] = {
                "email": email,
                "empresa": "—",
                "dominio": email.split('@')[-1] if '@' in email else '—',
                "sector": "—",
                "signal": "—",
                "score_intel": "—",
                "fuente": "followup_manual",
                "etapa": 2,
                "etapa_label": "followup_email_enviado",
                "contactos": contactos_base,
                "notas": "Origen desconocido — solo sabemos que recibió followup email"
            }

    out_path = ROOT / "outreach_tracking.json"
    out_path.write_text(json.dumps({
        "version": 1,
        "etapas": {
            "0": "sin_contacto",
            "1": "whatsapp_enviado",
            "2": "email1_enviado",
            "3": "followup_email_enviado",
            "4": "reunión_agendada",
            "5": "oportunidad_abierta",
            "99": "descartado"
        },
        "contactos": tracking
    }, indent=2, ensure_ascii=False))

    print(f"✅ outreach_tracking.json generado con {len(tracking)} contactos")
    resumen = {}
    for v in tracking.values():
        k = v['etapa_label']
        resumen[k] = resumen.get(k, 0) + 1
    for k, c in sorted(resumen.items()):
        print(f"   {k}: {c}")

if __name__ == "__main__":
    build()
