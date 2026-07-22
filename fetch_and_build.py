"""
Etendo Dashboard — Auto-generación diaria
Llama a Windsor.ai y genera analytics-dashboard/index.html
"""
import requests
import os
from datetime import datetime, timedelta

WINDSOR_API_KEY = os.environ["WINDSOR_API_KEY"]
BASE = "https://connectors.windsor.ai"

# Ruta de salida — carpeta del repo donde está el dashboard en Render
OUT_DIR  = os.path.join(os.path.dirname(__file__), "analytics-dashboard")
OUT_FILE = os.path.join(OUT_DIR, "index.html")
LOGO_FILE = os.path.join(os.path.dirname(__file__), "logo_b64.txt")

os.makedirs(OUT_DIR, exist_ok=True)

# ── API helpers ───────────────────────────────────────────────────────────────
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

def dates_curr():
    t = datetime.today()
    return (t - timedelta(days=30)).strftime("%Y-%m-%d"), t.strftime("%Y-%m-%d")

def dates_prev():
    t = datetime.today()
    return (t - timedelta(days=60)).strftime("%Y-%m-%d"), (t - timedelta(days=30)).strftime("%Y-%m-%d")

def fetch_curr(connector, fields):
    df, dt = dates_curr()
    return _fetch(connector, fields, df, dt)

def fetch_prev(connector, fields):
    df, dt = dates_prev()
    return _fetch(connector, fields, df, dt)

def fsum(rows, f):   return sum(float(r.get(f) or 0) for r in rows)
def favg(rows, f):
    v = [float(r.get(f) or 0) for r in rows if r.get(f) is not None]
    return sum(v)/len(v) if v else 0

def delta_pct(curr, prev, invert=False):
    if prev == 0: return "nuevo", "up"
    d = (curr - prev) / abs(prev) * 100
    cls = ("up" if d > 0 else "down") if not invert else ("up" if d < 0 else "down")
    return f"{'+'if d>0 else ''}{d:.1f}%", cls

def delta_pp(curr, prev, invert=False):
    if prev == 0: return "—", "neu"
    d = curr - prev
    cls = ("up" if d > 0 else "down") if not invert else ("up" if d < 0 else "down")
    return f"{'+'if d>0 else ''}{d:.1f}pp", cls

def fmt_dur(s):
    return f"{int(s//60)}m {int(s%60):02d}s"

# ── Fetch ─────────────────────────────────────────────────────────────────────
print("→ GA4 actual..."); ga4c = fetch_curr("googleanalytics4",
    ["sessions","active_users","screen_page_views","engagement_rate","bounce_rate","average_session_duration"])
print("→ GA4 anterior..."); ga4p = fetch_prev("googleanalytics4",
    ["sessions","screen_page_views","engagement_rate","bounce_rate"])

print("→ Google Ads actual..."); adsc = fetch_curr("google_ads",
    ["cost","clicks","impressions","conversions","campaign_name","campaign_status"])
print("→ Google Ads anterior..."); adsp = fetch_prev("google_ads",
    ["cost","clicks","conversions","campaign_name"])

print("→ Search Console actual..."); scc = fetch_curr("searchconsole",
    ["clicks","impressions","ctr","position"])
print("→ Search Console anterior..."); scp = fetch_prev("searchconsole",
    ["clicks","impressions","ctr","position"])

print("→ Keywords..."); kws = fetch_curr("searchconsole",
    ["query","clicks","impressions","ctr","position"])

# ── Agrega ────────────────────────────────────────────────────────────────────
C = dict(sessions=fsum(ga4c,"sessions"), pageviews=fsum(ga4c,"screen_page_views"),
         eng=favg(ga4c,"engagement_rate")*100, bounce=favg(ga4c,"bounce_rate")*100,
         dur=favg(ga4c,"average_session_duration"))
P = dict(sessions=fsum(ga4p,"sessions"), pageviews=fsum(ga4p,"screen_page_views"),
         eng=favg(ga4p,"engagement_rate")*100, bounce=favg(ga4p,"bounce_rate")*100)

AC = dict(cost=fsum(adsc,"cost"), clicks=fsum(adsc,"clicks"),
          impr=fsum(adsc,"impressions"), conv=fsum(adsc,"conversions"))
AC["ctr"]  = AC["clicks"]/AC["impr"]*100  if AC["impr"]   else 0
AC["cpc"]  = AC["cost"]/AC["clicks"]       if AC["clicks"] else 0
AC["cpconv"]= AC["cost"]/AC["conv"]        if AC["conv"]   else 0
AP = dict(cost=fsum(adsp,"cost"), clicks=fsum(adsp,"clicks"), conv=fsum(adsp,"conversions"))
AP["cpconv"]= AP["cost"]/AP["conv"] if AP["conv"] else 0

camps = {}
for r in adsc:
    n = r.get("campaign_name","—")
    if n not in camps:
        camps[n] = {"cost":0,"clicks":0,"conv":0,"status":r.get("campaign_status","—")}
    camps[n]["cost"]  += float(r.get("cost") or 0)
    camps[n]["clicks"]+= float(r.get("clicks") or 0)
    camps[n]["conv"]  += float(r.get("conversions") or 0)

SC = dict(clicks=fsum(scc,"clicks"), impr=fsum(scc,"impressions"),
          ctr=favg(scc,"ctr")*100,   pos=favg(scc,"position"))
SP = dict(clicks=fsum(scp,"clicks"), impr=fsum(scp,"impressions"),
          ctr=favg(scp,"ctr")*100,   pos=favg(scp,"position"))

kw_top = sorted(kws, key=lambda x: float(x.get("impressions") or 0), reverse=True)[:20]

# ── Fechas ────────────────────────────────────────────────────────────────────
today     = datetime.today()
date_to   = today.strftime("%d/%m/%Y")
date_from = (today - timedelta(days=30)).strftime("%d/%m/%Y")
generated = today.strftime("%d/%m/%Y %H:%M UTC")

# ── Logo ─────────────────────────────────────────────────────────────────────
with open(LOGO_FILE) as f:
    logo = f.read().strip()

# ── HTML helpers ─────────────────────────────────────────────────────────────
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--y:#FFD700;--yl:#FFF8CC;--bk:#0D0D0D;--gd:#333;--gm:#666;--gl:#F5F5F5;--gb:#E8E8E8;--wh:#fff;--gn:#1BAF7A;--rd:#E24B4A}
body{font-family:'Inter',sans-serif;background:var(--wh);color:var(--bk);padding:2.5rem;max-width:1100px;margin:0 auto}
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:2rem;padding-bottom:1.5rem;border-bottom:3px solid var(--y)}
.logo-area{display:flex;align-items:center;gap:14px}
.logo-area img{width:52px;height:52px;object-fit:contain}
.logo-text h1{font-size:20px;font-weight:800;letter-spacing:-.5px}
.logo-text p{font-size:12px;color:var(--gm);margin-top:2px}
.header-meta{text-align:right}
.period{font-size:13px;font-weight:600}
.verified{font-size:11px;color:var(--gn);margin-top:4px}
.auto-badge{display:inline-block;font-size:10px;padding:2px 8px;border-radius:10px;background:#EAF3DE;color:#3B6D11;font-weight:700;margin-top:4px}
.tabs{display:flex;gap:6px;margin-bottom:2rem;flex-wrap:wrap}
.tab{padding:8px 18px;border-radius:6px;border:1.5px solid var(--gb);font-size:13px;font-weight:500;cursor:pointer;background:var(--wh);color:var(--gm);font-family:'Inter',sans-serif;transition:all .15s}
.tab:hover{border-color:var(--y);color:var(--bk)}
.tab.active{background:var(--y);color:var(--bk);border-color:var(--y);font-weight:700}
.panel{display:none}.panel.active{display:block}
.sl{font-size:10px;font-weight:700;color:var(--gm);text-transform:uppercase;letter-spacing:.1em;margin:2rem 0 .3rem;display:flex;align-items:center;gap:8px}
.sl::after{content:'';flex:1;height:1px;background:var(--gb)}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:2rem}
.kpi{background:var(--wh);border:1.5px solid var(--gb);border-radius:10px;padding:1rem;position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--y)}
.kpi-label{font-size:11px;font-weight:700;color:var(--gd);margin-bottom:2px;text-transform:uppercase;letter-spacing:.04em}
.kpi-def{font-size:11px;color:var(--gm);margin-bottom:8px;line-height:1.4}
.kpi-row{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.kpi-prev{font-size:12px;color:#bbb;text-decoration:line-through}
.kpi-val{font-size:22px;font-weight:800;letter-spacing:-1px}
.kpi-delta{font-size:11px;padding:2px 7px;border-radius:4px;font-weight:700}
.up{background:#EAF3DE;color:#3B6D11}.down{background:#FCEBEB;color:#A32D2D}.neu{background:var(--gl);color:var(--gm)}
.ch-table{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:1.5rem}
.ch-table thead tr{background:var(--bk)}
.ch-table th{text-align:left;font-size:10px;font-weight:700;color:var(--y);text-transform:uppercase;letter-spacing:.07em;padding:9px 10px}
.ch-table th.r{text-align:right}
.ch-table td{padding:8px 10px;border-bottom:1px solid var(--gb);vertical-align:middle}
.ch-table td.r{text-align:right;font-variant-numeric:tabular-nums}
.ch-table tr:hover td{background:var(--yl)}
.ch-table tr:last-child td{border-bottom:none}
.badge{display:inline-block;font-size:10px;padding:3px 8px;border-radius:4px;font-weight:600}
.pos-pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:10px;font-weight:700}
.pos-top{background:#EAF3DE;color:#3B6D11}.pos-mid{background:#FFF8CC;color:#666}.pos-low{background:#FCEBEB;color:#A32D2D}
.footer{margin-top:3rem;padding-top:1.5rem;border-top:3px solid var(--y);display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--gm)}
.footer img{height:28px;object-fit:contain}
"""

def kpi(label, defn, val, prev, dstr, dcls, pre="", suf=""):
    return f'''<div class="kpi">
      <p class="kpi-label">{label}</p><p class="kpi-def">{defn}</p>
      <div class="kpi-row">
        <span class="kpi-prev">{pre}{prev}{suf}</span>
        <span class="kpi-val">{pre}{val}{suf}</span>
        <span class="kpi-delta {dcls}">{dstr}</span>
      </div></div>'''

def camp_rows():
    out = ""
    for name, d in camps.items():
        cconv = d["cost"]/d["conv"] if d["conv"] else 0
        st    = "Activa" if "ENABLED" in d["status"] else "Pausada"
        sc    = "#EAF3DE;color:#3B6D11" if st=="Activa" else "#FCEBEB;color:#A32D2D"
        out += f"""<tr>
          <td><strong>{name}</strong></td>
          <td><span class="badge" style="background:{sc}">{st}</span></td>
          <td class="r">€{d['cost']:.2f}</td><td class="r">{int(d['clicks'])}</td>
          <td class="r"><strong>{int(d['conv'])}</strong></td>
          <td class="r">{'€'+f"{cconv:.2f}" if d['conv'] else '—'}</td>
        </tr>"""
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
        out += f"""<tr>
          <td><strong>{q}</strong></td>
          <td class="r">{imp:,}</td><td class="r">{clk}</td>
          <td class="r">{ctr:.1f}%</td>
          <td class="r"><span class="pos-pill {pc}">#{pos:.1f}</span></td>
        </tr>"""
    return out

# ── Deltas ────────────────────────────────────────────────────────────────────
ds,  cs  = delta_pct(C["sessions"],  P["sessions"])
dpv, cpv = delta_pct(C["pageviews"], P["pageviews"])
de,  ce  = delta_pct(C["eng"],       P["eng"])
db,  cb  = delta_pct(C["bounce"],    P["bounce"],     invert=True)
dco, cco = delta_pct(AC["cost"],     AP["cost"],      invert=True)
dcv, ccv = delta_pct(AC["conv"],     AP["conv"])
dcc, ccc = delta_pct(AC["cpconv"],   AP["cpconv"],    invert=True)
dsi, csi = delta_pct(SC["clicks"],   SP["clicks"])
dim, cim = delta_pct(SC["impr"],     SP["impr"])
dct, cct = delta_pp(SC["ctr"],       SP["ctr"])
dpo, cpo = delta_pp(SC["pos"],       SP["pos"],       invert=True)

# ── Genera HTML ───────────────────────────────────────────────────────────────
html = f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard Etendo — {date_to}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head><body>

<div class="header">
  <div class="logo-area">
    <img src="data:image/png;base64,{logo}" alt="Etendo">
    <div class="logo-text">
      <h1>Etendo ERP</h1>
      <p>Dashboard Digital — GA4 · Google Ads · Search Console</p>
    </div>
  </div>
  <div class="header-meta">
    <div class="period">{date_from} – {date_to}</div>
    <div class="verified">✓ Datos en tiempo real via Windsor.ai</div>
    <div class="auto-badge">⚡ Actualizado automáticamente · {generated}</div>
  </div>
</div>

<div class="tabs">
  <button class="tab active" onclick="showTab('web',this)">Web (GA4)</button>
  <button class="tab" onclick="showTab('ads',this)">Google Ads</button>
  <button class="tab" onclick="showTab('seo',this)">Search Console</button>
</div>

<div id="web" class="panel active">
  <div class="sl">Métricas clave — últimos 30 días vs 30 días anteriores</div>
  <div class="kpi-grid">
    {kpi("Visitas totales","Veces que alguien entró a la web",f"{C['sessions']:,.0f}",f"{P['sessions']:,.0f}",ds,cs)}
    {kpi("Páginas vistas","Total de páginas abiertas",f"{C['pageviews']:,.0f}",f"{P['pageviews']:,.0f}",dpv,cpv)}
    {kpi("% Interacción","Visitas con interacción real",f"{C['eng']:.1f}",f"{P['eng']:.1f}",de,ce,suf="%")}
    {kpi("Tasa de rebote","Menor es mejor",f"{C['bounce']:.1f}",f"{P['bounce']:.1f}",db,cb,suf="%")}
    {kpi("Tiempo medio","Duración media por visita",fmt_dur(C['dur']),"—","—","neu")}
  </div>
</div>

<div id="ads" class="panel">
  <div class="sl">Inversión — últimos 30 días vs 30 días anteriores</div>
  <div class="kpi-grid">
    {kpi("Inversión total","Gasto en Google Ads",f"{AC['cost']:,.2f}",f"{AP['cost']:,.2f}",dco,cco,pre="€")}
    {kpi("Clics","Clics en anuncios",f"{AC['clicks']:,.0f}",f"{AP['clicks']:,.0f}",*delta_pct(AC['clicks'],AP['clicks']))}
    {kpi("Conversiones","Formularios completados",f"{AC['conv']:.0f}",f"{AP['conv']:.0f}",dcv,ccv)}
    {kpi("Coste/conv.","Coste medio por lead",f"{AC['cpconv']:.2f}",f"{AP['cpconv']:.2f}",dcc,ccc,pre="€")}
    {kpi("CTR","% impresiones con clic",f"{AC['ctr']:.1f}","—","—","neu",suf="%")}
    {kpi("CPC medio","Coste medio por clic",f"{AC['cpc']:.2f}","—","—","neu",pre="€")}
  </div>
  <div class="sl">Por campaña</div>
  <table class="ch-table">
    <thead><tr><th>Campaña</th><th>Estado</th><th class="r">Gasto</th><th class="r">Clics</th><th class="r">Conv.</th><th class="r">€/Conv.</th></tr></thead>
    <tbody>{camp_rows()}</tbody>
  </table>
</div>

<div id="seo" class="panel">
  <div class="sl">Posicionamiento orgánico — últimos 30 días vs 30 días anteriores</div>
  <div class="kpi-grid">
    {kpi("Impresiones","Apariciones en Google",f"{SC['impr']:,.0f}",f"{SP['impr']:,.0f}",dim,cim)}
    {kpi("Clics orgánicos","Clics sin pagar",f"{SC['clicks']:,.0f}",f"{SP['clicks']:,.0f}",dsi,csi)}
    {kpi("CTR orgánico","% que generaron clic",f"{SC['ctr']:.2f}",f"{SP['ctr']:.2f}",dct,cct,suf="%")}
    {kpi("Posición media","Menor es mejor",f"{SC['pos']:.1f}",f"{SP['pos']:.1f}",dpo,cpo)}
  </div>
  <div class="sl">Top 20 keywords por impresiones</div>
  <table class="ch-table">
    <thead><tr><th>Palabra clave</th><th class="r">Impresiones</th><th class="r">Clics</th><th class="r">CTR</th><th class="r">Posición</th></tr></thead>
    <tbody>{kw_rows()}</tbody>
  </table>
</div>

<div class="footer">
  <img src="data:image/png;base64,{logo}" alt="Etendo">
  <div>Actualizado automáticamente · Windsor.ai · etendo.software · {generated}</div>
</div>

<script>
function showTab(id,el){{
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  el.classList.add('active');
}}
</script>
</body></html>"""

with open(OUT_FILE, "w", encoding="utf-8") as f:
    f.write(html)

print(f"✅ Generado: {OUT_FILE}")
print(f"   GA4:  {C['sessions']:,.0f} sesiones")
print(f"   Ads:  €{AC['cost']:.2f}")
print(f"   SC:   {SC['clicks']:,.0f} clics")
