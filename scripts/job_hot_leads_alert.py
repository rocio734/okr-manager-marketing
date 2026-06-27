"""
Hot Leads Alert — corre los jueves a las 9am.
Le manda un mail a Vico con la lista de hot leads activos para que
confirme si el estado de cada uno es correcto antes del cálculo del viernes.
"""
import os, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from _etendo import etendo_sid_login, etendo_fetch_crm, APPROVER_EMAIL
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", os.getenv("GMAIL_USER", ""))
SMTP_PASS = os.getenv("SMTP_PASS", os.getenv("GMAIL_PASSWORD", ""))
VICO_EMAIL = "victoria.miguez@etendo.software"


def get_hot_leads():
    sid = etendo_sid_login()
    leads = etendo_fetch_crm(sid, "ETCRM_Lead")
    # Activos = no Dead y no Converted
    all_active = [l for l in leads
                  if (l.get("leadStatus$_identifier") or "") not in ("Dead", "Converted")]
    # Hot = status Qualified (oportunidad de venta validada)
    hot = [l for l in all_active
           if (l.get("leadStatus$_identifier") or "") == "Qualified"]
    # Cerca de negociación: keywords en description o successProbability alto
    _near_negotiation_kw = ["propuesta", "demo realiz", "demo hecha", "llamada exitosa",
                             "negociaci", "evaluando", "revisando", "avanzando", "cierre",
                             "reunión de", "presentamos", "acuerdo"]
    near_neg = [
        l for l in all_active
        if (any(k in (l.get("description") or "").lower() for k in _near_negotiation_kw)
            or float(l.get("successProbability") or 0) >= 50)
        and l not in hot
    ]
    return hot, near_neg


def build_html(hot_leads, near_neg=None):
    near_neg = near_neg or []
    today = datetime.now().strftime("%d/%m/%Y")

    spi_styles = {
        3: ("background:#FCE5E8;color:#D02F3D;", "🔴"),
        2: ("background:#FFF1C9;color:#B5810D;", "🟡"),
        1: ("background:#E1F3EA;color:#1A8554;", "🟢"),
    }

    cards = ""
    for l in hot_leads:
        name    = f"{l.get('firstName', '')} {l.get('lastName', '')}".strip() or l.get("company", "?")
        company = l.get("company") or "—"
        prob    = int(float(l.get("successProbability") or 0))
        summary = (l.get("description") or "").strip()
        lines   = [x.strip() for x in summary.split("\n") if x.strip()]
        last    = lines[-1] if lines else "Sin notas recientes"
        spi_css = "background:#FCE5E8;color:#D02F3D;" if prob >= 50 else "background:#FFF1C9;color:#B5810D;"
        fire    = "🔴" if prob >= 50 else "🟡"

        cards += f"""
<div style="background:#ffffff;border:1px solid #ECEEF1;border-radius:12px;padding:18px 22px;margin-bottom:14px;box-shadow:0 2px 8px rgba(31,40,88,0.06);">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="vertical-align:top;">
        <div style="font-size:15px;font-weight:700;color:#1F2858;">{fire} {name}</div>
        <div style="font-size:12px;color:#9098A8;margin-top:2px;">{company}</div>
      </td>
      <td align="right" style="vertical-align:top;">
        <span style="{spi_css}padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;">Prob {prob}%</span>
      </td>
    </tr>
    <tr>
      <td colspan="2" style="padding-top:12px;">
        <div style="font-size:11px;font-weight:700;color:#9098A8;letter-spacing:1px;text-transform:uppercase;margin-bottom:5px;">ÚLTIMO MOVIMIENTO</div>
        <div style="font-size:13px;color:#2D3556;background:#F6F7F9;border-radius:8px;padding:10px 14px;line-height:1.55;">{last}</div>
      </td>
    </tr>
    <tr>
      <td colspan="2" style="padding-top:12px;">
        <div style="font-size:11px;font-weight:700;color:#9098A8;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;">MARCALO EN ETENDO COMO:</div>
        <table cellpadding="0" cellspacing="0">
          <tr>
            <td style="padding-right:6px;"><span style="background:#E1F3EA;color:#1A8554;padding:5px 12px;border-radius:20px;font-size:12px;font-weight:600;white-space:nowrap;">✓ Sigue igual</span></td>
            <td style="padding-right:6px;"><span style="background:#FFF1C9;color:#B5810D;padding:5px 12px;border-radius:20px;font-size:12px;font-weight:600;white-space:nowrap;">📄 Propuesta enviada</span></td>
            <td style="padding-right:6px;"><span style="background:#E4ECFB;color:#2D5BCF;padding:5px 12px;border-radius:20px;font-size:12px;font-weight:600;white-space:nowrap;">🤝 Negociación</span></td>
            <td><span style="background:#EEF0F4;color:#6B7388;padding:5px 12px;border-radius:20px;font-size:12px;font-weight:600;white-space:nowrap;">❌ Archivar</span></td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</div>"""

    # Negotiation candidates section
    if near_neg:
        neg_rows = "".join(
            f'<div style="font-size:13px;color:#2D3556;padding:4px 0;border-bottom:1px solid #E9EBF3;">'
            f'· <strong>{((l.get("firstName") or "") + " " + (l.get("lastName") or "")).strip() or l.get("company","?")}</strong>'
            f' — {(l.get("description") or "")[-80:].strip()}</div>'
            for l in near_neg[:5]
        )
        neg_section = f"""<div style="background:#fff;border-radius:8px;padding:10px 14px;margin-bottom:10px;">
          <div style="font-size:11px;color:#6B7388;font-weight:600;margin-bottom:6px;">LEADS CON ACTIVIDAD RECIENTE QUE PODRÍAN ESTAR AVANZANDO:</div>
          {neg_rows}
        </div>"""
    else:
        neg_section = '<p style="margin:0 0 8px;font-size:13px;color:#9098A8;">El sistema no detectó leads en etapa avanzada fuera de los hot leads de arriba.</p>'

    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F6F7F9;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F6F7F9;padding:28px 16px;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%;">

  <tr><td style="background:#1F2858;border-radius:12px 12px 0 0;padding:24px 32px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td>
        <div style="font-size:11px;color:#FFCC00;font-weight:700;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;">OKR Manager · Revenue Org</div>
        <h1 style="margin:0 0 6px;font-size:20px;font-weight:800;color:#ffffff;line-height:1.2;">Revisá los hot leads antes del viernes</h1>
        <p style="margin:0;font-size:13px;color:rgba(255,255,255,0.6);">{today} · El agente calcula mañana a las 17hs</p>
      </td>
      <td align="right" style="vertical-align:middle;">
        <span style="background:#FFCC00;color:#1F2858;font-size:11px;font-weight:700;padding:6px 14px;border-radius:20px;white-space:nowrap;">⏰ ACCIÓN HOY</span>
      </td>
    </tr></table>
  </td></tr>

  <tr><td style="background:#ffffff;padding:20px 32px 16px;border-left:1px solid #ECEEF1;border-right:1px solid #ECEEF1;">
    <p style="margin:0;font-size:14px;color:#2D3556;line-height:1.65;">
      Hola Vico 👋 El sistema encontró <strong style="color:#1F2858;">{len(hot_leads)} hot leads activos</strong> (status Qualified) que se usan mañana para calcular el KR de pipeline.
      Revisá cada uno y <strong>actualizá el estado en Etendo CRM</strong> si algo cambió — antes de las 17hs.
    </p>
  </td></tr>

  <tr><td style="background:#F6F7F9;padding:0 32px 16px;border-left:1px solid #ECEEF1;border-right:1px solid #ECEEF1;">
    {cards}
  </td></tr>

  <tr><td style="background:#ffffff;padding:16px 32px 0;border-left:1px solid #ECEEF1;border-right:1px solid #ECEEF1;">
    <div style="background:#FFF8E6;border-left:4px solid #FFCC00;border-radius:0 10px 10px 0;padding:14px 18px;">
      <div style="font-size:11px;font-weight:700;color:#B5810D;letter-spacing:1px;text-transform:uppercase;margin-bottom:6px;">¿CÓMO ACTUALIZAR?</div>
      <p style="margin:0;font-size:13px;color:#1F2858;line-height:1.6;">
        Entrá a <strong>Etendo CRM → Lead</strong> y cambiá el <em>Lead Status</em> del que corresponda. Los cambios se reflejan automáticamente en el cálculo de mañana.
      </p>
    </div>
  </td></tr>

  <tr><td style="background:#ffffff;padding:20px 32px;border-left:1px solid #ECEEF1;border-right:1px solid #ECEEF1;">
    <div style="background:#F0F4FF;border-left:4px solid #2D5BCF;border-radius:0 10px 10px 0;padding:16px 20px;">
      <div style="font-size:11px;font-weight:700;color:#2D5BCF;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;">🤝 Consulta adicional — Leads en negociación</div>
      <p style="margin:0 0 12px;font-size:13px;color:#1F2858;line-height:1.6;">
        El sistema usa el CRM para detectar si hay leads en negociación, pero sabemos que no siempre está actualizado.
        <strong>¿Hay algún lead con el que estés en proceso de negociación activa o avanzado que no figure arriba?</strong>
      </p>
      {neg_section}
      <p style="margin:8px 0 0;font-size:12px;color:#9098A8;">
        Si hay alguno, respondé este mail con el nombre y el estado actual — el agente del viernes lo va a tener en cuenta.
      </p>
    </div>
  </td></tr>

  <tr><td style="background:#1F2858;border-radius:0 0 12px 12px;padding:14px 32px;">
    <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.4);">Este mail se envía automáticamente todos los jueves a las 9am · OKR Manager</p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""


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
    hot, near_neg = get_hot_leads()
    print(f"  Hot leads encontrados: {len(hot)}")
    print(f"  Leads cerca de negociación: {len(near_neg)}")
    if not hot:
        print("  Sin hot leads activos, no se envía mail.")
        return
    html = build_html(hot, near_neg)
    send_email(html, len(hot))


if __name__ == "__main__":
    main()
