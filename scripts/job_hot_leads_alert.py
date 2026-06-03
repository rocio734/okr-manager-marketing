"""
Hot Leads Alert — corre los jueves a las 9am.
Le manda un mail a Vico con la lista de hot leads activos para que
confirme si el estado de cada uno es correcto antes del cálculo del viernes.
"""
import os, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from _etendo import etendo_login, etendo_fetch, APPROVER_EMAIL

ROLE_COMERCIAL = os.getenv("ETENDO_ROLE_ID", "8351131DFF384725AB08E06773FE6144")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", os.getenv("GMAIL_USER", ""))
SMTP_PASS = os.getenv("SMTP_PASS", os.getenv("GMAIL_PASSWORD", ""))
VICO_EMAIL = "victoria.miguez@etendo.software"


def get_hot_leads():
    jwt = etendo_login(ROLE_COMERCIAL)
    leads = etendo_fetch(jwt, "ECLM_Lead")
    return [
        l for l in leads
        if (l.get("scorePurchaseIntention") or 0) >= 2
        and l.get("strategicFit") == "strategic_fit_yes"
        and l.get("leadStatus") not in ("lost_deal", "won_deal", "cold_archived")
    ]


def build_html(hot_leads):
    rows = ""
    for l in hot_leads:
        name    = f"{l.get('firstName','')} {l.get('lastName','')}".strip() or l.get("company", "?")
        company = l.get("company", "—")
        spi     = l.get("scorePurchaseIntention", "?")
        status  = l.get("leadStatus", "?")
        summary = (l.get("summary") or "Sin notas").replace("\n", "<br>")[:300]
        rows += f"""
        <tr style="border-bottom:1px solid #ECEEF1;">
          <td style="padding:12px 16px;font-weight:600;color:#1F2858;">{name}</td>
          <td style="padding:12px 16px;color:#6B7388;">{company}</td>
          <td style="padding:12px 16px;text-align:center;">
            <span style="background:#FFF4BF;color:#1F2858;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700;">SPI={spi}</span>
          </td>
          <td style="padding:12px 16px;color:#6B7388;font-size:13px;">{summary}</td>
        </tr>"""

    today = datetime.now().strftime("%d/%m/%Y")
    return f"""
    <div style="font-family:Helvetica,Arial,sans-serif;max-width:700px;margin:0 auto;">
      <div style="background:#1F2858;padding:24px 32px;border-radius:10px 10px 0 0;">
        <h2 style="margin:0;color:#FFCC00;font-size:16px;letter-spacing:2px;text-transform:uppercase;">OKR Manager</h2>
        <h1 style="margin:8px 0 4px;color:#fff;font-size:20px;">Revisión de hot leads — {today}</h1>
        <p style="margin:0;color:rgba(255,255,255,0.6);font-size:13px;">
          Mañana el agente calcula los KRs. Revisá si el estado de cada lead es correcto.
        </p>
      </div>
      <div style="background:#fff;border:1px solid #ECEEF1;border-top:none;padding:24px 32px;">
        <p style="color:#2D3556;font-size:14px;">Hola Vico 👋</p>
        <p style="color:#2D3556;font-size:14px;">
          El sistema detectó <strong>{len(hot_leads)} hot leads activos</strong> que se van a usar mañana para calcular el KR de pipeline.
          Revisá cada uno y actualizá el estado en el CRM si corresponde (<em>cold_archived, negotiation, proposal_sent</em>, etc.)
          antes de las <strong>5pm</strong>.
        </p>
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;border:1px solid #ECEEF1;border-radius:8px;overflow:hidden;margin-top:16px;">
          <thead>
            <tr style="background:#F6F7F9;">
              <th style="padding:10px 16px;text-align:left;font-size:12px;color:#6B7388;font-weight:600;">NOMBRE</th>
              <th style="padding:10px 16px;text-align:left;font-size:12px;color:#6B7388;font-weight:600;">EMPRESA</th>
              <th style="padding:10px 16px;text-align:center;font-size:12px;color:#6B7388;font-weight:600;">SPI</th>
              <th style="padding:10px 16px;text-align:left;font-size:12px;color:#6B7388;font-weight:600;">ÚLTIMO MOVIMIENTO</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        <div style="background:#FFF8E6;border-left:4px solid #FFCC00;border-radius:0 8px 8px 0;padding:14px 18px;margin-top:20px;">
          <p style="margin:0;font-size:13px;color:#1F2858;">
            <strong>¿Cómo actualizar?</strong> Entrá al CRM de Etendo y cambiá el <em>Lead Status</em> de los que corresponda.
            Los cambios se van a reflejar automáticamente en el cálculo de mañana.
          </p>
        </div>
      </div>
    </div>"""


def send_email(html_body, n_leads):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"⚠️ Revisá {n_leads} hot leads antes del cálculo de KRs (mañana)"
    msg["From"]    = SMTP_USER
    msg["To"]      = VICO_EMAIL
    msg["Cc"]      = APPROVER_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, [VICO_EMAIL, APPROVER_EMAIL], msg.as_string())
    print(f"✓ Mail enviado a {VICO_EMAIL} con {n_leads} hot leads")


def main():
    print("=== Hot Leads Alert ===")
    hot = get_hot_leads()
    print(f"  Hot leads encontrados: {len(hot)}")
    if not hot:
        print("  Sin hot leads activos, no se envía mail.")
        return
    html = build_html(hot)
    send_email(html, len(hot))


if __name__ == "__main__":
    main()
