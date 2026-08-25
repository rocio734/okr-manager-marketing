#!/usr/bin/env python3
"""
Job Visión del Mercado — OKR Manager.
Analiza competencia en Google (SerpAPI), estado del mercado ERP en España,
y genera acciones recomendadas. Guarda resultado en Supabase (tabla market_intel).
También envía un email resumen a los destinatarios configurados.

Uso:
  python3 job_market_intel.py
  python3 job_market_intel.py --team marketing
"""
import argparse, json, os, sys, urllib.request, urllib.parse
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _etendo import llm_call, sb_request

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

SMTP_USER   = os.environ.get("SMTP_USER",  os.environ.get("GMAIL_USER", ""))
SMTP_PASS   = os.environ.get("SMTP_PASS",  os.environ.get("GMAIL_PASSWORD", ""))
RECIPIENTS  = ["rocio.altamirano@smfconsulting.es", "victoria.miguez@smfconsulting.es"]

COMPETITORS = [
    {"name": "Odoo",     "domain": "odoo.com"},
    {"name": "Holded",   "domain": "holded.com"},
    {"name": "Sage",     "domain": "sage.com"},
    {"name": "A3ERP",    "domain": "a3software.com"},
    {"name": "SAP B1",   "domain": "sap.com"},
]

QUERIES = [
    ("erp pymes españa",          "es", "Demanda ES"),
    ("mejor erp empresa mediana", "es", "Demanda ES"),
    ("erp verifactu 2026",        "es", "Verifactu"),
    ("erp con inteligencia artificial", "es", "AI ERP"),
    ("odoo vs alternativas erp",  "es", "Competencia directa"),
    ("etendo erp",                "es", "Brand"),
]


def serp_search(query, country="es"):
    if not SERPAPI_KEY:
        return {}
    params = urllib.parse.urlencode({
        "q": query, "hl": "es", "gl": country,
        "num": 10, "api_key": SERPAPI_KEY
    })
    req = urllib.request.Request(f"https://serpapi.com/search.json?{params}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  [SERP] Error: {e}")
        return {}


def analyze_serp(data, query):
    organic = data.get("organic_results", [])
    ads     = data.get("ads", [])
    result  = {
        "query": query,
        "etendo_position": None,
        "etendo_in_ads": False,
        "competitors": [],
        "ads_count": len(ads),
        "top5": []
    }
    for i, r in enumerate(organic[:5], 1):
        link  = r.get("link", "").lower()
        title = r.get("title", "")
        result["top5"].append({"pos": i, "title": title[:80], "link": link})
        if "etendo" in link:
            result["etendo_position"] = i
        for c in COMPETITORS:
            if c["domain"] in link and c["name"] not in result["competitors"]:
                result["competitors"].append(c["name"])
    for ad in ads:
        if "etendo" in ad.get("link", "").lower():
            result["etendo_in_ads"] = True
    return result


def send_intel_email(analysis: dict, serp_results: list, today: str) -> None:
    """Envía el resumen de inteligencia competitiva por email."""
    if not SMTP_USER or not SMTP_PASS:
        print("  ⚠️  Sin credenciales SMTP — email omitido")
        return

    # ── Construir HTML ────────────────────────────────────────────────────────
    resumen   = analysis.get("resumen_ejecutivo", "—")
    pos       = analysis.get("posicion_etendo", {})
    fortalezas = pos.get("fortalezas", [])
    brechas    = pos.get("brechas", [])
    competidores = analysis.get("competidores", [])
    oportunidades = analysis.get("oportunidades", [])
    acciones  = analysis.get("acciones_semana", [])

    amenaza_color = {"alta": "#c0392b", "media": "#e67e22", "baja": "#27ae60"}
    urgencia_color = {"hoy": "#c0392b", "esta_semana": "#e67e22", "este_mes": "#2980b9"}

    def badge(val, cmap, default="#666"):
        color = cmap.get(val, default)
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:bold">{val}</span>'

    comp_rows = "".join(
        f"""<tr>
          <td style="padding:8px 12px;font-weight:bold">{c.get('nombre','?')}</td>
          <td style="padding:8px 12px">{badge(c.get('amenaza',''), amenaza_color)}</td>
          <td style="padding:8px 12px;font-size:13px;color:#444">{c.get('movimiento_detectado','')}</td>
          <td style="padding:8px 12px;font-size:13px;color:#555">{c.get('diferenciador_vs_etendo','')}</td>
        </tr>""" for c in competidores
    )

    opp_rows = "".join(
        f"""<tr>
          <td style="padding:8px 12px;font-size:13px">{o.get('descripcion','')}</td>
          <td style="padding:8px 12px;font-size:12px;color:#2980b9;font-weight:bold">{o.get('canal','')}</td>
          <td style="padding:8px 12px">{badge(o.get('impacto',''), {'alto':'#27ae60','medio':'#e67e22','bajo':'#95a5a6'})}</td>
          <td style="padding:8px 12px;font-size:12px;color:#333">{o.get('accion_concreta','')}</td>
        </tr>""" for o in oportunidades
    )

    acc_rows = "".join(
        f"""<tr>
          <td style="padding:8px 12px;font-size:13px">{a.get('accion','')}</td>
          <td style="padding:8px 12px;font-size:12px;color:#555">{a.get('responsable','')}</td>
          <td style="padding:8px 12px">{badge(a.get('urgencia',''), urgencia_color)}</td>
        </tr>""" for a in acciones
    )

    # Posición SERP de Etendo
    serp_rows = "".join(
        f"""<tr>
          <td style="padding:6px 10px;font-size:12px;color:#555">{r.get('label','')}</td>
          <td style="padding:6px 10px;font-size:12px">{r.get('query','')}</td>
          <td style="padding:6px 10px;font-size:12px;font-weight:bold;color:{'#27ae60' if r.get('etendo_position') else '#c0392b'}">
            {'#' + str(r['etendo_position']) if r.get('etendo_position') else 'fuera top10'}
          </td>
          <td style="padding:6px 10px;font-size:11px;color:#777">{', '.join(r.get('competitors', [])) or '—'}</td>
        </tr>""" for r in serp_results
    )

    body_html = f"""
<div style="font-family:Arial,sans-serif;max-width:750px;margin:0 auto;color:#222">
  <div style="background:#1a1a2e;color:#fff;padding:20px 24px;border-radius:8px 8px 0 0">
    <h2 style="margin:0;font-size:20px">🔍 Inteligencia Competitiva — {today}</h2>
    <p style="margin:6px 0 0;color:#aaa;font-size:13px">Etendo · Análisis diario de mercado ERP España</p>
  </div>

  <div style="background:#f0f4ff;padding:16px 24px;border-left:4px solid #3498db">
    <h3 style="margin:0 0 8px;font-size:14px;color:#2c3e50;text-transform:uppercase;letter-spacing:1px">Resumen ejecutivo</h3>
    <p style="margin:0;font-size:14px;line-height:1.6">{resumen}</p>
  </div>

  <div style="padding:16px 24px">
    <h3 style="font-size:14px;color:#2c3e50;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">Posición Etendo</h3>
    <div style="display:inline-block;vertical-align:top;width:48%;margin-right:2%">
      <p style="font-size:12px;color:#27ae60;font-weight:bold;margin:0 0 4px">✅ Fortalezas</p>
      <ul style="margin:0;padding-left:16px;font-size:13px;line-height:1.8">
        {''.join(f'<li>{f}</li>' for f in fortalezas)}
      </ul>
    </div>
    <div style="display:inline-block;vertical-align:top;width:48%">
      <p style="font-size:12px;color:#e74c3c;font-weight:bold;margin:0 0 4px">⚠️ Brechas</p>
      <ul style="margin:0;padding-left:16px;font-size:13px;line-height:1.8">
        {''.join(f'<li>{b}</li>' for b in brechas)}
      </ul>
    </div>
  </div>

  <div style="padding:0 24px 16px">
    <h3 style="font-size:14px;color:#2c3e50;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">Competidores</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead style="background:#f5f5f5">
        <tr>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Competidor</th>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Amenaza</th>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Movimiento</th>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Diferenciador Etendo</th>
        </tr>
      </thead>
      <tbody>{comp_rows}</tbody>
    </table>
  </div>

  <div style="padding:0 24px 16px">
    <h3 style="font-size:14px;color:#2c3e50;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">Oportunidades</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead style="background:#f5f5f5">
        <tr>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Oportunidad</th>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Canal</th>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Impacto</th>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Acción concreta</th>
        </tr>
      </thead>
      <tbody>{opp_rows}</tbody>
    </table>
  </div>

  <div style="padding:0 24px 16px">
    <h3 style="font-size:14px;color:#2c3e50;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">🎯 Acciones esta semana</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead style="background:#f5f5f5">
        <tr>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Acción</th>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Responsable</th>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Urgencia</th>
        </tr>
      </thead>
      <tbody>{acc_rows}</tbody>
    </table>
  </div>

  <details style="padding:0 24px 16px">
    <summary style="font-size:13px;color:#888;cursor:pointer">Ver datos SERP</summary>
    <table style="width:100%;border-collapse:collapse;margin-top:8px">
      <thead style="background:#f5f5f5">
        <tr>
          <th style="padding:6px 10px;text-align:left;font-size:11px;color:#666">Label</th>
          <th style="padding:6px 10px;text-align:left;font-size:11px;color:#666">Query</th>
          <th style="padding:6px 10px;text-align:left;font-size:11px;color:#666">Pos. Etendo</th>
          <th style="padding:6px 10px;text-align:left;font-size:11px;color:#666">Competidores top10</th>
        </tr>
      </thead>
      <tbody>{serp_rows}</tbody>
    </table>
  </details>

  <div style="background:#f9f9f9;padding:12px 24px;border-top:1px solid #eee;font-size:11px;color:#999;border-radius:0 0 8px 8px">
    Generado automáticamente por job_market_intel.py · Etendo Revenue Org
  </div>
</div>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🔍 Inteligencia Competitiva — {today}"
    msg["From"]    = f"Etendo Intel <{SMTP_USER}>"
    msg["To"]      = ", ".join(RECIPIENTS)
    msg.attach(MIMEText(resumen, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.ehlo(); s.starttls(); s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, RECIPIENTS, msg.as_string())
        print(f"  ✓ Email enviado a {', '.join(RECIPIENTS)}")
    except Exception as e:
        print(f"  ✗ Error enviando email: {e}")


def run(team="marketing"):
    print(f"=== Visión del Mercado — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    # 1) Análisis SERP por query
    serp_results = []
    for query, country, label in QUERIES:
        print(f"  SERP: {query!r} ({country})...")
        data    = serp_search(query, country)
        analysis = analyze_serp(data, query)
        analysis["label"] = label
        serp_results.append(analysis)

    # 2) Construir contexto para LLM
    serp_summary = "\n".join([
        f"- [{r['label']}] '{r['query']}': Etendo pos={r['etendo_position'] or 'fuera top10'}, "
        f"competidores={r['competitors'] or 'ninguno en top10'}, "
        f"top5: {[x['title'][:50] for x in r['top5']]}"
        for r in serp_results
    ])

    prompt = f"""Eres analista de inteligencia competitiva para Etendo, un ERP Agentic para pymes españolas.

DATOS DEL MERCADO HOY ({datetime.now().strftime('%d/%m/%Y')}):
{serp_summary}

COMPETIDORES PRINCIPALES: Odoo (open source, market leader), Holded (cloud ES), Sage (legacy), A3ERP (contabilidad ES), SAP B1 (enterprise).

POSICIONAMIENTO DE ETENDO: "Agentic ERP" — el único ERP que ejecuta acciones con agentes IA. Foco: pymes España, verifactu, automatización de procesos.

Con estos datos, genera un análisis estructurado en JSON con esta estructura exacta:
{{
  "resumen_ejecutivo": "2-3 frases sobre el estado del mercado hoy",
  "posicion_etendo": {{
    "fortalezas": ["...", "..."],
    "brechas": ["...", "..."]
  }},
  "competidores": [
    {{
      "nombre": "...",
      "amenaza": "alta|media|baja",
      "movimiento_detectado": "...",
      "diferenciador_vs_etendo": "..."
    }}
  ],
  "oportunidades": [
    {{
      "descripcion": "...",
      "canal": "SEO|Paid|LinkedIn|Contenido|Outbound",
      "impacto": "alto|medio|bajo",
      "accion_concreta": "..."
    }}
  ],
  "acciones_semana": [
    {{
      "accion": "...",
      "responsable": "Marketing|Comercial|Producto",
      "urgencia": "hoy|esta_semana|este_mes"
    }}
  ]
}}

Responde SOLO con el JSON, sin markdown ni texto adicional."""

    print("  Analizando con LLM...")
    raw = llm_call(prompt, max_tokens=2000)

    try:
        analysis = json.loads(raw)
    except Exception:
        # Intentar extraer JSON si hay texto extra
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        analysis = json.loads(m.group()) if m else {"error": raw[:500]}

    # 3) Guardar en Supabase
    payload = {
        "team":       team,
        "generated_at": datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
        "serp_data":  serp_results,
        "analysis":   analysis,
    }
    try:
        result = sb_request("POST", "market_intel", payload)
        print(f"  ✓ Guardado en Supabase (id={result[0].get('id') if result else '?'})")
    except Exception as e:
        print(f"  ✗ Error Supabase: {e}")
        # Guardar localmente como fallback
        out = Path("/tmp/market_intel_latest.json")
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"  → Guardado en {out}")

    print("\nAnálisis:")
    print(json.dumps(analysis, indent=2, ensure_ascii=False))

    # 4) Enviar email con el resumen
    today_str = datetime.now().strftime("%d/%m/%Y")
    send_intel_email(analysis, serp_results, today_str)

    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", default="marketing")
    args = parser.parse_args()
    run(args.team)
