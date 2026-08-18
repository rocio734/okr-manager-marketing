"""
Outreach a leads score 3 (usuarios DistritoK / TeamSystem / MS Dynamics).
Envía desde victoria.miguez@smfconsulting.es via Gmail SMTP.
Filtra duplicados por dominio usando un log local.
"""
import json, smtplib, os, time
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from outreach_tracking_helper import load_tracking, save_tracking, update_stage, get_emails_sent, print_summary

ROOT = Path(__file__).resolve().parent
ENV  = ROOT.parent.parent / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_PASSWORD", "")
LOG_FILE   = ROOT / "outreach_sent_score3.json"

SUBJECT_DK = "Tu ERP actual tiene fecha de caducidad"
SUBJECT_MS = "Una alternativa a Dynamics que tus equipos agradecerán"

BODY_DK = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.7;color:#1a1a1a;max-width:560px;">
<p>Hola,</p>

<p>Detectamos que vuestra empresa trabaja con DistritoK o TeamSystem. Son herramientas que funcionan, pero tienen un techo claro: no están pensadas para conectarse con el resto del stack tecnológico ni para automatizar procesos con IA.</p>

<p>Etendo es un ERP de código abierto diseñado exactamente para eso. Se integra con cualquier herramienta que ya uséis y permite conectar agentes de IA externos — como Claude o GPT — para automatizar tareas dentro del propio ERP: crear pedidos, consultar stock, gestionar facturas, todo desde lenguaje natural.</p>

<p>No es que "tenga IA". Es que se conecta a los modelos de IA que ya existen para que operen dentro de vuestros procesos.</p>

<p>Si os interesa ver cómo funciona en 20 minutos, con un caso de vuestro sector, estoy disponible esta semana.</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — <a href="https://etendo.software" style="color:#E85D04;">etendo.software</a></p>
</div>"""

BODY_MS = """\
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.7;color:#1a1a1a;max-width:560px;">
<p>Hola,</p>

<p>Muchas empresas que trabajan con Microsoft Dynamics nos contactan porque el coste de licencias y la dependencia del partner se va haciendo insostenible a medida que crecen.</p>

<p>Etendo es un ERP de código abierto — sin licencias por usuario, sin bloqueos de proveedor — y está diseñado para conectarse con agentes de IA externos. No es que "tenga IA integrada": se conecta a modelos como Claude o GPT para que operen dentro de vuestros flujos reales, automatizando tareas que hoy hace alguien manualmente.</p>

<p>Si tenéis curiosidad, podemos hacer una demo corta enfocada en vuestro sector. Sin compromiso.</p>

<p>Un saludo,<br>
<strong>Victoria Miguez</strong><br>
Etendo — <a href="https://etendo.software" style="color:#E85D04;">etendo.software</a></p>
</div>"""

FAKE_EMAILS = {"su@email.com", "test@test.com", "example@example.com"}


def load_sent():
    """Carga emails ya enviados desde el tracking centralizado (etapa >= 2)."""
    tracking = load_tracking()
    already = get_emails_sent(tracking, etapa=2)
    # compatibilidad con el log viejo si existe
    if LOG_FILE.exists():
        old = set(json.loads(LOG_FILE.read_text()).get("sent_emails", []))
        already |= old
    return already, tracking


def save_sent(tracking: dict) -> None:
    """Guarda el tracking centralizado."""
    save_tracking(tracking)


def send_email(to_email, subject, html_body, dry_run=False):
    if dry_run:
        print(f"  [DRY RUN] → {to_email} | {subject}")
        return True
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Victoria Miguez <{GMAIL_USER}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASS)
            smtp.send_message(msg)
        return True
    except Exception as e:
        print(f"  ✗ Error enviando a {to_email}: {e}")
        return False


def main(dry_run=False):
    with open(ROOT / "intel_data.json") as f:
        data = json.load(f)

    leads = data.get("leads_history", [])
    score3 = [
        l for l in leads
        if l.get("score", 0) == 3
        and l.get("email", "—") != "—"
        and l.get("email", "") not in FAKE_EMAILS
    ]

    # Dedup por email
    seen = set()
    score3_dedup = []
    for l in score3:
        if l["email"] not in seen:
            seen.add(l["email"])
            score3_dedup.append(l)

    sent_emails, tracking = load_sent()
    pending = [l for l in score3_dedup if l.get("email") not in sent_emails]

    print(f"Score 3 con email: {len(score3_dedup)}")
    print(f"Ya enviados:       {len(sent_emails)}")
    print(f"Pendientes:        {len(pending)}")
    print()

    ok, fail = 0, 0
    for lead in pending:
        email  = lead["email"]
        signal = lead.get("signal_label", "")
        domain = lead.get("domain", "")

        if "MS Dynamics" in signal:
            subject = SUBJECT_MS
            body    = BODY_MS
        else:
            subject = SUBJECT_DK
            body    = BODY_DK

        print(f"  → {domain:35s} {email:40s} {'[DRY]' if dry_run else ''}")
        success = send_email(email, subject, body, dry_run=dry_run)
        if success:
            ok += 1
            if not dry_run:
                update_stage(
                    tracking, email, nueva_etapa=2,
                    canal="email",
                    subject=subject,
                    enviado_por=GMAIL_USER,
                    empresa=lead.get("company", "—"),
                    dominio=domain,
                    sector=lead.get("sector", "—"),
                    signal=signal,
                    score_intel=lead.get("score", 0),
                    fuente="intel_dashboard",
                )
        else:
            fail += 1

        if not dry_run:
            time.sleep(3)  # 3s entre envíos para no disparar spam filters

    if not dry_run:
        save_sent(tracking)
        print_summary(tracking)

    print()
    print(f"✅ Enviados: {ok}  ✗ Fallidos: {fail}")


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    if dry:
        print("=== DRY RUN — no se envía nada ===\n")
    main(dry_run=dry)
