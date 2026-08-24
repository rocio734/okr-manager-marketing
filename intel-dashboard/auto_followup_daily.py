"""
auto_followup_daily.py — Follow-up automático diario a leads que no respondieron.

Lee outreach_tracking.json, busca contactos en etapa 2 (email enviado) que llevan
MIN_DAYS_WAIT días sin respuesta, y envía hasta MAX_PER_DAY follow-ups.

Crontab (lun–vie 10:00, después del outreach de 09:30):
  0 10 * * 1-5 python3 /home/rocio/prueba/okr_manager_site/intel-dashboard/auto_followup_daily.py >> /home/rocio/prueba/logs/auto_followup_$(date +%Y%m%d).log 2>&1

Flags:
  --dry-run    Ver qué enviaría sin enviar nada
  --limit N    Cambia el cap diario (default: 10)
  --days N     Cambia el umbral de espera (default: 5)
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

MAX_PER_DAY  = 30
MIN_DAYS_WAIT = 3        # días mínimos desde el primer contacto
SLEEP_BETWEEN = 4
N8N_WEBHOOK  = "https://n8n.labs.etendo.cloud/webhook/a3f7c821-5d04-4b9e-8c31-0e72b49d6f15"
GMAIL_USER   = os.environ.get("GMAIL_USER", "victoria.miguez@smfconsulting.es")
PIXEL_BASE   = "https://etendo-dashboard-api.onrender.com/pixel"
UTM_BASE     = "utm_source=email&utm_medium=followup&utm_campaign=followup_auto"

# Dolor concreto por sector → lo que se pierde sin actuar
SECTOR_PAIN = {
    "Consultoría":  ("Vuestros consultores dedican horas a introducir datos en el ERP que el cliente ya tiene en otros sistemas.", "conectar esos flujos con IA y ejecutarlos desde lenguaje natural"),
    "Logística":    ("Cada pedido urgente requiere abrir el ERP, buscar stock, aprobar salida — pasos manuales que frenan la operación.", "gestionar expediciones y stock diciéndole a Claude lo que necesitáis"),
    "Construcción": ("Aprobar una orden de compra en obra depende de que alguien esté delante del ERP. Eso retrasa proyectos.", "aprobar pedidos y gestionar subcontratas desde el móvil, en lenguaje natural"),
    "Industrial":   ("La producción para porque alguien tiene que actualizar órdenes en el ERP. Eso no debería depender de una persona.", "que el ERP actualice órdenes de producción y compras solo, sin intervención manual"),
    "Servicios":    ("El equipo pierde tiempo navegando el ERP para tareas que podrían resolverse en segundos.", "que cualquier persona de vuestro equipo opere el ERP hablándole a Claude"),
    "Distribución": ("Gestionar pedidos de múltiples canales en el ERP requiere demasiados clics y demasiado tiempo.", "centralizar pedidos y stock con agentes que actúan solos dentro del ERP"),
    "Fabricación":  ("Sincronizar producción, compras y almacén manualmente en el ERP introduce errores y retrasos.", "que producción, compras y almacén se sincronicen solos a través de IA"),
    "Alimentación": ("Los pedidos de distribución llegan por canales distintos y alguien tiene que pasarlos al ERP uno a uno.", "automatizar la entrada de pedidos y el control de stock con agentes de IA"),
    "Madera":       ("Controlar inventario de madera y gestionar órdenes de corte manualmente en el ERP genera errores costosos.", "gestionar stock y órdenes de producción desde lenguaje natural"),
}
DEFAULT_PAIN = ("Vuestra operación depende de que alguien esté delante del ERP para cada tarea repetitiva.", "que esas tareas se ejecuten solas a través de agentes de IA conectados al ERP")

def get_pain(sector: str) -> tuple:
    if not sector:
        return DEFAULT_PAIN
    for key, pain in SECTOR_PAIN.items():
        if key.lower() in sector.lower():
            return pain
    return DEFAULT_PAIN

def pixel_tag(email: str) -> str:
    enc = urllib.parse.quote(email, safe="")
    return f'<img src="{PIXEL_BASE}/{enc}.gif?t=followup" width="1" height="1" style="display:none" alt="">'

def etendo_link(utm_content: str) -> str:
    return f"https://etendo.software?{UTM_BASE}&utm_content={urllib.parse.quote(utm_content, safe='')}"

def build_body(empresa: str, sector: str, original_subject: str, email: str = "") -> str:
    pain, solution = get_pain(sector)
    link = etendo_link(empresa)
    return f"""\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.8;color:#1a1a1a;max-width:560px;">

<p>Hola,</p>

<p>Os escribí hace unos días y quería insistir porque creo que tiene sentido para vosotros.</p>

<p><strong>{pain}</strong></p>

<p>Etendo es un ERP de código abierto que resuelve exactamente eso: permite {solution}. Sin cambiar vuestro sistema actual, sin integraciones complejas.</p>

<p>Esta semana tenemos hueco para una demo de 20 minutos con un caso real de vuestro sector. Sin presentación de ventas — vais a ver el sistema funcionando en directo.</p>

<p>¿Os viene bien el miércoles o jueves por la mañana? Respondedme con el día que mejor os encaje y os mando la invitación.</p>

<p style="margin-top:24px">Un saludo,<br>
<strong>Victoria</strong><br>
<span style="color:#666;font-size:13px">Etendo — <a href="{link}" style="color:#E85D04;text-decoration:none">etendo.software</a></span></p>

</div>
{pixel_tag(email) if email else ""}"""

def send_email(to_email: str, subject: str, html_body: str, dry_run=False) -> bool:
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
        print(f"    ❌ Error: {e}")
        return False

def load_tracking():
    tf = ROOT / "outreach_tracking.json"
    if not tf.exists():
        return {}
    data = json.loads(tf.read_text(encoding="utf-8"))
    return data.get("contactos", data) if "contactos" in data else data

def save_tracking(tracking):
    tf = ROOT / "outreach_tracking.json"
    if tf.exists():
        data = json.loads(tf.read_text(encoding="utf-8"))
        if "contactos" in data:
            data["contactos"] = tracking
            tf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return
    tf.write_text(json.dumps(tracking, indent=2, ensure_ascii=False), encoding="utf-8")

def days_since(fecha_str: str) -> int:
    if not fecha_str or fecha_str == "desconocida":
        return 99
    try:
        fecha = datetime.fromisoformat(fecha_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - fecha).days
    except Exception:
        return 99

def main():
    dry_run    = "--dry-run" in sys.argv
    limit_arg  = next((int(sys.argv[i+1]) for i, a in enumerate(sys.argv)
                       if a == "--limit" and i+1 < len(sys.argv)), None)
    days_arg   = next((int(sys.argv[i+1]) for i, a in enumerate(sys.argv)
                       if a == "--days" and i+1 < len(sys.argv)), None)
    max_hoy    = limit_arg or MAX_PER_DAY
    min_days   = days_arg  or MIN_DAYS_WAIT

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*60}")
    print(f"  AUTO FOLLOW-UP DIARIO — {now}")
    print(f"  Cap: {max_hoy} | Umbral: >= {min_days} días | dry_run: {dry_run}")
    print(f"{'='*60}\n")

    tracking = load_tracking()
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Leads en etapa 2 con >= min_days desde el primer contacto
    candidatos = []
    for email, c in tracking.items():
        if c.get("etapa") != 2:
            continue
        if email == "rocio.altamirano@smfconsulting.es":
            continue
        contactos_list = c.get("contactos", [])
        primera_fecha = contactos_list[0].get("fecha", "") if contactos_list else ""
        dias = days_since(primera_fecha)
        if dias >= min_days:
            orig_subject = contactos_list[0].get("subject", "Etendo ERP") if contactos_list else "Etendo ERP"
            candidatos.append({
                "email":   email,
                "empresa": c.get("empresa", ""),
                "sector":  c.get("sector", ""),
                "dias":    dias,
                "subject": orig_subject,
            })

    candidatos.sort(key=lambda x: x["dias"], reverse=True)  # más antiguos primero

    print(f"Etapa 2 totales           : {sum(1 for c in tracking.values() if c.get('etapa')==2)}")
    print(f"Listos para follow-up     : {len(candidatos)}  (>= {min_days} días sin respuesta)")
    print(f"Enviando hoy (cap {max_hoy})    : {min(len(candidatos), max_hoy)}")
    print()

    if not candidatos:
        print("✅ Ningún lead listo para follow-up hoy.")
        return

    lote = candidatos[:max_hoy]
    ok, fail = 0, 0

    for lead in lote:
        email   = lead["email"]
        empresa = lead["empresa"] or email.split("@")[1]
        sector  = lead["sector"]
        dias    = lead["dias"]
        subject = f"Re: {lead['subject']}"
        body    = build_body(empresa, sector, lead["subject"], email=email)

        print(f"  [{dias:3d}d] {empresa[:35]:35s} | {email}")
        success = send_email(email, subject, body, dry_run=dry_run)

        if success:
            ok += 1
            if not dry_run:
                tracking[email]["etapa"]       = 3
                tracking[email]["etapa_label"] = "followup_email_enviado"
                tracking[email].setdefault("contactos", []).append({
                    "etapa":       3,
                    "etapa_label": "followup_email_enviado",
                    "canal":       "email_n8n",
                    "subject":     subject,
                    "fecha":       now_ts,
                    "enviado_por": GMAIL_USER,
                    "notas":       "Follow-up automático por auto_followup_daily.py",
                })
        else:
            fail += 1

        if not dry_run:
            time.sleep(SLEEP_BETWEEN)

    if not dry_run and ok:
        save_tracking(tracking)

    pendientes_restantes = max(0, len(candidatos) - max_hoy)
    print(f"\n{'─'*60}")
    print(f"  ✅ Enviados: {ok}  |  ❌ Fallidos: {fail}")
    if pendientes_restantes > 0:
        print(f"  📋 Quedan {pendientes_restantes} para días siguientes")
    print(f"{'─'*60}\n")

if __name__ == "__main__":
    main()
