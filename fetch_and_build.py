"""
Etendo Dashboard — Auto-actualización diaria
Lee analytics-dashboard/index.html como template,
reemplaza los marcadores <!-- WS:id --> con datos frescos de Windsor.ai,
y escribe el resultado de vuelta al mismo archivo.

Tabs estáticos que NO se tocan:
  - CRM & Leads
  - Mejoras sugeridas
  - Conclusiones
  - Top Páginas

Tabs que se actualizan automáticamente:
  - Web (GA4): KPIs + tabla de canales
  - Google Ads: KPIs + tabla por campaña
  - Search Console: KPIs + top 20 keywords
"""
import requests
import os
import re
from datetime import datetime, timedelta

WINDSOR_API_KEY = os.environ["WINDSOR_API_KEY"]
BASE      = "https://connectors.windsor.ai"
REPO_DIR  = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(REPO_DIR, "analytics-dashboard", "index.html")

def _fetch(connector, fields, date_from, date_to):
    params = {"api_key": WINDSOR_API_KEY, "fields": ",".join(fields),
              "date_from": date_from, "date_to": date_to}
    r = requests.get(f"{BASE}/{connector}", params=params, timeout=40)
    r.raise_for_status()
    d = r.json()
    return d.get("data", d) if isinstance(d, dict) else d

def fetch(connector, fields, start=30, end=0):
    t  = datetime.today()
    df = (t - timedelta(days=start)).strftime("%Y-%m-%d")
    dt = (t - timedelta(days=end)).strftime("%Y-%m-%d")
    return _fetch(connector, fields, df, dt)

fsum = lambda rows, f: sum(float(r.get(f) or 0) for r in rows)
def favg(rows, f):
    v = [float(r.get(f) or 0) for r in rows if r.get(f) is not None]
    return sum(v)/len(v) if v else 0

fmt_dur = lambda s: f"{int(s//60)}m {int(s%60):02d}s"
fmtn    = lambda v: f"{int(v):,}".replace(",", ".")

def channel_sum(rows, name):
    return sum(float(r.get("sessions") or 0) for r in rows
               if name.lower() in str(r.get("session_default_channel_group","")).lower())

# ── Fetch ─────────────────────────────────────────────────────────────────────
print("→ GA4 actual...")
ga4c = fetch("googleanalytics4", ["sessions","active_users","new_users",
    "screen_page_views","engagement_rate","bounce_rate","average_session_duration"])
ga4p = fetch("googleanalytics4", ["sessions","active_users","new_users",
    "screen_page_views","engagement_rate","bounce_rate"], start=60, end=30)

print("→ GA4 canales...")
ch_c_rows = fetch("googleanalytics4", ["sessions","session_default_channel_group"])
ch_p_rows = fetch("googleanalytics4", ["sessions","session_default_channel_group"], start=60, end=30)

print("→ Google Ads...")
adsc = fetch("google_ads", ["cost","clicks","impressions","conversions","campaign_name","campaign_status"])
adsp = fetch("google_ads", ["cost","clicks","conversions","campaign_name"], start=60, end=30)

print("→ Search Console...")
scc  = fetch("searchconsole", ["clicks","impressions","ctr","position"])
scp  = fetch("searchconsole", ["clicks","impressions","ctr","position"], start=60, end=30)
kws  = fetch("searchconsole", ["query","clicks","impressions","ctr","position"])

# ── Agregados ─────────────────────────────────────────────────────────────────
C = dict(sessions=fsum(ga4c,"sessions"), users=fsum(ga4c,"active_users"),
         new_users=fsum(ga4c,"new_users"), pageviews=fsum(ga4c,"screen_page_views"),
         eng=favg(ga4c,"engagement_rate")*100, bounce=favg(ga4c,"bounce_rate")*100,
         dur=favg(ga4c,"average_session_duration"))
P = dict(sessions=fsum(ga4p,"sessions"), users=fsum(ga4p,"active_users"),
         new_users=fsum(ga4p,"new_users"), pageviews=fsum(ga4p,"screen_page_views"),
         eng=favg(ga4p,"engagement_rate")*100, bounce=favg(ga4p,"bounce_rate")*100)

ch_map = {"direct":"direct","organic":"organic search","paid_search":"paid search",
          "cross":"cross-network","paid_social":"paid social","referral":"referral",
          "organic_social":"organic social","ai":"ai"}
ch_c = {k: channel_sum(ch_c_rows, v) for k,v in ch_map.items()}
ch_p = {k: channel_sum(ch_p_rows, v) for k,v in ch_map.items()}

AC = dict(cost=fsum(adsc,"cost"), clicks=fsum(adsc,"clicks"),
          impr=fsum(adsc,"impressions"), conv=fsum(adsc,"conversions"))
AC["ctr"]    = AC["clicks"]/AC["impr"]*100 if AC["impr"]   else 0
AC["cpc"]    = AC["cost"]/AC["clicks"]      if AC["clicks"] else 0
AC["cpconv"] = AC["cost"]/AC["conv"]        if AC["conv"]   else 0
AC["daily"]  = AC["cost"]/30
AP = dict(cost=fsum(adsp,"cost"), clicks=fsum(adsp,"clicks"), conv=fsum(adsp,"conversions"))
AP["cpconv"] = AP["cost"]/AP["conv"] if AP["conv"] else 0
AP["daily"]  = AP["cost"]/30

camps = {}
for r in adsc:
    n = r.get("campaign_name","—")
    if n not in camps:
        camps[n] = {"cost":0,"clicks":0,"conv":0,"status":r.get("campaign_status","—")}
    camps[n]["cost"]  += float(r.get("cost") or 0)
    camps[n]["clicks"]+= float(r.get("clicks") or 0)
    camps[n]["conv"]  += float(r.get("conversions") or 0)

SC = dict(clicks=fsum(scc,"clicks"), impr=fsum(scc,"impressions"),
          ctr=favg(scc,"ctr")*100, pos=favg(scc,"position"))
SP = dict(clicks=fsum(scp,"clicks"), impr=fsum(scp,"impressions"),
          ctr=favg(scp,"ctr")*100, pos=favg(scp,"position"))
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
        st = "Activa" if "ENABLED" in d["status"] else "Pausada"
        sc = "#EAF3DE;color:#3B6D11" if st=="Activa" else "#FCEBEB;color:#A32D2D"
        warn = " ⚠️" if st=="Pausada" else ""
        out += (f'<tr><td><strong>{name}</strong></td>'
                f'<td><span class="badge" style="background:{sc}">{st}{warn}</span></td>'
                f'<td class="r">€{d["cost"]:.2f}</td><td class="r">{int(d["clicks"])}</td>'
                f'<td class="r"><strong>{int(d["conv"])}</strong></td>'
                f'<td class="r">{"€"+f"{cpconv:.2f}" if d["conv"] else "—"}</td></tr>\n')
    return out

def kw_rows():
    out = ""
    for kw in kw_top:
        q   = kw.get("query","—")
        imp = int(float(kw.get("impressions") or 0))
        clk = int(float(kw.get("clicks") or 0))
        ctr = float(kw.get("ctr") or 0)*100
        pos = float(kw.get("position") or 0)
        pc  = "pos-top" if pos<=3 else ("pos-mid" if pos<=10 else "pos-low")
        out += (f'<tr><td><strong>{q}</strong></td>'
                f'<td class="r">{imp:,}</td><td class="r">{clk}</td>'
                f'<td class="r">{ctr:.1f}%</td>'
                f'<td class="r"><span class="pos-pill {pc}">#{pos:.1f}</span></td></tr>\n')
    return out

# ── Valores a inyectar ────────────────────────────────────────────────────────
valores = {
    "header_period":  f"{date_from} – {date_to} vs. {date_fr_p} – {date_to_p}",
    "footer_date":    today.strftime("%d %B %Y"),
    "ga4_sessions_curr": fmtn(C["sessions"]),
    "ga4_sessions_prev": fmtn(P["sessions"]),
    "ga4_users_curr":    fmtn(C["users"]),
    "ga4_users_prev":    fmtn(P["users"]),
    "ga4_new_users_curr":fmtn(C["new_users"]),
    "ga4_new_users_prev":fmtn(P["new_users"]),
    "ga4_pageviews_curr":fmtn(C["pageviews"]),
    "ga4_pageviews_prev":fmtn(P["pageviews"]),
    "ga4_eng_curr":   f"{C['eng']:.1f}",
    "ga4_eng_prev":   f"{P['eng']:.1f}",
    "ga4_bounce_curr":f"{C['bounce']:.1f}",
    "ga4_bounce_prev":f"{P['bounce']:.1f}",
    "ga4_dur_curr":   fmt_dur(C["dur"]),
    "ga4_dur_prev":   "—",
    "ga4_ai_curr":    str(int(ch_c.get("ai", 0))),
    "ga4_ai_prev":    str(int(ch_p.get("ai", 0))),
    "ch_direct_curr": fmtn(ch_c["direct"]),
    "ch_direct_prev": fmtn(ch_p["direct"]),
    "ch_organic_curr":fmtn(ch_c["organic"]),
    "ch_organic_prev":fmtn(ch_p["organic"]),
    "ch_paid_curr":   fmtn(ch_c["paid_search"]),
    "ch_paid_prev":   fmtn(ch_p["paid_search"]),
    "ch_cross_curr":  fmtn(ch_c["cross"]),
    "ch_cross_prev":  fmtn(ch_p["cross"]),
    "ch_social_paid_curr": fmtn(ch_c["paid_social"]),
    "ch_social_paid_prev": fmtn(ch_p["paid_social"]),
    "ch_ref_curr":    fmtn(ch_c["referral"]),
    "ch_ref_prev":    fmtn(ch_p["referral"]),
    "ch_social_organic_curr": fmtn(ch_c["organic_social"]),
    "ch_social_organic_prev": fmtn(ch_p["organic_social"]),
    "ch_ai_curr":     str(int(ch_c.get("ai", 0))),
    "ch_ai_prev":     str(int(ch_p.get("ai", 0))),
    "ads_cost_curr":  f"€{AC['cost']:,.2f}".replace(",", "."),
    "ads_cost_prev":  f"€{AP['cost']:,.2f}".replace(",", "."),
    "ads_clicks_curr":fmtn(AC["clicks"]),
    "ads_clicks_prev":fmtn(AP["clicks"]),
    "ads_impr_curr":  fmtn(AC["impr"]),
    "ads_impr_prev":  "—",
    "ads_ctr_curr":   f"{AC['ctr']:.2f}",
    "ads_ctr_prev":   "—",
    "ads_cpc_curr":   f"€{AC['cpc']:.2f}",
    "ads_cpc_prev":   "—",
    "ads_conv_curr":  str(int(AC["conv"])),
    "ads_conv_prev":  str(int(AP["conv"])),
    "ads_cpconv_curr":f"€{AC['cpconv']:.2f}",
    "ads_cpconv_prev":f"€{AP['cpconv']:.2f}",
    "ads_daily_curr": f"€{AC['daily']:.2f}",
    "ads_daily_prev": f"€{AP['daily']:.2f}",
    "sc_impr_curr":   fmtn(SC["impr"]),
    "sc_impr_prev":   fmtn(SP["impr"]),
    "sc_clicks_curr": str(int(SC["clicks"])),
    "sc_clicks_prev": str(int(SP["clicks"])),
    "sc_ctr_curr":    f"{SC['ctr']:.2f}",
    "sc_ctr_prev":    f"{SP['ctr']:.2f}",
    "sc_pos_curr":    f"{SC['pos']:.1f}",
    "sc_pos_prev":    f"{SP['pos']:.1f}",
}

# ── Leer template y reemplazar ────────────────────────────────────────────────
with open(HTML_FILE, encoding="utf-8") as f:
    html = f.read()

replaced = 0
for mid, val in valores.items():
    pat = f"<!-- WS:{mid} -->.*?<!-- /WS:{mid} -->"
    rep = f"<!-- WS:{mid} -->{val}<!-- /WS:{mid} -->"
    html, n = re.subn(pat, rep, html, flags=re.DOTALL)
    replaced += n
    if n == 0:
        print(f"  ⚠️  Marcador no encontrado: {mid}")

# Tablas dinámicas
for pat, rep_fn, label in [
    (r"<!-- WS:CAMP_ROWS -->.*?<!-- /WS:CAMP_ROWS -->", camp_rows, f"campañas ({len(camps)})"),
    (r"<!-- WS:KW_ROWS -->.*?<!-- /WS:KW_ROWS -->",    kw_rows,   f"keywords ({len(kw_top)})"),
]:
    rep = f"<!-- WS:{pat.split(':')[1].split(' ')[0]} -->\n{rep_fn()}<!-- /WS:{pat.split(':')[1].split(' ')[0]} -->"
    marker = pat.split(':')[1].split(' ')[0]
    rep = f"<!-- WS:{marker} -->\n{rep_fn()}<!-- /WS:{marker} -->"
    html, n = re.subn(pat, rep, html, flags=re.DOTALL)
    replaced += n
    print(f"  {'✅' if n else '⚠️ '} Tabla {label} {'actualizada' if n else 'NO ENCONTRADA'}")

# Badge de fecha
html = re.sub(r'(class="auto-badge">)[^<]+(</div>)',
              f'\\1⚡ Actualizado automáticamente · {generated}\\2', html)

with open(HTML_FILE, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\n✅ Dashboard actualizado — {generated}")
print(f"   Reemplazos: {replaced}")
print(f"   GA4:  {fmtn(C['sessions'])} sesiones | {fmtn(C['pageviews'])} páginas")
print(f"   Ads:  €{AC['cost']:.2f} | {int(AC['conv'])} conversiones")
print(f"   SC:   {int(SC['clicks'])} clics | pos {SC['pos']:.1f}")
