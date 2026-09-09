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
import argparse, json, os, sys, urllib.request, urllib.parse, urllib.error
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _etendo import llm_call, sb_request

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

# .env.google vive en la raíz del workspace (fuera de okr_manager_site/)
ENV_GOOGLE_FILE = Path(__file__).resolve().parent.parent.parent / ".env.google"

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


# ── Google OAuth (Search Console / GA4 / Ads) ───────────────────────────────

def _load_env_google() -> dict:
    env_vars = {}
    if ENV_GOOGLE_FILE.exists():
        for line in ENV_GOOGLE_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"')
    return env_vars


def _google_access_token(refresh_token_env: str) -> str:
    """Intercambia el refresh token guardado en .env.google por un access token."""
    env_vars      = _load_env_google()
    client_id     = env_vars.get("GOOGLE_OAUTH_CLIENT_ID", "")     or os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = env_vars.get("GOOGLE_OAUTH_CLIENT_SECRET", "") or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    refresh_token = env_vars.get(refresh_token_env, "")             or os.environ.get(refresh_token_env, "")
    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError(f"Faltan credenciales OAuth Google ({refresh_token_env}) en .env.google")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        }).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        token = json.loads(r.read()).get("access_token", "")
    if not token:
        raise RuntimeError(f"No se obtuvo access_token para {refresh_token_env}")
    return token


def _pct_delta(cur, prev):
    if not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


# ── Google Search Console ────────────────────────────────────────────────────

def fetch_search_console_weekly(keywords: list) -> dict:
    """GSC v1: últimos 7 días vs semana anterior, top queries/pages y posición real
    de `keywords` (las mismas 6 que usa SerpAPI)."""
    token    = _google_access_token("GOOGLE_REFRESH_TOKEN_GA4_SC")
    env_vars = _load_env_google()
    site     = env_vars.get("SC_SITE", "sc-domain:etendo.software")
    site_enc = urllib.parse.quote(site, safe="")
    url      = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{site_enc}/searchAnalytics/query"

    # GSC tiene 2-3 días de lag en los datos más recientes
    end_date   = date.today() - timedelta(days=3)
    start_date = end_date - timedelta(days=6)
    prev_end   = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)

    def sc_rows(start, end, dims=None, row_limit=1, extra=None):
        body = {"startDate": str(start), "endDate": str(end),
                "dimensions": dims or [], "rowLimit": row_limit}
        if extra:
            body.update(extra)
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read()).get("rows", [])

    def totals_for(start, end):
        rows = sc_rows(start, end)
        return rows[0] if rows else {}

    cur_totals  = totals_for(start_date, end_date)
    prev_totals = totals_for(prev_start, prev_end)

    top_queries = [
        {"query": r["keys"][0], "clicks": int(r.get("clicks", 0)),
         "impressions": int(r.get("impressions", 0)),
         "ctr": round(r.get("ctr", 0) * 100, 2),
         "position": round(r.get("position", 0), 1)}
        for r in sc_rows(start_date, end_date, dims=["query"], row_limit=10)
    ]
    top_pages = [
        {"page": r["keys"][0], "clicks": int(r.get("clicks", 0)),
         "impressions": int(r.get("impressions", 0)),
         "ctr": round(r.get("ctr", 0) * 100, 2)}
        for r in sc_rows(start_date, end_date, dims=["page"], row_limit=10)
    ]

    keyword_positions = {}
    for kw in keywords:
        rows = sc_rows(start_date, end_date, dims=[], row_limit=1, extra={
            "dimensionFilterGroups": [{"filters": [
                {"dimension": "query", "operator": "equals", "expression": kw}
            ]}]
        })
        if rows:
            r = rows[0]
            keyword_positions[kw] = {
                "position":    round(r.get("position", 0), 1),
                "clicks":      int(r.get("clicks", 0)),
                "impressions": int(r.get("impressions", 0)),
            }
        else:
            keyword_positions[kw] = {"position": None, "clicks": 0, "impressions": 0}

    cur_clicks, cur_impr   = int(cur_totals.get("clicks", 0)),  int(cur_totals.get("impressions", 0))
    prev_clicks, prev_impr = int(prev_totals.get("clicks", 0)), int(prev_totals.get("impressions", 0))

    return {
        "period":                 f"{start_date} a {end_date}",
        "clicks":                 cur_clicks,
        "impressions":            cur_impr,
        "ctr":                    round(cur_totals.get("ctr", 0) * 100, 2),
        "avg_position":           round(cur_totals.get("position", 0), 1),
        "clicks_delta_pct":       _pct_delta(cur_clicks, prev_clicks),
        "impressions_delta_pct":  _pct_delta(cur_impr, prev_impr),
        "top_queries":            top_queries,
        "top_pages":              top_pages,
        "keyword_positions":      keyword_positions,
    }


# ── Google Analytics 4 ───────────────────────────────────────────────────────

def fetch_ga4_weekly() -> dict:
    """GA4 Data API: sesiones por canal, nuevos vs recurrentes, conversión y
    top páginas de los últimos 7 días vs semana anterior."""
    token    = _google_access_token("GOOGLE_REFRESH_TOKEN_GA4_SC")
    env_vars = _load_env_google()
    prop     = env_vars.get("GA4_PROPERTY_ID", "353675924")
    endpoint = f"https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport"

    end_date   = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=6)
    prev_end   = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)

    def run(payload):
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def totals_for(start, end):
        r    = run({
            "dateRanges": [{"startDate": str(start), "endDate": str(end)}],
            "metrics": [{"name": "sessions"}, {"name": "keyEvents"},
                        {"name": "newUsers"}, {"name": "activeUsers"}],
        })
        rows = r.get("rows", [])
        vals = rows[0].get("metricValues", []) if rows else []
        get  = lambda i: float(vals[i]["value"]) if len(vals) > i else 0.0
        return {"sessions": get(0), "key_events": get(1), "new_users": get(2), "active_users": get(3)}

    cur  = totals_for(start_date, end_date)
    prev = totals_for(prev_start, prev_end)

    r_ch = run({
        "dateRanges":  [{"startDate": str(start_date), "endDate": str(end_date)}],
        "dimensions":  [{"name": "sessionDefaultChannelGroup"}],
        "metrics":     [{"name": "sessions"}],
    })
    sessions_by_channel = {
        row["dimensionValues"][0]["value"]: int(float(row["metricValues"][0]["value"]))
        for row in r_ch.get("rows", [])
    }

    r_pages = run({
        "dateRanges":  [{"startDate": str(start_date), "endDate": str(end_date)}],
        "dimensions":  [{"name": "pagePath"}],
        "metrics":     [{"name": "sessions"}],
        "orderBys":    [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit":       5,
    })
    top_pages = [
        {"page": row["dimensionValues"][0]["value"], "sessions": int(float(row["metricValues"][0]["value"]))}
        for row in r_pages.get("rows", [])
    ]

    total_sessions  = cur["sessions"]
    conversion_rate = round(cur["key_events"] / total_sessions * 100, 2) if total_sessions else 0
    returning_users  = max(cur["active_users"] - cur["new_users"], 0)

    return {
        "period":                f"{start_date} a {end_date}",
        "sessions":              int(cur["sessions"]),
        "sessions_delta_pct":    _pct_delta(cur["sessions"], prev["sessions"]),
        "new_users":             int(cur["new_users"]),
        "returning_users":       int(returning_users),
        "conversion_rate_pct":   conversion_rate,
        "key_events":            int(cur["key_events"]),
        "organic_sessions":      sessions_by_channel.get("Organic Search", 0),
        "sessions_by_channel":   sessions_by_channel,
        "top_pages":             top_pages,
    }


# ── Google Ads ────────────────────────────────────────────────────────────────

def fetch_google_ads_weekly() -> dict:
    """Google Ads API v20: gasto, clicks, conversiones y CPL reales de los
    últimos 7 días vs semana anterior, con top 3 campañas por gasto."""
    token       = _google_access_token("GOOGLE_REFRESH_TOKEN_ADS")
    env_vars    = _load_env_google()
    dev_token   = env_vars.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
    mcc_id      = env_vars.get("GOOGLE_ADS_MCC_CUSTOMER_ID", "")
    customer_id = env_vars.get("GOOGLE_ADS_CLIENT_CUSTOMER_ID", "")
    # v20 es la única versión soportada hoy — GOOGLE_ADS_API_VERSION en
    # .env.google puede quedar desactualizada (v16-v18 y v21 dan 404).
    api_version = "v20"
    if not all([dev_token, customer_id]):
        raise RuntimeError("Faltan credenciales de Google Ads en .env.google")

    headers = {
        "Authorization": f"Bearer {token}",
        "developer-token": dev_token,
        "Content-Type": "application/json",
    }
    if mcc_id:
        headers["login-customer-id"] = mcc_id

    endpoint = f"https://googleads.googleapis.com/{api_version}/customers/{customer_id}/googleAds:search"

    end_date   = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=6)
    prev_end   = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)

    def campaigns_for(start, end):
        query = f"""
            SELECT campaign.name, metrics.cost_micros, metrics.clicks,
                metrics.impressions, metrics.conversions
            FROM campaign
            WHERE segments.date BETWEEN '{start}' AND '{end}'
              AND campaign.status = 'ENABLED'
        """
        req = urllib.request.Request(endpoint, data=json.dumps({"query": query}).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read()).get("results", [])

    def aggregate(rows):
        campaigns = {}
        total_cost = total_clicks = total_impr = total_conv = 0.0
        for row in rows:
            name = row["campaign"]["name"]
            m    = row["metrics"]
            cost   = int(m.get("costMicros", 0)) / 1_000_000
            clicks = int(m.get("clicks", 0))
            impr   = int(m.get("impressions", 0))
            conv   = float(m.get("conversions", 0))
            c = campaigns.setdefault(name, {"spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0})
            c["spend"] += cost; c["clicks"] += clicks
            c["impressions"] += impr; c["conversions"] += conv
            total_cost += cost; total_clicks += clicks
            total_impr += impr; total_conv += conv
        return campaigns, total_cost, total_clicks, total_impr, total_conv

    campaigns, cost, clicks, impr, conv = aggregate(campaigns_for(start_date, end_date))
    _, prev_cost, _, _, prev_conv       = aggregate(campaigns_for(prev_start, prev_end))

    for c in campaigns.values():
        c["spend"]       = round(c["spend"], 2)
        c["conversions"] = round(c["conversions"], 1)
        c["cpl"]         = round(c["spend"] / c["conversions"], 2) if c["conversions"] > 0 else 0

    top_campaigns = [
        {"name": name, **data}
        for name, data in sorted(campaigns.items(), key=lambda kv: kv[1]["spend"], reverse=True)[:3]
    ]

    return {
        "period":                  f"{start_date} a {end_date}",
        "spend":                   round(cost, 2),
        "impressions":             int(impr),
        "clicks":                  int(clicks),
        "ctr_pct":                 round(clicks / impr * 100, 2) if impr else 0,
        "avg_cpc":                 round(cost / clicks, 2) if clicks else 0,
        "conversions":             round(conv, 1),
        "conversion_rate_pct":     round(conv / clicks * 100, 2) if clicks else 0,
        "cpl":                     round(cost / conv, 2) if conv > 0 else 0,
        "top_campaigns":           top_campaigns,
        "spend_delta_pct":         _pct_delta(cost, prev_cost),
        "conversions_delta_pct":   _pct_delta(conv, prev_conv),
    }


def build_metricas_semana(gsc: dict, ga4: dict, ads: dict) -> dict:
    """KPIs reales resumidos — se fuerzan estos valores en el JSON del LLM
    para no depender de que el LLM copie los números correctamente."""
    return {
        "clics_organicos":            gsc.get("clicks"),
        "clics_organicos_delta_pct":  gsc.get("clicks_delta_pct"),
        "sesiones_ga4":               ga4.get("sessions"),
        "sesiones_ga4_delta_pct":     ga4.get("sessions_delta_pct"),
        "sesiones_organicas_ga4":     ga4.get("organic_sessions"),
        "tasa_conversion_ga4_pct":    ga4.get("conversion_rate_pct"),
        "gasto_ads":                  ads.get("spend"),
        "gasto_ads_delta_pct":        ads.get("spend_delta_pct"),
        "conversiones_ads":           ads.get("conversions"),
        "conversiones_ads_delta_pct": ads.get("conversions_delta_pct"),
        "ctr_ads_pct":                ads.get("ctr_pct"),
        "cpl_real":                   ads.get("cpl"),
    }


def send_intel_email(analysis: dict, serp_results: list, today: str,
                      gsc_data: dict = None, ga4_data: dict = None, ads_data: dict = None,
                      api_errors: list = None, dry_run: bool = False) -> None:
    """Envía el resumen de inteligencia competitiva por email."""
    if not dry_run and (not SMTP_USER or not SMTP_PASS):
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

    gsc_data      = gsc_data or {}
    ga4_data      = ga4_data or {}
    ads_data      = ads_data or {}
    api_errors    = api_errors or []
    metricas      = analysis.get("metricas_semana", {})

    amenaza_color = {"alta": "#c0392b", "media": "#e67e22", "baja": "#27ae60"}
    urgencia_color = {"hoy": "#c0392b", "esta_semana": "#e67e22", "este_mes": "#2980b9"}

    def badge(val, cmap, default="#666"):
        color = cmap.get(val, default)
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:bold">{val}</span>'

    def delta_html(pct):
        if pct is None:
            return '<span style="color:#999">—</span>'
        color = "#27ae60" if pct >= 0 else "#c0392b"
        arrow = "↑" if pct >= 0 else "↓"
        return f'<span style="color:{color};font-weight:bold">{arrow} {abs(pct)}%</span>'

    def kpi_row(label, value, delta_pct, suffix=""):
        return f"""<tr>
          <td style="padding:8px 12px;font-size:13px">{label}</td>
          <td style="padding:8px 12px;font-size:14px;font-weight:bold">{value}{suffix}</td>
          <td style="padding:8px 12px;font-size:12px">{delta_html(delta_pct)}</td>
        </tr>"""

    kpi_rows = "".join([
        kpi_row("Clics orgánicos (GSC)", metricas.get("clics_organicos", "—"), metricas.get("clics_organicos_delta_pct")),
        kpi_row("Sesiones GA4", metricas.get("sesiones_ga4", "—"), metricas.get("sesiones_ga4_delta_pct")),
        kpi_row("Gasto Ads", metricas.get("gasto_ads", "—"), metricas.get("gasto_ads_delta_pct"), " €"),
        kpi_row("Conversiones Ads", metricas.get("conversiones_ads", "—"), metricas.get("conversiones_ads_delta_pct")),
        kpi_row("CPL real", metricas.get("cpl_real", "—"), None, " €"),
    ])

    kw_position_rows = "".join(
        f"""<tr>
          <td style="padding:6px 10px;font-size:12px">{kw}</td>
          <td style="padding:6px 10px;font-size:12px;font-weight:bold;color:{'#27ae60' if pos.get('position') else '#c0392b'}">
            {'#' + str(pos['position']) if pos.get('position') else 'fuera top100'}
          </td>
          <td style="padding:6px 10px;font-size:11px;color:#777">{pos.get('clicks', 0)} clics / {pos.get('impressions', 0)} impr.</td>
        </tr>""" for kw, pos in gsc_data.get("keyword_positions", {}).items()
    )

    errores_html = ""
    if api_errors:
        errores_html = (
            '<p style="margin:8px 0 0;font-size:12px;color:#c0392b">⚠️ Datos no disponibles hoy: '
            + ", ".join(api_errors) + '</p>'
        )

    metricas_section = f"""
  <div style="padding:16px 24px;background:#fafbfc">
    <h3 style="font-size:14px;color:#2c3e50;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">📊 Métricas de la semana</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:12px">
      <thead style="background:#f5f5f5">
        <tr>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">KPI</th>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Valor</th>
          <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">vs. semana anterior</th>
        </tr>
      </thead>
      <tbody>{kpi_rows}</tbody>
    </table>
    {"<p style='font-size:12px;color:#888;margin:0 0 4px'>Posición real (Search Console) para keywords clave:</p>" if kw_position_rows else ""}
    {f'''<table style="width:100%;border-collapse:collapse;font-size:12px">
      <tbody>{kw_position_rows}</tbody>
    </table>''' if kw_position_rows else ""}
    {errores_html}
  </div>"""

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
{metricas_section}
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

    if dry_run:
        out = Path("/tmp/market_intel_email_preview.html")
        out.write_text(body_html)
        print(f"  [dry-run] Email NO enviado — HTML guardado en {out}")
        return

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.ehlo(); s.starttls(); s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, RECIPIENTS, msg.as_string())
        print(f"  ✓ Email enviado a {', '.join(RECIPIENTS)}")
    except Exception as e:
        print(f"  ✗ Error enviando email: {e}")


def run(team="marketing", dry_run=False):
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

    # 2b) Datos reales de las tres plataformas de Google — cada una puede
    # fallar de forma independiente; si falla, se loguea y se sigue sin ella.
    keywords = [q for q, _, _ in QUERIES]
    gsc_data, ga4_data, ads_data = {}, {}, {}
    api_errors = []

    print("  GSC: consultando Search Console...")
    try:
        gsc_data = fetch_search_console_weekly(keywords)
        print(f"    ✓ {gsc_data['clicks']} clics / {gsc_data['impressions']} impresiones (7d)")
    except Exception as e:
        print(f"  ✗ [GSC] Error: {e}")
        api_errors.append("Google Search Console")

    print("  GA4: consultando Analytics...")
    try:
        ga4_data = fetch_ga4_weekly()
        print(f"    ✓ {ga4_data['sessions']} sesiones / {ga4_data['conversion_rate_pct']}% conversión (7d)")
    except Exception as e:
        print(f"  ✗ [GA4] Error: {e}")
        api_errors.append("Google Analytics 4")

    print("  Ads: consultando Google Ads...")
    try:
        ads_data = fetch_google_ads_weekly()
        print(f"    ✓ €{ads_data['spend']} gasto / {ads_data['conversions']} conversiones (7d)")
    except Exception as e:
        print(f"  ✗ [Ads] Error: {e}")
        api_errors.append("Google Ads")

    metricas_semana = build_metricas_semana(gsc_data, ga4_data, ads_data)

    gsc_summary = "(Search Console no disponible hoy)" if not gsc_data else f"""  Período: {gsc_data['period']}
  Clics totales: {gsc_data['clicks']} (delta vs semana anterior: {gsc_data['clicks_delta_pct']}%)
  Impresiones totales: {gsc_data['impressions']} (delta: {gsc_data['impressions_delta_pct']}%)
  CTR promedio: {gsc_data['ctr']}% | Posición promedio: {gsc_data['avg_position']}
  Top 5 queries por clics: {[q['query'] for q in gsc_data['top_queries'][:5]]}
  Top 5 páginas por clics: {[p['page'] for p in gsc_data['top_pages'][:5]]}
  Posición real por keyword: {json.dumps(gsc_data['keyword_positions'], ensure_ascii=False)}"""

    ga4_summary = "(GA4 no disponible hoy)" if not ga4_data else f"""  Período: {ga4_data['period']}
  Sesiones totales: {ga4_data['sessions']} (delta vs semana anterior: {ga4_data['sessions_delta_pct']}%)
  Usuarios nuevos: {ga4_data['new_users']} | Usuarios recurrentes: {ga4_data['returning_users']}
  Tasa de conversión (key events/sesiones): {ga4_data['conversion_rate_pct']}%
  Sesiones orgánicas: {ga4_data['organic_sessions']}
  Sesiones por canal: {json.dumps(ga4_data['sessions_by_channel'], ensure_ascii=False)}
  Top 5 páginas por sesiones: {[p['page'] for p in ga4_data['top_pages']]}"""

    ads_summary = "(Google Ads no disponible hoy)" if not ads_data else f"""  Período: {ads_data['period']}
  Gasto total: €{ads_data['spend']} (delta vs semana anterior: {ads_data['spend_delta_pct']}%)
  Impresiones: {ads_data['impressions']} | Clics: {ads_data['clicks']} | CTR: {ads_data['ctr_pct']}% | CPC promedio: €{ads_data['avg_cpc']}
  Conversiones: {ads_data['conversions']} (delta: {ads_data['conversions_delta_pct']}%) | Tasa conversión: {ads_data['conversion_rate_pct']}%
  CPL real (gasto/conversiones): €{ads_data['cpl']}
  Top 3 campañas por gasto: {json.dumps(ads_data['top_campaigns'], ensure_ascii=False)}"""

    prompt = f"""Eres analista de inteligencia competitiva para Etendo, un ERP Agentic para pymes españolas.

DATOS SERP HOY ({datetime.now().strftime('%d/%m/%Y')}):
{serp_summary}

DATOS REALES — GOOGLE SEARCH CONSOLE (últimos 7 días):
{gsc_summary}

DATOS REALES — GOOGLE ANALYTICS 4 (últimos 7 días):
{ga4_summary}

DATOS REALES — GOOGLE ADS (últimos 7 días):
{ads_summary}

COMPETIDORES PRINCIPALES: Odoo (open source, market leader), Holded (cloud ES), Sage (legacy), A3ERP (contabilidad ES), SAP B1 (enterprise).

POSICIONAMIENTO DE ETENDO: "Agentic ERP" — el único ERP que ejecuta acciones con agentes IA. Foco: pymes España, verifactu, automatización de procesos.

INSTRUCCIONES CLAVE:
- Usá SIEMPRE los datos reales de GSC/GA4/Ads (no las estimaciones SERP) cuando estén disponibles — nunca inventes cifras que no aparecen en los datos de arriba.
- En "resumen_ejecutivo" mencioná al menos una métrica real concreta (ej: clics orgánicos, sesiones GA4 o gasto/CPL de Ads).
- En "posicion_etendo", basá fortalezas/brechas en la posición real de GSC para las keywords, no solo en SerpAPI.
- En "oportunidades", basá las recomendaciones en brechas reales de los datos (ej: canal con caída de sesiones, keyword con impresiones altas pero clics bajos, página con pocas sesiones).
- En "acciones_semana", priorizá "hoy" si el CPL real de Ads es muy alto o si el gasto subió sin subir conversiones.
- Si alguna fuente no está disponible hoy, no inventes su dato — decilo explícitamente donde corresponda.
- Si el JSON no llegara con los datos reales, van a ser sobreescritos automáticamente por el pipeline, así que no hace falta que copies los números con precisión perfecta.

Con estos datos, genera un análisis estructurado en JSON con esta estructura exacta:
{{
  "resumen_ejecutivo": "2-3 frases sobre el estado del mercado hoy, citando al menos una métrica real",
  "posicion_etendo": {{
    "fortalezas": ["...", "..."],
    "brechas": ["...", "..."]
  }},
  "metricas_semana": {{
    "clics_organicos": <número>,
    "sesiones_ga4": <número>,
    "gasto_ads": <número>,
    "conversiones_ads": <número>,
    "cpl_real": <número>
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
    raw = llm_call(prompt, max_tokens=2500)

    try:
        analysis = json.loads(raw)
    except Exception:
        # Intentar extraer JSON si hay texto extra
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        analysis = json.loads(m.group()) if m else {"error": raw[:500]}

    # Forzamos los KPIs reales calculados en Python — el LLM sólo redacta,
    # nunca decide estos números (evita que copie mal una cifra).
    analysis["metricas_semana"] = metricas_semana

    # 3) Guardar en Supabase
    payload = {
        "team":         team,
        "generated_at": datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
        "serp_data":    serp_results,
        "analysis":     analysis,
        "gsc_data":     gsc_data,
        "ga4_data":     ga4_data,
        "ads_data":     ads_data,
    }
    if dry_run:
        out = Path("/tmp/market_intel_dryrun_payload.json")
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"  [dry-run] Supabase NO escrito — payload guardado en {out}")
    else:
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
    send_intel_email(analysis, serp_results, today_str,
                      gsc_data=gsc_data, ga4_data=ga4_data, ads_data=ads_data,
                      api_errors=api_errors, dry_run=dry_run)

    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", default="marketing")
    parser.add_argument("--dry-run", action="store_true",
                         help="No escribe en Supabase ni envía el email — guarda el HTML/JSON en /tmp para revisar")
    args = parser.parse_args()
    run(args.team, dry_run=args.dry_run)
