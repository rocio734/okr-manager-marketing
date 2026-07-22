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
from datetime import datetime, timedelta

WINDSOR_API_KEY = os.environ["WINDSOR_API_KEY"]
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

def fetch(connector, fields, start=30, end=0):
    t  = datetime.today()
    df = (t - timedelta(days=start)).strftime("%Y-%m-%d")
    dt = (t - timedelta(days=end)).strftime("%Y-%m-%d")
    return _fetch(connector, fields, df, dt)

def fetch_safe(connector, fields, start=30, end=0, fallback=None):
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
    ("CAMP_ROWS", camp_rows, f"campañas ({len(camps)})"),
    ("KW_ROWS",   kw_rows,   f"keywords ({len(kw_top)})"),
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
