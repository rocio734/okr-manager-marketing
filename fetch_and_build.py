"""
Etendo Dashboard — Auto-actualización diaria
Lee analytics-dashboard/index.html como template,
reemplaza los marcadores <!-- WS:id --> con datos frescos de Windsor.ai,
y escribe el resultado de vuelta al mismo archivo.

Campos GA4 confirmados como válidos en Windsor.ai (verificados en sesión real):
  sessions, active_users, screen_page_views,
  engagement_rate, bounce_rate, average_session_duration

session_default_channel_group se intenta pero con fallback si da 400.
"""
import requests
import os
import re
import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

WINDSOR_API_KEY = os.environ["WINDSOR_API_KEY"]

# ── CRM (Etendo) ───────────────────────────────────────────────────────────────
_CRM_BASE  = os.environ.get("ETENDO_BASE_URL", "")
_CRM_USER  = os.environ.get("ETENDO_USERNAME", "")
_CRM_PASS  = os.environ.get("ETENDO_PASSWORD", "")
_CRM_ROLE  = "8351131DFF384725AB08E06773FE6144"

def crm_login():
    body = json.dumps({"username": _CRM_USER, "password": _CRM_PASS, "role": _CRM_ROLE}).encode()
    req  = urllib.request.Request(f"{_CRM_BASE}/api/auth/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["token"]

def crm_fetch_all(token, entity):
    out, start = [], 0
    while True:
        body = urllib.parse.urlencode({
            "_operationType": "fetch",
            "_startRow": str(start),
            "_endRow": str(start + 500),
        }).encode()
        req = urllib.request.Request(f"{_CRM_BASE}/api/datasource/{entity}", data=body, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=30) as r:
            page = json.loads(r.read()).get("response", {}).get("data", [])
        if not page:
            break
        out.extend(page)
        if len(page) < 500:
            break
        start += 500
    return out

def phone_country(phone):
    if not phone:
        return "—"
    p = str(phone).replace(" ", "").replace("-", "")
    if p.startswith("+54") or p.startswith("54"):
        return "Argentina"
    if p.startswith("+34") or p.startswith("34"):
        return "España"
    if p.startswith("+52") or p.startswith("52"):
        return "México"
    if p.startswith("+57"):
        return "Colombia"
    if p.startswith("+56"):
        return "Chile"
    if p.startswith("+51"):
        return "Perú"
    if p.startswith("+60"):
        return "Malasia"
    return "—"
BASE      = "https://connectors.windsor.ai"
REPO_DIR  = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(REPO_DIR, "analytics-dashboard", "index.html")

# ── API ───────────────────────────────────────────────────────────────────────
def _fetch(connector, fields, date_from, date_to):
    params = {
        "api_key":   WINDSOR_API_KEY,
        "fields":    ",".join(fields),
        "date_from": date_from,
        "date_to":   date_to,
    }
    r = requests.get(f"{BASE}/{connector}", params=params, timeout=40)
    r.raise_for_status()
    d = r.json()
    return d.get("data", d) if isinstance(d, dict) else d

def fetch(connector, fields, start=30, end=1):
    t  = datetime.today()
    df = (t - timedelta(days=start)).strftime("%Y-%m-%d")
    dt = (t - timedelta(days=end)).strftime("%Y-%m-%d")
    return _fetch(connector, fields, df, dt)

def fetch_safe(connector, fields, start=30, end=1, fallback=None):
    """Igual que fetch pero devuelve fallback si la API retorna error."""
    try:
        return fetch(connector, fields, start, end)
    except requests.HTTPError as e:
        print(f"  ⚠️  {connector} [{','.join(fields[:2])}...] → {e.response.status_code}, usando fallback")
        return fallback if fallback is not None else []

fsum = lambda rows, f: sum(float(r.get(f) or 0) for r in rows)
def favg(rows, f):
    v = [float(r.get(f) or 0) for r in rows if r.get(f) is not None]
    return sum(v)/len(v) if v else 0

fmt_dur = lambda s: f"{int(s//60)}m {int(s%60):02d}s"
fmtn    = lambda v: f"{int(v):,}".replace(",", ".")

def channel_sum(rows, name):
    return sum(
        float(r.get("sessions") or 0) for r in rows
        if name.lower() in str(r.get("session_default_channel_group","")).lower()
    )

# ── Fetch ─────────────────────────────────────────────────────────────────────
# Campos GA4 confirmados válidos en Windsor
GA4_FIELDS = [
    "sessions", "active_users", 
    "screen_page_views", "engagement_rate",
    "bounce_rate", "average_session_duration",
]

print("→ GA4 actual (30d)...")
ga4c = fetch("googleanalytics4", GA4_FIELDS)
print("→ GA4 anterior (30-60d)...")
ga4p = fetch("googleanalytics4", GA4_FIELDS, start=60, end=30)

# Canales — session_default_channel_group puede no estar disponible en todos los planes
print("→ GA4 canales actual (con fallback)...")
ch_c_rows = fetch_safe("googleanalytics4",
    ["sessions", "session_default_channel_group"], fallback=[])
print("→ GA4 canales anterior (con fallback)...")
ch_p_rows = fetch_safe("googleanalytics4",
    ["sessions", "session_default_channel_group"],
    start=60, end=30, fallback=[])

print("→ Google Ads actual...")
adsc = fetch("google_ads", [
    "cost", "clicks", "impressions", "conversions",
    "campaign_name", "campaign_status",
])
print("→ Google Ads anterior...")
adsp = fetch("google_ads", [
    "cost", "clicks", "conversions", "campaign_name",
], start=60, end=30)

print("→ Search Console actual...")
scc = fetch("searchconsole", ["clicks", "impressions", "ctr", "position"])
print("→ Search Console anterior...")
scp = fetch("searchconsole", ["clicks", "impressions", "ctr", "position"],
            start=60, end=30)

print("→ Keywords top 20...")
kws = fetch("searchconsole", ["query", "clicks", "impressions", "ctr", "position"])

print("→ GA4 top páginas...")
pages_raw = fetch("googleanalytics4", ["page_path", "sessions", "bounce_rate", "average_session_duration"])
pages_top     = sorted(pages_raw, key=lambda x: float(x.get("sessions") or 0), reverse=True)[:12]
pages_quality = sorted(pages_raw, key=lambda x: float(x.get("average_session_duration") or 0), reverse=True)[:10]

print("→ CRM leads...")
crm_leads_all, crm_ok = [], False
try:
    crm_token = crm_login()
    crm_leads_all = crm_fetch_all(crm_token, "ETCRM_Lead")
    crm_ok = True
    print(f"  ✅ {len(crm_leads_all)} leads obtenidos (total)")
except Exception as e:
    print(f"  ⚠️  CRM no disponible: {e}")

# Ventana 30 días — igual que GA4
_crm_cutoff = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
def _crm_in_window(l):
    d = (l.get("creationDate") or "")[:10]
    return d >= _crm_cutoff

crm_leads = [l for l in crm_leads_all if _crm_in_window(l)]
print(f"  → {len(crm_leads)} leads en últimos 30 días")

_INACTIVE = {"Dead", "Converted"}
def _lstatus(l): return l.get("leadStatus$_identifier") or ""
def _lclass(l):  return l.get("classification$_identifier") or ""
def _lname(l):
    fn = (l.get("firstname") or "").strip()
    ln = (l.get("lastname") or "").strip()
    return (fn + (" " + ln if ln else "")) or "—"

crm_active  = [l for l in crm_leads if _lstatus(l) not in _INACTIVE]
crm_dead    = [l for l in crm_leads if _lstatus(l) == "Dead"]
crm_conv    = [l for l in crm_leads if _lstatus(l) == "Converted"]
crm_iql     = [l for l in crm_active if _lclass(l) == "IQL"]
crm_mql     = [l for l in crm_active if _lclass(l) == "MQL"]
crm_sql     = [l for l in crm_active if _lclass(l) == "SQL"]
crm_qual    = [l for l in crm_active if _lstatus(l) == "Qualified"]

# Orden pipeline: SQL > MQL > IQL > resto activos
_CLASS_ORDER = {"SQL": 0, "MQL": 1, "IQL": 2}
crm_pipeline = sorted(crm_active,
    key=lambda l: (_CLASS_ORDER.get(_lclass(l), 9), _lname(l)))

# ── Agregados GA4 ─────────────────────────────────────────────────────────────
C = dict(
    sessions  = fsum(ga4c, "sessions"),
    users     = fsum(ga4c, "active_users"),
    pageviews = fsum(ga4c, "screen_page_views"),
    eng       = favg(ga4c, "engagement_rate") * 100,
    bounce    = favg(ga4c, "bounce_rate") * 100,
    dur       = favg(ga4c, "average_session_duration"),
)
P = dict(
    sessions  = fsum(ga4p, "sessions"),
    users     = fsum(ga4p, "active_users"),
    pageviews = fsum(ga4p, "screen_page_views"),
    eng       = favg(ga4p, "engagement_rate") * 100,
    bounce    = favg(ga4p, "bounce_rate") * 100,
)

# Canales — si no hay datos de dimensión, queda en 0 (no rompe el script)
ch_map = {
    "direct":         "direct",
    "organic":        "organic search",
    "paid_search":    "paid search",
    "cross":          "cross-network",
    "paid_social":    "paid social",
    "referral":       "referral",
    "organic_social": "organic social",
    "ai":             "ai",
}
ch_c = {k: channel_sum(ch_c_rows, v) for k, v in ch_map.items()}
ch_p = {k: channel_sum(ch_p_rows, v) for k, v in ch_map.items()}
channels_available = len(ch_c_rows) > 0
if not channels_available:
    print("  ℹ️  Canales no disponibles — se mostrarán como '—' en el dashboard")

# ── Agregados Ads ─────────────────────────────────────────────────────────────
AC = dict(
    cost  = fsum(adsc, "cost"),
    clicks= fsum(adsc, "clicks"),
    impr  = fsum(adsc, "impressions"),
    conv  = fsum(adsc, "conversions"),
)
AC["ctr"]    = AC["clicks"] / AC["impr"] * 100 if AC["impr"]   else 0
AC["cpc"]    = AC["cost"]   / AC["clicks"]      if AC["clicks"] else 0
AC["cpconv"] = AC["cost"]   / AC["conv"]        if AC["conv"]   else 0
AC["daily"]  = AC["cost"]   / 30

AP = dict(
    cost  = fsum(adsp, "cost"),
    clicks= fsum(adsp, "clicks"),
    conv  = fsum(adsp, "conversions"),
)
AP["cpconv"] = AP["cost"] / AP["conv"] if AP["conv"] else 0
AP["daily"]  = AP["cost"] / 30

camps = {}
for r in adsc:
    n = r.get("campaign_name", "—")
    if n not in camps:
        camps[n] = {"cost":0,"clicks":0,"conv":0,"status":r.get("campaign_status","—")}
    camps[n]["cost"]  += float(r.get("cost") or 0)
    camps[n]["clicks"]+= float(r.get("clicks") or 0)
    camps[n]["conv"]  += float(r.get("conversions") or 0)

# ── Agregados SC ──────────────────────────────────────────────────────────────
SC = dict(
    clicks = fsum(scc, "clicks"),
    impr   = fsum(scc, "impressions"),
    ctr    = favg(scc, "ctr") * 100,
    pos    = favg(scc, "position"),
)
SP = dict(
    clicks = fsum(scp, "clicks"),
    impr   = fsum(scp, "impressions"),
    ctr    = favg(scp, "ctr") * 100,
    pos    = favg(scp, "position"),
)
kw_top = sorted(kws, key=lambda x: float(x.get("impressions") or 0), reverse=True)[:20]

# ── Fechas ────────────────────────────────────────────────────────────────────
today     = datetime.today()
date_to   = today.strftime("%d/%m/%Y")
date_from = (today - timedelta(days=30)).strftime("%d/%m/%Y")
date_to_p = (today - timedelta(days=30)).strftime("%d/%m/%Y")
date_fr_p = (today - timedelta(days=60)).strftime("%d/%m/%Y")
generated = today.strftime("%d/%m/%Y %H:%M UTC")

# ── Tablas HTML ───────────────────────────────────────────────────────────────
def crm_badge(cls, status):
    if cls == "SQL" or status == "Qualified":
        return '<span class="badge" style="background:#EAF3DE;color:#3B6D11">SQL ⭐</span>'
    if cls == "MQL":
        return '<span class="badge" style="background:#FFF8CC;color:#666">MQL</span>'
    if cls == "IQL":
        return '<span class="badge" style="background:#EEF4FF;color:#185FA5">IQL</span>'
    if status == "Dead":
        return '<span class="badge" style="background:#FCEBEB;color:#A32D2D">Dead</span>'
    return f'<span class="badge" style="background:#F5F5F5;color:#666">{status or "Nuevo"}</span>'

def crm_rows():
    if not crm_pipeline:
        return "<tr><td colspan='5' style='text-align:center;color:#999'>Sin datos CRM</td></tr>\n"
    out = ""
    for l in crm_pipeline[:15]:
        name    = _lname(l)
        company = (l.get("company") or "—").strip() or "—"
        phone   = l.get("phone") or ""
        country = phone_country(phone)
        cls     = _lclass(l)
        status  = _lstatus(l)
        badge   = crm_badge(cls, status)
        desc    = (l.get("description") or "").replace("\n", " ").strip()
        desc    = desc[:80] + "…" if len(desc) > 80 else desc
        out += (
            f'<tr><td><strong>{name}</strong></td>'
            f'<td>{company}</td>'
            f'<td>{country}</td>'
            f'<td>{badge}</td>'
            f'<td style="font-size:11px;color:#666">{desc}</td></tr>\n'
        )
    return out

def page_type(path):
    if any(x in path for x in ["/user-guide/", "/developer-guide/", "/whats-new/"]):
        return '<span class="badge" style="background:#EEF4FF;color:#185FA5">Doc técnica</span>'
    if "/blog/" in path:
        return '<span class="badge" style="background:#EAF3DE;color:#3B6D11">Blog</span>'
    if path in ["/contactanos/", "/muchas-gracias/", "/en/contactanos/"]:
        return '<span class="badge" style="background:#EAF3DE;color:#3B6D11">Conversión</span>'
    if any(x in path for x in ["/copilot/", "/etendo-go/", "/etendo-next/"]):
        return '<span class="badge" style="background:#FFF8CC;color:#666">Producto</span>'
    return '<span class="badge" style="background:#E2F0FF;color:#0a3c6e">Captación</span>'

def page_bars():
    if not pages_top:
        return "<p>— Sin datos —</p>"
    max_s = max(float(p.get("sessions") or 0) for p in pages_top) or 1
    out = ""
    for p in pages_top:
        path    = p.get("page_path", "—")
        sess    = float(p.get("sessions") or 0)
        bounce  = float(p.get("bounce_rate") or 0) * 100
        pct     = sess / max_s * 100
        if bounce < 35:
            bs, star = "#EAF3DE;color:#3B6D11", " ⭐"
        elif bounce < 55:
            bs, star = "#FFF8CC;color:#666", ""
        else:
            bs, star = "#FCEBEB;color:#A32D2D", ""
        out += (
            f'<div class="page-bar">'
            f'<div class="page-name">{path}</div>'
            f'<div class="page-track"><div class="page-fill" style="width:{pct:.0f}%"></div></div>'
            f'<div class="page-val">{int(sess)}</div>'
            f'<span class="page-badge" style="{bs}">Rebote {bounce:.1f}%{star}</span>'
            f'</div>\n'
        )
    return out

def page_quality_rows():
    out = ""
    for p in pages_quality:
        path   = p.get("page_path", "—")
        sess   = int(float(p.get("sessions") or 0))
        bounce = float(p.get("bounce_rate") or 0) * 100
        dur    = float(p.get("average_session_duration") or 0)
        dur_s  = f"{int(dur//60)}m {int(dur%60):02d}s"
        out += (
            f'<tr><td>{path}</td>'
            f'<td class="r">{sess}</td>'
            f'<td class="r">{bounce:.1f}%</td>'
            f'<td class="r"><strong>{dur_s}</strong></td>'
            f'<td class="r">{page_type(path)}</td></tr>\n'
        )
    return out

def camp_rows():
    out = ""
    for name, d in camps.items():
        cpconv = d["cost"]/d["conv"] if d["conv"] else 0
        st     = "Activa" if "ENABLED" in d["status"] else "Pausada"
        sc     = "#EAF3DE;color:#3B6D11" if st == "Activa" else "#FCEBEB;color:#A32D2D"
        warn   = " ⚠️" if st == "Pausada" else ""
        out += (
            f'<tr><td><strong>{name}</strong></td>'
            f'<td><span class="badge" style="background:{sc}">{st}{warn}</span></td>'
            f'<td class="r">€{d["cost"]:.2f}</td>'
            f'<td class="r">{int(d["clicks"])}</td>'
            f'<td class="r"><strong>{int(d["conv"])}</strong></td>'
            f'<td class="r">{"€"+f"{cpconv:.2f}" if d["conv"] else "—"}</td></tr>\n'
        )
    return out

def kw_rows():
    out = ""
    for kw in kw_top:
        q   = kw.get("query", "—")
        imp = int(float(kw.get("impressions") or 0))
        clk = int(float(kw.get("clicks") or 0))
        ctr = float(kw.get("ctr") or 0) * 100
        pos = float(kw.get("position") or 0)
        pc  = "pos-top" if pos <= 3 else ("pos-mid" if pos <= 10 else "pos-low")
        out += (
            f'<tr><td><strong>{q}</strong></td>'
            f'<td class="r">{imp:,}</td>'
            f'<td class="r">{clk}</td>'
            f'<td class="r">{ctr:.1f}%</td>'
            f'<td class="r"><span class="pos-pill {pc}">#{pos:.1f}</span></td></tr>\n'
        )
    return out

def ch_val(d, key):
    """Devuelve valor de canal o '—' si no hay datos de dimensión."""
    return fmtn(d[key]) if channels_available else "—"

# ── Valores a inyectar ────────────────────────────────────────────────────────
valores = {
    # Periodo
    "header_period": f"{date_from} – {date_to} vs. {date_fr_p} – {date_to_p}",
    "footer_date":   today.strftime("%d %B %Y"),
    # GA4
    "ga4_sessions_curr":  fmtn(C["sessions"]),
    "ga4_sessions_prev":  fmtn(P["sessions"]),
    "ga4_users_curr":     fmtn(C["users"]),
    "ga4_users_prev":     fmtn(P["users"]),
    "ga4_new_users_curr": fmtn(C["users"]),  # proxy: active_users
    "ga4_new_users_prev": fmtn(P["users"]),  # proxy: active_users
    "ga4_pageviews_curr": fmtn(C["pageviews"]),
    "ga4_pageviews_prev": fmtn(P["pageviews"]),
    "ga4_eng_curr":       f"{C['eng']:.1f}",
    "ga4_eng_prev":       f"{P['eng']:.1f}",
    "ga4_bounce_curr":    f"{C['bounce']:.1f}",
    "ga4_bounce_prev":    f"{P['bounce']:.1f}",
    "ga4_dur_curr":       fmt_dur(C["dur"]),
    "ga4_dur_prev":       "—",
    "ga4_ai_curr":        str(int(ch_c.get("ai", 0))) if channels_available else "—",
    "ga4_ai_prev":        str(int(ch_p.get("ai", 0))) if channels_available else "—",
    # Canales
    "ch_direct_curr":         ch_val(ch_c, "direct"),
    "ch_direct_prev":         ch_val(ch_p, "direct"),
    "ch_organic_curr":        ch_val(ch_c, "organic"),
    "ch_organic_prev":        ch_val(ch_p, "organic"),
    "ch_paid_curr":           ch_val(ch_c, "paid_search"),
    "ch_paid_prev":           ch_val(ch_p, "paid_search"),
    "ch_cross_curr":          ch_val(ch_c, "cross"),
    "ch_cross_prev":          ch_val(ch_p, "cross"),
    "ch_social_paid_curr":    ch_val(ch_c, "paid_social"),
    "ch_social_paid_prev":    ch_val(ch_p, "paid_social"),
    "ch_ref_curr":            ch_val(ch_c, "referral"),
    "ch_ref_prev":            ch_val(ch_p, "referral"),
    "ch_social_organic_curr": ch_val(ch_c, "organic_social"),
    "ch_social_organic_prev": ch_val(ch_p, "organic_social"),
    "ch_ai_curr":             ch_val(ch_c, "ai"),
    "ch_ai_prev":             ch_val(ch_p, "ai"),
    # Google Ads
    "ads_cost_curr":   f"€{AC['cost']:,.2f}".replace(",", "."),
    "ads_cost_prev":   f"€{AP['cost']:,.2f}".replace(",", "."),
    "ads_clicks_curr": fmtn(AC["clicks"]),
    "ads_clicks_prev": fmtn(AP["clicks"]),
    "ads_impr_curr":   fmtn(AC["impr"]),
    "ads_impr_prev":   "—",
    "ads_ctr_curr":    f"{AC['ctr']:.2f}",
    "ads_ctr_prev":    "—",
    "ads_cpc_curr":    f"€{AC['cpc']:.2f}",
    "ads_cpc_prev":    "—",
    "ads_conv_curr":   str(int(AC["conv"])),
    "ads_conv_prev":   str(int(AP["conv"])),
    "ads_cpconv_curr": f"€{AC['cpconv']:.2f}",
    "ads_cpconv_prev": f"€{AP['cpconv']:.2f}",
    "ads_daily_curr":  f"€{AC['daily']:.2f}",
    "ads_daily_prev":  f"€{AP['daily']:.2f}",
    # Search Console
    "sc_impr_curr":   fmtn(SC["impr"]),
    "sc_impr_prev":   fmtn(SP["impr"]),
    "sc_clicks_curr": str(int(SC["clicks"])),
    "sc_clicks_prev": str(int(SP["clicks"])),
    "sc_ctr_curr":    f"{SC['ctr']:.2f}",
    "sc_ctr_prev":    f"{SP['ctr']:.2f}",
    "sc_pos_curr":    f"{SC['pos']:.1f}",
    "sc_pos_prev":    f"{SP['pos']:.1f}",
    # CRM
    "crm_total":   str(len(crm_leads)) if crm_ok else "—",
    "crm_dead":    str(len(crm_dead))  if crm_ok else "—",
    "crm_active":  str(len(crm_active)) if crm_ok else "—",
    "crm_iql":     str(len(crm_iql))  if crm_ok else "—",
    "crm_mql":     str(len(crm_mql))  if crm_ok else "—",
    "crm_sql":     str(len(crm_sql) + len(crm_qual)) if crm_ok else "—",
    "crm_cpl":     (f"€{AC['cost']/len(crm_active):.0f}" if crm_ok and crm_active else "—"),
    "crm_subtitle": (
        f"{len(crm_leads)} leads últimos 30 días · {len(crm_active)} activos · "
        f"{len(crm_dead)} descartados · Actualizado {today.strftime('%d/%m/%Y')}"
        if crm_ok else "CRM no disponible"
    ),
}

# ── Leer template ─────────────────────────────────────────────────────────────
with open(HTML_FILE, encoding="utf-8") as f:
    html = f.read()

# ── Reemplazar marcadores WS ──────────────────────────────────────────────────
replaced = 0
missing  = []
for mid, val in valores.items():
    pat = f"<!-- WS:{mid} -->.*?<!-- /WS:{mid} -->"
    rep = f"<!-- WS:{mid} -->{val}<!-- /WS:{mid} -->"
    html, n = re.subn(pat, rep, html, flags=re.DOTALL)
    replaced += n
    if n == 0:
        missing.append(mid)

# ── Tablas dinámicas ──────────────────────────────────────────────────────────
for marker, fn, label in [
    ("CAMP_ROWS",         camp_rows,         f"campañas ({len(camps)})"),
    ("KW_ROWS",           kw_rows,           f"keywords ({len(kw_top)})"),
    ("PAGE_BARS",         page_bars,         f"páginas top ({len(pages_top)})"),
    ("PAGE_QUALITY_ROWS", page_quality_rows, f"páginas calidad ({len(pages_quality)})"),
    ("CRM_ROWS",          crm_rows,          f"CRM pipeline ({len(crm_pipeline)})"),
]:
    pat = f"<!-- WS:{marker} -->.*?<!-- /WS:{marker} -->"
    rep = f"<!-- WS:{marker} -->\n{fn()}<!-- /WS:{marker} -->"
    html, n = re.subn(pat, rep, html, flags=re.DOTALL)
    replaced += n
    status = "✅" if n else "⚠️ NO ENCONTRADO"
    print(f"  {status} Tabla {label}")

# ── Badge de generación ───────────────────────────────────────────────────────
html = re.sub(
    r'(class="auto-badge">)[^<]+(</div>)',
    f'\\1⚡ Actualizado automáticamente · {generated}\\2',
    html
)

# ── Escribir ──────────────────────────────────────────────────────────────────
with open(HTML_FILE, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\n✅ Dashboard actualizado — {generated}")
print(f"   Reemplazos realizados: {replaced}")
if missing:
    print(f"   Marcadores no encontrados ({len(missing)}): {', '.join(missing[:5])}")
print(f"   GA4:  {fmtn(C['sessions'])} sesiones | {fmtn(C['pageviews'])} páginas")
print(f"   Ads:  €{AC['cost']:.2f} | {int(AC['conv'])} conversiones")
print(f"   SC:   {int(SC['clicks'])} clics | pos {SC['pos']:.1f}")
print(f"   Canales disponibles: {'sí' if channels_available else 'no (fallback a —)'}")
