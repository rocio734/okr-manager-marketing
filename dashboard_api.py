"""
Etendo Dashboard API — FastAPI backend
GET /api/metrics?period=30  →  JSON con datos de Windsor.ai + Etendo CRM
Caché en memoria por período, TTL 55 minutos.
Startup warmup en background para period=30.
"""
from fastapi import FastAPI, Query, HTTPException, Response
from fastapi.responses import RedirectResponse
import secrets
from fastapi.middleware.cors import CORSMiddleware
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import os
import json
import urllib.request
import urllib.parse
import threading
import time
from datetime import datetime, timedelta
from collections import Counter
from typing import Optional

N8N_OUTREACH_WEBHOOK = "https://n8n.labs.etendo.cloud/webhook/a3f7c821-5d04-4b9e-8c31-0e72b49d6f15"

# ── Config ─────────────────────────────────────────────────────────────────────
WINDSOR_API_KEY = os.environ.get("WINDSOR_API_KEY", "")
_CRM_BASE = os.environ.get("ETENDO_BASE_URL", "")
_CRM_USER = os.environ.get("ETENDO_USERNAME", "")
_CRM_PASS = os.environ.get("ETENDO_PASSWORD", "")
_CRM_ROLE = "8351131DFF384725AB08E06773FE6144"
WINDSOR_BASE = "https://connectors.windsor.ai"
CACHE_TTL = 55 * 60  # 55 minutos
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
_SB_HEADERS = lambda: {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}
OUTREACH_CAMPAIGN = "outreach-ago2026"
OUTREACH_SOURCE   = "intel_dashboard"
PIPELINE_ID       = "11d2089f-a64e-4001-b8af-9210787f3fce"
STAGE_MAP = {
    "Nuevo Lead":        "2f7828bf-51eb-4a5e-a645-026a7e06834b",
    "Contactado":        "5f230c21-f6e8-4a1d-8b85-4a49655a1a5d",
    "Reunión Agendada":  "943efba8-7576-458c-a174-759d48bc8bd2",
    "Propuesta Enviada": "7c113042-bbbf-4fbc-9f0c-7430ba5e8dd0",
    "Negociación":       "21df17b6-7d79-46e9-b988-5bb14642b189",
    "Cerrado Ganado":    "50217db0-be0f-4ddc-9d92-25fca24cce7e",
    "Cerrado Perdido":   "18ac2275-509c-443d-8460-10b4f322ddd0",
}

# 1x1 transparent GIF
_GIF = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff"
    b"\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00"
    b"\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
)

GA4_FIELDS = [
    "sessions", "active_users", "screen_page_views",
    "engagement_rate", "bounce_rate", "average_session_duration",
]

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Etendo Dashboard API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "PUT", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache: dict = {}
_cache_lock = threading.Lock()

def _cache_get(period: int):
    with _cache_lock:
        entry = _cache.get(period)
        if entry and (time.time() - entry["ts"]) < CACHE_TTL:
            return entry["data"]
    return None

def _cache_set(period: int, data: dict):
    with _cache_lock:
        _cache[period] = {"data": data, "ts": time.time()}

# ── Windsor.ai ─────────────────────────────────────────────────────────────────
def _windsor_raw(connector, fields, date_from, date_to):
    params = {
        "api_key": WINDSOR_API_KEY,
        "fields": ",".join(fields),
        "date_from": date_from,
        "date_to": date_to,
    }
    r = requests.get(f"{WINDSOR_BASE}/{connector}", params=params, timeout=20)
    r.raise_for_status()
    d = r.json()
    return d.get("data", d) if isinstance(d, dict) else d

def _windsor(connector, fields, start_days, end_days=0):
    t = datetime.today()
    df = (t - timedelta(days=start_days)).strftime("%Y-%m-%d")
    dt = (t - timedelta(days=end_days)).strftime("%Y-%m-%d")
    return _windsor_raw(connector, fields, df, dt)

def _windsor_safe(connector, fields, start_days, end_days=0):
    try:
        return _windsor(connector, fields, start_days, end_days)
    except Exception:
        return []

# ── Etendo CRM ─────────────────────────────────────────────────────────────────
def _crm_login():
    body = json.dumps({
        "username": _CRM_USER,
        "password": _CRM_PASS,
        "role": _CRM_ROLE,
    }).encode()
    req = urllib.request.Request(
        f"{_CRM_BASE}/api/auth/login", data=body, method="POST"
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["token"]

def _crm_fetch_all(token, entity):
    out, start, seen_ids = [], 0, set()
    while start < 20000:  # hard cap: 20k rows max
        body = urllib.parse.urlencode({
            "_operationType": "fetch",
            "_startRow": str(start),
            "_endRow": str(start + 500),
        }).encode()
        req = urllib.request.Request(
            f"{_CRM_BASE}/api/datasource/{entity}", data=body, method="POST"
        )
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=30) as r:
            page = json.loads(r.read()).get("response", {}).get("data", [])
        if not page:
            break
        new = [r for r in page if r.get("id") not in seen_ids]
        if not new:
            break  # no new records — pagination exhausted
        seen_ids.update(r.get("id") for r in new)
        out.extend(new)
        start += 500
    return out

# ── Helpers ────────────────────────────────────────────────────────────────────
def fsum(rows, f):
    return sum(float(r.get(f) or 0) for r in rows)

def favg(rows, f):
    v = [float(r.get(f) or 0) for r in rows if r.get(f) is not None]
    return sum(v) / len(v) if v else 0

def channel_sum(rows, name):
    return sum(
        float(r.get("sessions") or 0) for r in rows
        if name.lower() in str(r.get("session_default_channel_group", "")).lower()
    )

def phone_country(phone):
    if not phone:
        return "—"
    p = str(phone).replace(" ", "").replace("-", "")
    if p.startswith(("+54", "54")): return "Argentina"
    if p.startswith(("+34", "34")): return "España"
    if p.startswith(("+52", "52")): return "México"
    if p.startswith("+57"): return "Colombia"
    if p.startswith("+56"): return "Chile"
    if p.startswith("+51"): return "Perú"
    if p.startswith("+60"): return "Malasia"
    return "—"

def fmt_dur(s):
    return f"{int(s // 60)}m {int(s % 60):02d}s"

def page_type_label(path):
    if any(x in path for x in ["/user-guide/", "/developer-guide/", "/whats-new/"]):
        return "Doc técnica"
    if "/blog/" in path:
        return "Blog"
    if path in ["/contactanos/", "/muchas-gracias/", "/en/contactanos/"]:
        return "Conversión"
    if any(x in path for x in ["/copilot/", "/etendo-go/", "/etendo-next/"]):
        return "Producto"
    return "Captación"

# ── Core data builder ──────────────────────────────────────────────────────────
def build_metrics(period: int) -> dict:
    prev_start = period * 2
    prev_end = period

    # ── Fetch all sources IN PARALLEL ─────────────────────────────────────────
    CH_FIELDS    = ["sessions", "session_default_channel_group",
                    "bounce_rate", "average_session_duration"]
    ADS_FIELDS_C = ["cost", "clicks", "impressions", "conversions", "campaign_name", "campaign_status"]
    ADS_FIELDS_P = ["cost", "clicks", "conversions", "campaign_name"]
    SC_FIELDS    = ["clicks", "impressions", "ctr", "position"]
    KW_FIELDS    = ["query", "clicks", "impressions", "ctr", "position"]
    PG_FIELDS    = ["page_path", "sessions", "bounce_rate", "average_session_duration"]
    DAILY_GA4    = ["date", "sessions"]
    DAILY_ADS    = ["date", "cost", "conversions"]

    _fetches = {
        "ga4c":      ("googleanalytics4", GA4_FIELDS,   period,      0),
        "ga4p":      ("googleanalytics4", GA4_FIELDS,   prev_start,  prev_end),
        "ch_c_rows": ("googleanalytics4", CH_FIELDS,    period,      0),
        "ch_p_rows": ("googleanalytics4", CH_FIELDS,    prev_start,  prev_end),
        "adsc":      ("google_ads",       ADS_FIELDS_C, period,      0),
        "adsp":      ("google_ads",       ADS_FIELDS_P, prev_start,  prev_end),
        "scc":       ("searchconsole",    SC_FIELDS,    period,      0),
        "scp":       ("searchconsole",    SC_FIELDS,    prev_start,  prev_end),
        "kws":       ("searchconsole",    KW_FIELDS,    period,      0),
        "pages_raw": ("googleanalytics4", PG_FIELDS,    period,      0),
        "daily_web": ("googleanalytics4", DAILY_GA4,    period,      0),
        "daily_ads": ("google_ads",       DAILY_ADS,    period,      0),
    }

    def _fetch_crm():
        try:
            token = _crm_login()
            all_leads = _crm_fetch_all(token, "ETCRM_Lead")
            cutoff = (datetime.today() - timedelta(days=period)).strftime("%Y-%m-%d")
            return [l for l in all_leads if (l.get("creationDate") or "")[:10] >= cutoff], True
        except Exception as e:
            print(f"CRM error: {e}")
            return [], False

    _results = {}
    with ThreadPoolExecutor(max_workers=13) as ex:
        future_map = {
            ex.submit(_windsor_safe, conn, fields, s, e): key
            for key, (conn, fields, s, e) in _fetches.items()
        }
        crm_future = ex.submit(_fetch_crm)
        for f in as_completed(list(future_map) + [crm_future]):
            if f is crm_future:
                _results["_crm"] = f.result()
            else:
                _results[future_map[f]] = f.result()

    ga4c         = _results["ga4c"]
    ga4p         = _results["ga4p"]
    ch_c_rows    = _results["ch_c_rows"]
    ch_p_rows    = _results["ch_p_rows"]
    adsc         = _results["adsc"]
    adsp         = _results["adsp"]
    scc          = _results["scc"]
    scp          = _results["scp"]
    kws          = _results["kws"]
    pages_raw    = _results["pages_raw"]
    daily_web_rows = _results["daily_web"]
    daily_ads_rows = _results["daily_ads"]
    crm_leads, crm_ok = _results["_crm"]

    def _process_daily(rows, *fields):
        rows_s = sorted(rows, key=lambda r: r.get("date", ""))
        labels, vals = [], {f: [] for f in fields}
        for r in rows_s:
            d = r.get("date", "")
            if len(d) >= 10:
                labels.append(f"{int(d[8:10])}/{int(d[5:7])}")
            else:
                labels.append(d)
            for f in fields:
                v = r.get(f)
                vals[f].append(round(float(v), 2) if v else 0)
        return labels, vals

    web_labels, web_vals = _process_daily(daily_web_rows, "sessions")
    ads_labels, ads_vals = _process_daily(daily_ads_rows, "cost", "conversions")

    # ── GA4 aggregates ─────────────────────────────────────────────────────────
    C = {
        "sessions":   fsum(ga4c, "sessions"),
        "users":      fsum(ga4c, "active_users"),
        "pageviews":  fsum(ga4c, "screen_page_views"),
        "engagement": favg(ga4c, "engagement_rate") * 100,
        "bounce":     favg(ga4c, "bounce_rate") * 100,
        "duration":   favg(ga4c, "average_session_duration"),
    }
    P = {
        "sessions":   fsum(ga4p, "sessions"),
        "users":      fsum(ga4p, "active_users"),
        "pageviews":  fsum(ga4p, "screen_page_views"),
        "engagement": favg(ga4p, "engagement_rate") * 100,
        "bounce":     favg(ga4p, "bounce_rate") * 100,
    }

    # ── Channels ───────────────────────────────────────────────────────────────
    ch_map = {
        "direct": "direct", "organic": "organic search",
        "paid_search": "paid search", "cross": "cross-network",
        "paid_social": "paid social", "referral": "referral",
        "organic_social": "organic social", "ai": "ai",
    }
    ch_c = {k: channel_sum(ch_c_rows, v) for k, v in ch_map.items()}
    ch_p = {k: channel_sum(ch_p_rows, v) for k, v in ch_map.items()}
    channels_available = len(ch_c_rows) > 0

    def ch_wavg(rows, ch_val, field):
        rel = [r for r in rows if ch_val.lower() in str(r.get("session_default_channel_group", "")).lower()]
        total_s = sum(float(r.get("sessions") or 0) for r in rel)
        if not total_s: return None
        return sum(float(r.get(field) or 0) * float(r.get("sessions") or 0) for r in rel) / total_s

    ch_bounce_c = {k: ch_wavg(ch_c_rows, v, "bounce_rate") for k, v in ch_map.items()}
    ch_dur_c    = {k: ch_wavg(ch_c_rows, v, "average_session_duration") for k, v in ch_map.items()}

    def ch_pct(val):
        return f"{round(val * 100, 1)}%" if val is not None else "—"

    def _page_sessions(path):
        for p in pages_raw:
            if p.get("page_path") == path:
                return int(float(p.get("sessions") or 0))
        return 0

    # ── Ads aggregates ─────────────────────────────────────────────────────────
    AC = {
        "cost":        fsum(adsc, "cost"),
        "clicks":      fsum(adsc, "clicks"),
        "impressions": fsum(adsc, "impressions"),
        "conversions": fsum(adsc, "conversions"),
    }
    AC["ctr"]   = AC["clicks"] / AC["impressions"] * 100 if AC["impressions"] else 0
    AC["cpc"]   = AC["cost"] / AC["clicks"] if AC["clicks"] else 0
    AC["cpl"]   = AC["cost"] / AC["conversions"] if AC["conversions"] else 0
    AC["daily"] = AC["cost"] / period

    AP = {
        "cost":        fsum(adsp, "cost"),
        "clicks":      fsum(adsp, "clicks"),
        "conversions": fsum(adsp, "conversions"),
    }
    AP["cpl"]   = AP["cost"] / AP["conversions"] if AP["conversions"] else 0
    AP["daily"] = AP["cost"] / period

    camps = {}
    for r in adsc:
        n = r.get("campaign_name", "—")
        if n not in camps:
            camps[n] = {"cost": 0.0, "clicks": 0.0, "conversions": 0.0, "status": r.get("campaign_status", "—")}
        camps[n]["cost"]        += float(r.get("cost") or 0)
        camps[n]["clicks"]      += float(r.get("clicks") or 0)
        camps[n]["conversions"] += float(r.get("conversions") or 0)

    campaigns_list = []
    for name, d in camps.items():
        cpl = d["cost"] / d["conversions"] if d["conversions"] else None
        campaigns_list.append({
            "name":        name,
            "active":      "ENABLED" in d["status"],
            "cost":        round(d["cost"], 2),
            "clicks":      int(d["clicks"]),
            "conversions": int(d["conversions"]),
            "cpl":         round(cpl, 2) if cpl is not None else None,
        })

    # ── Search Console aggregates ──────────────────────────────────────────────
    SC = {
        "clicks":      fsum(scc, "clicks"),
        "impressions": fsum(scc, "impressions"),
        "ctr":         favg(scc, "ctr") * 100,
        "position":    favg(scc, "position"),
    }
    SP = {
        "clicks":      fsum(scp, "clicks"),
        "impressions": fsum(scp, "impressions"),
        "ctr":         favg(scp, "ctr") * 100,
        "position":    favg(scp, "position"),
    }

    keywords_list = [
        {
            "query":       kw.get("query", "—"),
            "impressions": int(float(kw.get("impressions") or 0)),
            "clicks":      int(float(kw.get("clicks") or 0)),
            "ctr":         round(float(kw.get("ctr") or 0) * 100, 1),
            "position":    round(float(kw.get("position") or 0), 1),
        }
        for kw in sorted(kws, key=lambda x: float(x.get("impressions") or 0), reverse=True)[:20]
    ]

    # ── Pages ──────────────────────────────────────────────────────────────────
    pages_sorted = sorted(pages_raw, key=lambda x: float(x.get("sessions") or 0), reverse=True)
    pages_list = [
        {
            "path":       p.get("page_path", "—"),
            "sessions":   int(float(p.get("sessions") or 0)),
            "bounce_pct": round(float(p.get("bounce_rate") or 0) * 100, 1),
            "duration_s": float(p.get("average_session_duration") or 0),
            "type":       page_type_label(p.get("page_path", "")),
        }
        for p in pages_sorted[:12]
    ]
    pages_quality_list = [
        {
            "path":       p.get("page_path", "—"),
            "sessions":   int(float(p.get("sessions") or 0)),
            "bounce_pct": round(float(p.get("bounce_rate") or 0) * 100, 1),
            "duration_s": float(p.get("average_session_duration") or 0),
            "type":       page_type_label(p.get("page_path", "")),
        }
        for p in sorted(pages_raw, key=lambda x: float(x.get("average_session_duration") or 0), reverse=True)[:10]
    ]

    # ── CRM processing ─────────────────────────────────────────────────────────
    _INACTIVE = {"Dead", "Converted"}
    def lstatus(l): return l.get("leadStatus$_identifier") or ""
    def lclass(l):  return l.get("classification$_identifier") or ""
    def lname(l):
        fn = (l.get("firstname") or "").strip()
        ln = (l.get("lastname") or "").strip()
        return (fn + (" " + ln if ln else "")) or "—"

    crm_active = [l for l in crm_leads if lstatus(l) not in _INACTIVE]
    crm_dead   = [l for l in crm_leads if lstatus(l) == "Dead"]
    crm_iql    = [l for l in crm_active if lclass(l) == "IQL"]
    crm_mql    = [l for l in crm_active if lclass(l) == "MQL"]
    crm_sql    = [l for l in crm_active if lclass(l) == "SQL" or lstatus(l) == "Qualified"]

    def _descarte_reason(l):
        text = ((l.get("description") or "") + " " + (l.get("leadNote") or "")).lower()
        if any(w in text for w in ["trabajo", "empleo", "vacante", "curriculum", "curriculo",
                                    "busca empleo", "busco trabajo", "busca trabajo", "oferta de trabajo"]):
            return "job"
        if any(w in text for w in ["integr", "chatgpt", "claude", "plugin", "su erp", "erp actual",
                                    "conectar ia", "parchear", "añadir ia", "su sistema actual",
                                    "no quiere cambiar", "no migrar"]):
            return "misunderstanding"
        if any(w in text for w in ["sin respuesta", "no responde", "no contesta", "no ha respondido",
                                    "no contactable", "imposible contactar", "sin contacto"]):
            return "no_response"
        if any(w in text for w in ["precio", "caro", "presupuesto", "no encaja", "no le interesa",
                                    "sin presupuesto"]):
            return "product_fit"
        return "other"

    _dc = Counter(_descarte_reason(l) for l in crm_dead)
    descarte_breakdown = {
        "job":            _dc.get("job", 0),
        "misunderstanding": _dc.get("misunderstanding", 0),
        "no_response":    _dc.get("no_response", 0),
        "product_fit":    _dc.get("product_fit", 0) + _dc.get("other", 0),
    }

    _CLASS_ORDER = {"SQL": 0, "MQL": 1, "IQL": 2}
    pipeline_sorted = sorted(
        crm_active, key=lambda l: (_CLASS_ORDER.get(lclass(l), 9), lname(l))
    )
    pipeline_list = []
    for l in pipeline_sorted[:15]:
        desc = (l.get("description") or "").replace("\n", " ").strip()
        desc = desc[:80] + "…" if len(desc) > 80 else desc
        pipeline_list.append({
            "name":        lname(l),
            "company":     (l.get("company") or "—").strip() or "—",
            "country":     phone_country(l.get("phone") or ""),
            "cls":         lclass(l),
            "status":      lstatus(l),
            "description": desc,
        })

    cpl_val = (
        round(AC["cost"] / len(crm_active), 0) if crm_ok and crm_active else None
    )

    # ── Dates ──────────────────────────────────────────────────────────────────
    today    = datetime.today()
    date_to  = today.strftime("%d/%m/%Y")
    date_fr  = (today - timedelta(days=period)).strftime("%d/%m/%Y")
    date_top = (today - timedelta(days=period)).strftime("%d/%m/%Y")
    date_frp = (today - timedelta(days=period * 2)).strftime("%d/%m/%Y")

    period_labels = {7: "últimos 7 días", 30: "últimos 30 días", 90: "últimos 90 días"}

    return {
        "period":          period,
        "period_label":    period_labels.get(period, f"últimos {period} días"),
        "date_from":       date_fr,
        "date_to":         date_to,
        "date_from_prev":  date_frp,
        "date_to_prev":    date_top,
        "generated_at":    today.strftime("%d/%m/%Y %H:%M UTC"),
        "ga4": {
            "sessions_curr":   int(C["sessions"]),
            "sessions_prev":   int(P["sessions"]),
            "users_curr":      int(C["users"]),
            "users_prev":      int(P["users"]),
            "pageviews_curr":  int(C["pageviews"]),
            "pageviews_prev":  int(P["pageviews"]),
            "engagement_curr": round(C["engagement"], 1),
            "engagement_prev": round(P["engagement"], 1),
            "bounce_curr":     round(C["bounce"], 1),
            "bounce_prev":     round(P["bounce"], 1),
            "duration_curr":   fmt_dur(C["duration"]),
            "ai_curr":         int(ch_c.get("ai", 0)) if channels_available else None,
            "ai_prev":         int(ch_p.get("ai", 0)) if channels_available else None,
        },
        "channels": {
            "available": channels_available,
            **{f"{k}_curr": int(ch_c[k]) for k in ch_map},
            **{f"{k}_prev": int(ch_p[k]) for k in ch_map},
            **{f"{k}_bounce_curr": ch_pct(ch_bounce_c.get(k)) for k in ch_map},
            **{f"{k}_dur_curr": (fmt_dur(ch_dur_c[k]) if ch_dur_c.get(k) else "—") for k in ch_map},
        },
        "ads": {
            "cost_curr":        round(AC["cost"], 2),
            "cost_prev":        round(AP["cost"], 2),
            "clicks_curr":      int(AC["clicks"]),
            "clicks_prev":      int(AP["clicks"]),
            "impressions_curr": int(AC["impressions"]),
            "ctr_curr":         round(AC["ctr"], 2),
            "cpc_curr":         round(AC["cpc"], 2),
            "conversions_curr": int(AC["conversions"]),
            "conversions_prev": int(AP["conversions"]),
            "cpl_curr":         round(AC["cpl"], 2),
            "cpl_prev":         round(AP["cpl"], 2),
            "daily_curr":       round(AC["daily"], 2),
            "daily_prev":       round(AP["daily"], 2),
            "campaigns":        campaigns_list,
        },
        "search_console": {
            "impressions_curr": int(SC["impressions"]),
            "impressions_prev": int(SP["impressions"]),
            "clicks_curr":      int(SC["clicks"]),
            "clicks_prev":      int(SP["clicks"]),
            "ctr_curr":         round(SC["ctr"], 2),
            "ctr_prev":         round(SP["ctr"], 2),
            "position_curr":    round(SC["position"], 1),
            "position_prev":    round(SP["position"], 1),
            "keywords":         keywords_list,
        },
        "pages":         pages_list,
        "pages_quality": pages_quality_list,
        "crm": {
            "available": crm_ok,
            "total":     len(crm_leads),
            "active":    len(crm_active),
            "dead":      len(crm_dead),
            "iql":       len(crm_iql),
            "mql":       len(crm_mql),
            "sql":       len(crm_sql),
            "cpl":       int(cpl_val) if cpl_val is not None else None,
            "subtitle":  (
                f"{len(crm_leads)} leads · {len(crm_active)} activos · "
                f"{len(crm_dead)} descartados · {today.strftime('%d/%m/%Y')}"
                if crm_ok else "CRM no disponible"
            ),
            "pipeline":  pipeline_list,
            "descarte":  descarte_breakdown,
        },
        "daily": {
            "web_labels":  web_labels,
            "sessions":    web_vals.get("sessions", []),
            "ads_labels":  ads_labels,
            "cost":        ads_vals.get("cost", []),
            "conversions": [int(v) for v in ads_vals.get("conversions", [])],
        },
        "leads_funnel": {
            "sessions":   int(C["sessions"]),
            "contactanos": _page_sessions("/contactanos/"),
            "gracias":    _page_sessions("/muchas-gracias/"),
            "ads_conv":   int(AC["conversions"]),
        },
    }

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    cached = list(_cache.keys())
    return {"ok": True, "cached_periods": cached}

@app.get("/api/metrics")
def metrics(period: int = Query(default=30, ge=7, le=180)):
    cached = _cache_get(period)
    if cached:
        return {**cached, "_from_cache": True}
    try:
        data = build_metrics(period)
        _cache_set(period, data)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Outreach Pixel Tracking ────────────────────────────────────────────────────
@app.get("/pixel/{email}.gif")
def pixel(email: str):
    """Redirige a URL única por apertura — evita que los clientes de email cacheen el pixel.
    Gmail y Outlook cachean imágenes en sesión; el redirect con nonce aleatorio fuerza
    una nueva petición en cada apertura, permitiendo contar re-aperturas."""
    nonce = secrets.token_hex(10)
    return RedirectResponse(
        url=f"/pixel/{email}/open?_={nonce}",
        status_code=302,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                 "Pragma": "no-cache", "Vary": "*"},
    )


@app.get("/pixel/{email}/open")
def pixel_open(email: str):
    """Registra la apertura en Supabase y sirve el GIF. Llamado tras el redirect."""
    if not SUPABASE_URL:
        return Response(content=_GIF, media_type="image/gif")
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/email_opens",
            headers=_SB_HEADERS(),
            json={"contact_id": email, "campaign": OUTREACH_CAMPAIGN},
            timeout=5,
        )
        # Avanzar deal a Contactado si sigue en Nuevo Lead
        c_r = requests.get(
            f"{SUPABASE_URL}/rest/v1/contacts?email=eq.{urllib.parse.quote(email)}&select=id",
            headers=_SB_HEADERS(), timeout=5)
        contacts_found = c_r.json() if c_r.status_code == 200 else []
        if contacts_found:
            cid = contacts_found[0]["id"]
            deals_r = requests.get(
                f"{SUPABASE_URL}/rest/v1/deals?contact_id=eq.{cid}&stage_id=eq.{STAGE_MAP['Nuevo Lead']}",
                headers=_SB_HEADERS(), timeout=5)
            deals = deals_r.json() if deals_r.status_code == 200 else []
            for d in deals:
                requests.patch(
                    f"{SUPABASE_URL}/rest/v1/deals?id=eq.{d['id']}",
                    headers={**_SB_HEADERS(), "Prefer": "return=minimal"},
                    json={"stage_id": STAGE_MAP["Contactado"]},
                    timeout=5,
                )
    except Exception as e:
        print(f"⚠️  pixel tracking error for {email}: {e}")
    return Response(content=_GIF, media_type="image/gif",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                             "Pragma": "no-cache", "Expires": "Thu, 01 Jan 1970 00:00:00 GMT",
                             "Vary": "*"})


# ── Outreach Leads API ──────────────────────────────────────────────────────────
@app.get("/api/outreach")
def outreach_list():
    """Devuelve contacts + deals + open counts para el tab de Outreach."""
    if not SUPABASE_URL:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    try:
        c_r = requests.get(
            f"{SUPABASE_URL}/rest/v1/contacts?fuente=eq.{OUTREACH_SOURCE}"
            f"&select=id,nombre,email,empresa,notas_internas,custom_fields",
            headers=_SB_HEADERS(), timeout=10)
        contacts = c_r.json() if c_r.status_code == 200 else []
        if not contacts:
            return {"leads": []}

        contact_ids = [c["id"] for c in contacts]
        id_filter = ",".join(contact_ids)

        d_r = requests.get(
            f"{SUPABASE_URL}/rest/v1/deals?contact_id=in.({id_filter})"
            f"&select=id,contact_id,stage_id,prioridad,updated_at",
            headers=_SB_HEADERS(), timeout=10)
        deals = d_r.json() if d_r.status_code == 200 else []
        deal_by_contact = {d["contact_id"]: d for d in deals}

        emails = [c["email"] for c in contacts if c.get("email")]
        open_counts: dict = {}
        open_last: dict = {}
        if emails:
            email_filter = ",".join(emails)
            o_r = requests.get(
                f"{SUPABASE_URL}/rest/v1/email_opens"
                f"?campaign=eq.{OUTREACH_CAMPAIGN}&contact_id=in.({email_filter})"
                f"&select=contact_id,opened_at&order=opened_at.desc",
                headers=_SB_HEADERS(), timeout=10)
            opens = o_r.json() if o_r.status_code == 200 else []
            for o in opens:
                eid = o["contact_id"]
                open_counts[eid] = open_counts.get(eid, 0) + 1
                if eid not in open_last:
                    open_last[eid] = o["opened_at"]

        stage_name = {v: k for k, v in STAGE_MAP.items()}

        result = []
        for c in contacts:
            deal = deal_by_contact.get(c["id"], {})
            email = c.get("email", "")
            cf = c.get("custom_fields") or {}
            signal_label = cf.get("signal_label", "")
            PARTNER_SIGNALS = {
                "Partner Odoo", "Partner SAP", "Partner Sage", "Partner Holded",
                "Partner SAP (deep)", "Busca partner",
            }
            lead_type = "partner" if signal_label in PARTNER_SIGNALS else "lead"

            result.append({
                "contact_id":   c["id"],
                "deal_id":      deal.get("id", ""),
                "empresa":      c.get("empresa", ""),
                "email":        email,
                "sector":       cf.get("sector", ""),
                "score":        cf.get("score", 0),
                "signal_label": signal_label,
                "lead_type":    lead_type,
                "stage":        stage_name.get(deal.get("stage_id", ""), "Nuevo Lead"),
                "stage_id":     deal.get("stage_id", STAGE_MAP["Nuevo Lead"]),
                "opens":        open_counts.get(email, 0),
                "last_open":    open_last.get(email, ""),
                "notas":        c.get("notas_internas", ""),
                "sent_at":      cf.get("sent_at", ""),
                "attempts":     cf.get("attempts", 0),
                "subject":      cf.get("outreach_subject", ""),
                "body_html":    cf.get("outreach_body", ""),
                "has_message":  bool(cf.get("outreach_body", "")),
                "replied_at":   cf.get("replied_at", ""),
                "current_erp":  cf.get("current_erp", "—"),
                "pixel_url":    f"https://etendo-dashboard-api.onrender.com/pixel/{urllib.parse.quote(email)}.gif",
            })

        result.sort(key=lambda x: (-x["opens"], -x["score"]))
        return {"leads": result, "total": len(result)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/outreach/{deal_id}/email-content")
def outreach_email_content(deal_id: str, payload: dict):
    """Guarda subject y body_html editados en Supabase."""
    if not SUPABASE_URL:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    dr = requests.get(f"{SUPABASE_URL}/rest/v1/deals?id=eq.{deal_id}&select=contact_id",
                      headers=_SB_HEADERS(), timeout=10)
    rows = dr.json() if dr.status_code == 200 else []
    if not rows:
        raise HTTPException(status_code=404, detail="Deal no encontrado")
    contact_id = rows[0]["contact_id"]
    cr = requests.get(f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}&select=custom_fields",
                      headers=_SB_HEADERS(), timeout=10)
    cf = ((cr.json() or [{}])[0]).get("custom_fields") or {}
    if payload.get("subject"):
        cf["outreach_subject"] = payload["subject"]
    if payload.get("body_html") is not None:
        cf["outreach_body"] = payload["body_html"]
    requests.patch(f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}",
                   headers={**_SB_HEADERS(), "Prefer": "return=minimal"},
                   json={"custom_fields": cf}, timeout=10)
    return {"ok": True}


@app.post("/api/outreach/{deal_id}/mark-replied")
def outreach_mark_replied(deal_id: str):
    """Marca el lead como que respondió (guarda replied_at en custom_fields)."""
    if not SUPABASE_URL:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    dr = requests.get(f"{SUPABASE_URL}/rest/v1/deals?id=eq.{deal_id}&select=contact_id",
                      headers=_SB_HEADERS(), timeout=10)
    rows = dr.json() if dr.status_code == 200 else []
    if not rows:
        raise HTTPException(status_code=404, detail="Deal no encontrado")
    contact_id = rows[0]["contact_id"]
    cr = requests.get(f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}&select=custom_fields",
                      headers=_SB_HEADERS(), timeout=10)
    cf = ((cr.json() or [{}])[0]).get("custom_fields") or {}
    cf["replied_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}",
        headers={**_SB_HEADERS(), "Prefer": "return=minimal"},
        json={"custom_fields": cf}, timeout=10)
    return {"ok": True, "replied_at": cf["replied_at"]}


@app.put("/api/outreach/{deal_id}")
def outreach_update(deal_id: str, stage: Optional[str] = None,
                    notas: Optional[str] = None, sent: Optional[bool] = None):
    """Actualiza stage, notas y/o registra un intento de envío."""
    if not SUPABASE_URL:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    # Fetch deal to get contact_id
    dr = requests.get(f"{SUPABASE_URL}/rest/v1/deals?id=eq.{deal_id}&select=contact_id",
                      headers=_SB_HEADERS(), timeout=10)
    deal_rows = dr.json() if dr.status_code == 200 else []
    if not deal_rows:
        raise HTTPException(status_code=404, detail="Deal no encontrado")
    contact_id = deal_rows[0]["contact_id"]

    # Update deal stage if requested
    if stage and stage in STAGE_MAP:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/deals?id=eq.{deal_id}",
            headers={**_SB_HEADERS(), "Prefer": "return=minimal"},
            json={"stage_id": STAGE_MAP[stage]}, timeout=10)

    # Update contact notes and/or sent tracking
    contact_payload: dict = {}
    if notas is not None:
        contact_payload["notas_internas"] = notas

    if sent:
        # Fetch current custom_fields to increment attempts
        cr = requests.get(
            f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}&select=custom_fields",
            headers=_SB_HEADERS(), timeout=10)
        cf = (cr.json() or [{}])[0].get("custom_fields") or {}
        now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        cf["attempts"] = cf.get("attempts", 0) + 1
        if not cf.get("sent_at"):
            cf["sent_at"] = now_iso
        cf["last_sent_at"] = now_iso
        contact_payload["custom_fields"] = cf

    if contact_payload:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}",
            headers={**_SB_HEADERS(), "Prefer": "return=minimal"},
            json=contact_payload, timeout=10)

    return {"ok": True}


# ── Generate Outreach Email ────────────────────────────────────────────────────
@app.post("/api/generate-outreach")
def generate_outreach(deal_id: str):
    """Genera cuerpo y asunto de outreach personalizado con IA para un lead."""
    if not SUPABASE_URL:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY no configurada")

    dr = requests.get(f"{SUPABASE_URL}/rest/v1/deals?id=eq.{deal_id}&select=contact_id",
                      headers=_SB_HEADERS(), timeout=10)
    deal_rows = dr.json() if dr.status_code == 200 else []
    if not deal_rows:
        raise HTTPException(status_code=404, detail="Deal no encontrado")
    contact_id = deal_rows[0]["contact_id"]

    cr = requests.get(
        f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}"
        f"&select=nombre,email,empresa,cargo,custom_fields",
        headers=_SB_HEADERS(), timeout=10)
    c = (cr.json() or [{}])[0]
    empresa = c.get("empresa", "")
    cargo   = c.get("cargo", "")
    email   = (c.get("email") or "").lower()
    cf           = c.get("custom_fields") or {}
    sector       = cf.get("sector", "")
    score        = cf.get("score", "")
    signal_label = cf.get("signal_label", "")
    current_erp  = cf.get("current_erp", "")

    GENERIC_PREFIXES = {
        "info", "contacto", "contact", "hola", "hello", "admin", "administracion",
        "administración", "logistica", "logística", "comercial", "ventas", "sales",
        "marketing", "direccion", "dirección", "recepcion", "recepción", "general",
        "empresa", "mail", "correo", "soporte", "support", "web",
    }
    local_part = email.split("@")[0] if "@" in email else email
    is_generic = local_part in GENERIC_PREFIXES

    PARTNER_SIGNALS = {
        "Partner Odoo", "Partner SAP", "Partner Sage", "Partner Holded",
        "Partner SAP (deep)", "Busca partner",
    }
    lead_type = "partner" if signal_label in PARTNER_SIGNALS else "lead"

    if is_generic:
        prompt = f"""Completá esta plantilla de email con UNA sola línea de gancho adaptada al sector. Nada más.

PLANTILLA (no cambies nada excepto la línea marcada):
---
Hola,

Soy Victoria de Etendo. [GANCHO: una oración que diga qué hacemos por empresas de {sector} — el diferenciador es que operan el ERP hablándole a Claude en lenguaje natural, sin tocar pantallas. Máx 20 palabras, tono directo, sin adjetivos vacíos.]

¿A quién le puedo contar esto en {empresa}?

Victoria
---

Ejemplos de GANCHO bien hecho (tomá el tono, no copies):
- Construcción: "Estamos ayudando a empresas de construcción a operar su ERP desde Claude — le hablás en lenguaje natural y el sistema ejecuta solo, sin pantallas."
- Logística: "Estamos ayudando a operadores logísticos a gestionar pedidos y stock desde Claude — sin abrir el ERP, solo hablando."
- Alimentación: "Ayudamos a empresas de alimentación a automatizar su ERP con lenguaje natural — el equipo le habla a Claude y el sistema actúa solo."

Asunto: corto, menciona el sector. Sin "Consulta rápida". Sin signos de exclamación.

Respondé SOLO con JSON válido:
{{"subject": "...", "body_html": "..."}}

El body_html debe usar <p> con estilos inline. Respetá los saltos de párrafo de la plantilla."""

    elif lead_type == "partner":
        prompt = f"""Eres Victoria, encargada del área comercial de Etendo (ERP agéntico y composable para empresas medianas).
Si te presentás en el email, usá exactamente: "Soy Victoria, encargada del área comercial de Etendo" — nunca uses otro nombre ni cargo.
Generá un email de outreach en español (España) para una consultora/implementadora que actualmente trabaja con {signal_label.replace('Partner ', '') if signal_label.startswith('Partner') else 'otro ERP'}.

- Empresa: {empresa}
- Señal detectada: {signal_label}
- Cargo del contacto: {cargo}

Contexto CRÍTICO sobre Etendo (léelo con atención antes de escribir):
- Etendo es el PRIMER ERP agéntico del mercado. Eso no es marketing — es una diferencia técnica real.
- Cualquier IA externa (Claude, GPT, agentes propios del cliente) puede operar el ERP directamente vía MCP, en lenguaje natural, sin que el usuario toque la interfaz. El usuario le dice a Claude "creá una orden de compra para el proveedor X" y el ERP lo ejecuta solo.
- Etendo tiene además Copilot como capa de IA interna para tareas del ERP.
- NO digas "integra IA" ni "tiene IA incorporada" — eso lo tienen todos. El diferenciador es MCP: la IA opera el ERP como si fuera una persona, solo con lenguaje natural.
- La red de partners obtiene margen en licencias, soporte y servicios de implementación.

Reglas ESTRICTAS:
1. El email es de partner-to-partner, no de vendedor a comprador. Tono: profesional entre colegas
2. NO digas que Etendo es mejor que su ERP actual — plantea una oportunidad de ampliar su portfolio
3. El eje central: "Tus clientes te van a pedir que su ERP funcione con IA. Con Etendo ya es posible hoy: conectan Claude o GPT y operan el ERP solo con lenguaje natural."
4. Una pregunta concreta al final que invite a una llamada de 20 minutos
5. Máximo 4 párrafos cortos. Sin frases de relleno
6. NO incluyas links de video ni CTAs — se agrega automáticamente
7. NO incluyas firma

Respondé SOLO con JSON válido:
{{"subject": "...", "body_html": "..."}}

El body_html debe usar párrafos <p> con estilos inline básicos."""
    else:
        erp_line = f"\n- ERP actual detectado: {current_erp} (si es relevante, mencioná que la migración a Etendo es más sencilla de lo que parece)" if current_erp and current_erp not in ("—","") else ""
        prompt = f"""Eres Victoria, encargada del área comercial de Etendo (ERP agéntico para empresas).
Si te presentás en el email, usá exactamente: "Soy Victoria, encargada del área comercial de Etendo" — nunca uses otro nombre ni cargo.
Generá un email de outreach en español (España) para este prospecto:
- Empresa: {empresa}
- Sector: {sector}
- Cargo del contacto: {cargo}
- Score de interés: {score}/100{erp_line}

Contexto CRÍTICO sobre Etendo (léelo con atención antes de escribir):
- Etendo es el PRIMER ERP agéntico del mercado. Diferencia técnica real, no marketing.
- Cualquier IA externa (Claude, GPT, agentes propios) puede operar el ERP vía MCP usando solo lenguaje natural: el usuario le indica a Claude "procesá los pedidos pendientes" y el ERP lo ejecuta, sin tocar la interfaz.
- Ejemplo concreto que podés usar: "Conectás Claude a Etendo vía MCP y desde una conversación podés crear órdenes de compra, consultar stock, cerrar facturas — todo en lenguaje natural."
- NO digas "integra IA" ni "tiene IA" — eso lo tienen todos. El diferenciador es que la IA opera el ERP como una persona, con lenguaje natural, vía MCP.

Reglas ESTRICTAS:
1. El email debe crear OBLIGACIÓN de responder — usá una pregunta directa al final que sea difícil de ignorar
2. Tono: cercano, directo, sin corporativismo. Nada de "espero que este email te encuentre bien"
3. Mencioná un dolor específico del sector {sector} que Etendo resuelve, y conectalo con el beneficio agéntico
4. Máximo 4 párrafos cortos
5. NO incluyas links de video ni CTAs — se agrega automáticamente
6. NO incluyas la firma (la ponemos aparte)
7. Generá también un asunto potente, corto y con curiosidad gap

Respondé SOLO con JSON válido, sin texto extra:
{{"subject": "...", "body_html": "..."}}

El body_html debe usar párrafos <p> con estilos inline básicos. El enlace del video usá href="#VIDEO_URL" como placeholder."""

    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"OpenAI error: {resp.text[:200]}")

    raw = resp.json()["choices"][0]["message"]["content"].strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        generated = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=502, detail=f"JSON inválido del modelo: {raw[:200]}")

    subject   = generated.get("subject", "")
    body_html = generated.get("body_html", "")

    # Replace video placeholder with real thumbnail block
    video_url = "https://drive.google.com/file/d/1EnB--MreDyPmOmoFtgd1CTBphPW4vKei/view"
    video_block = (
        f'<a href="{video_url}" target="_blank" style="display:block;margin:20px 0;text-decoration:none">'
        '<div style="background:linear-gradient(135deg,#1a2b6d,#f5c400);border-radius:10px;padding:20px 24px;'
        'color:#fff;font-family:Arial,sans-serif;display:flex;align-items:center;gap:16px;max-width:480px">'
        '<div style="width:48px;height:48px;background:rgba(255,255,255,0.25);border-radius:50%;'
        'display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0">▶</div>'
        '<div><div style="font-weight:700;font-size:15px">Ver cómo funciona Etendo en 90 segundos</div>'
        '<div style="font-size:12px;opacity:.85;margin-top:3px">Demo real · Sin instalación · Sin registro</div></div>'
        '</div></a>'
    )
    # Eliminar cualquier link de video inline que haya generado el modelo y siempre poner el banner visual
    import re as _re
    body_html = _re.sub(r'<a[^>]*drive\.google[^>]*>.*?</a>', '', body_html, flags=_re.DOTALL)
    body_html = body_html.replace('href="#VIDEO_URL"', f'href="{video_url}"')
    body_html = body_html + video_block

    # Save to Supabase
    cf["outreach_subject"] = subject
    cf["outreach_body"]    = body_html
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}",
        headers={**_SB_HEADERS(), "Prefer": "return=minimal"},
        json={"custom_fields": cf}, timeout=10)

    return {"ok": True, "subject": subject, "body_html": body_html}


# ── Send Outreach Email ────────────────────────────────────────────────────────
@app.post("/api/send-outreach")
def send_outreach(deal_id: str, from_email: str, sender_name: Optional[str] = "Victoria"):
    """Envía el email de outreach via n8n (Gmail de Vico)."""
    if not SUPABASE_URL:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    # Fetch deal → contact
    dr = requests.get(f"{SUPABASE_URL}/rest/v1/deals?id=eq.{deal_id}&select=contact_id",
                      headers=_SB_HEADERS(), timeout=10)
    deal_rows = dr.json() if dr.status_code == 200 else []
    if not deal_rows:
        raise HTTPException(status_code=404, detail="Deal no encontrado")
    contact_id = deal_rows[0]["contact_id"]

    cr = requests.get(
        f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}"
        f"&select=email,custom_fields",
        headers=_SB_HEADERS(), timeout=10)
    contact = (cr.json() or [{}])[0]
    to_email = contact.get("email", "")
    cf       = contact.get("custom_fields") or {}
    subject  = cf.get("outreach_subject", "Etendo ERP — Demo gratuita")
    body_txt = cf.get("outreach_body", "")

    if not to_email:
        raise HTTPException(status_code=400, detail="Lead sin email")
    if not body_txt:
        raise HTTPException(status_code=400, detail="Email sin cuerpo — contactá a Rocío")

    # Build HTML with signature and pixel
    pixel_url = f"https://etendo-dashboard-api.onrender.com/pixel/{urllib.parse.quote(to_email)}.gif"
    signature = """
<div style="margin-top:24px;border-top:1px solid #e6e6e6;padding-top:14px;font-family:Arial,sans-serif;font-size:13px;line-height:1.4;color:#333">
  <div style="font-size:16px;color:#1a2b6d;font-weight:700">Victoria Miguez</div>
  <div style="font-size:12px;color:#7a7a7a;text-transform:uppercase;letter-spacing:.4px">
    Encargada del Área Comercial
  </div>
  <div style="margin-top:10px">
    📞 <a href="tel:+5493584199352" style="text-decoration:none;color:#1a2b6d">+54 9 358 4199352</a><br>
    🌐 <a href="https://www.etendo.software" style="text-decoration:none;color:#1a2b6d">www.etendo.software</a><br>
    ✉️ <a href="mailto:victoria.miguez@etendo.software" style="text-decoration:none;color:#1a2b6d">victoria.miguez@etendo.software</a>
  </div>
  <div style="margin-top:10px">
    <a href="https://calendar.app.google/HyVddorYyUecHuJz9" style="color:#1a2b6d;text-decoration:underline">Reservá 30 min</a>
  </div>
  <div style="margin-top:12px">
    <a href="https://www.etendo.software" target="_blank">
      <img src="https://drive.google.com/uc?export=view&id=1CPhb8NKIUTbklRvX0fsP4XAVoWe9xGRP"
           alt="Etendo" style="width:260px;border-radius:8px;border:none">
    </a>
  </div>
</div>"""
    body_html = (
        "<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.7;color:#222'>"
        + body_txt.replace("\n\n", "</p><p>").replace("\n", "<br>")
        + "</div>"
        + signature
        + f'<img src="{pixel_url}" width="1" height="1" style="display:none">'
    )

    # Send via n8n webhook (Gmail de Vico)
    try:
        resp = requests.post(
            N8N_OUTREACH_WEBHOOK,
            json={"to": to_email, "subject": subject, "html": body_html},
            timeout=20,
        )
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=502,
                detail=f"n8n error {resp.status_code}: {resp.text[:200]}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al enviar: {e}")

    # Mark as sent in Supabase
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    cf["attempts"]     = cf.get("attempts", 0) + 1
    cf["last_sent_at"] = now_iso
    if not cf.get("sent_at"):
        cf["sent_at"] = now_iso
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}",
        headers={**_SB_HEADERS(), "Prefer": "return=minimal"},
        json={"custom_fields": cf}, timeout=10)

    return {"ok": True, "to": to_email, "subject": subject, "attempts": cf["attempts"]}


# ── Follow-up automático ───────────────────────────────────────────────────────
@app.get("/api/followup-pending")
def followup_pending():
    """
    Lista contactos que necesitan follow-up:
    - email enviado hace 3+ días (sent_at o last_sent_at)
    - sin respuesta (replied_at nulo)
    - menos de 3 intentos (no saturar)
    """
    if not SUPABASE_URL:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    from datetime import timezone
    cutoff = (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    cr = requests.get(
        f"{SUPABASE_URL}/rest/v1/contacts"
        f"?fuente=eq.{OUTREACH_SOURCE}&select=id,email,empresa,custom_fields",
        headers=_SB_HEADERS(), timeout=15)
    contacts = cr.json() if cr.status_code == 200 else []

    pending = []
    for c in contacts:
        cf = c.get("custom_fields") or {}
        last_sent = cf.get("last_sent_at") or cf.get("sent_at","")
        replied   = cf.get("replied_at","")
        attempts  = cf.get("attempts", 0)
        if not last_sent or replied or attempts >= 3:
            continue
        if last_sent < cutoff:
            pending.append({
                "contact_id": c["id"],
                "email":      c.get("email",""),
                "empresa":    c.get("empresa",""),
                "attempts":   attempts,
                "last_sent":  last_sent,
                "days_ago":   round((datetime.utcnow() - datetime.strptime(
                    last_sent[:19], "%Y-%m-%dT%H:%M:%S")).days),
            })

    pending.sort(key=lambda x: x["last_sent"])
    return {"count": len(pending), "pending": pending}


@app.post("/api/send-followup/{contact_id}")
def send_followup(contact_id: str):
    """
    Envía un follow-up corto al contacto. Solo si tiene sent_at y sin replied_at.
    Genera un mensaje breve con IA ("¿llegó mi mensaje anterior?") y lo envía vía n8n.
    """
    if not SUPABASE_URL:
        raise HTTPException(status_code=503, detail="Supabase not configured")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY no configurada")

    cr = requests.get(
        f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}"
        f"&select=email,empresa,cargo,custom_fields",
        headers=_SB_HEADERS(), timeout=10)
    c = (cr.json() or [{}])[0]
    if not c:
        raise HTTPException(status_code=404, detail="Contacto no encontrado")

    email   = c.get("email","")
    empresa = c.get("empresa","")
    cf      = c.get("custom_fields") or {}

    if not email:
        raise HTTPException(status_code=400, detail="Sin email")
    if cf.get("replied_at"):
        raise HTTPException(status_code=400, detail="Ya respondió — no enviar follow-up")
    if cf.get("attempts",0) >= 3:
        raise HTTPException(status_code=400, detail="Máximo de intentos alcanzado (3)")

    sector       = cf.get("sector","")
    signal_label = cf.get("signal_label","")
    current_erp  = cf.get("current_erp","")
    orig_subject = cf.get("outreach_subject","")

    erp_ctx = f" (detectamos que usan {current_erp})" if current_erp and current_erp != "—" else ""

    prompt = f"""Eres Victoria, encargada del área comercial de Etendo.
Ya enviaste un primer email a {empresa} ({sector}{erp_ctx}) hace varios días y no hubo respuesta.
Escribí un follow-up MUY corto (2-3 líneas máximo) en español (España).

Reglas:
1. Referencia al email anterior sin copiarlo ("te escribí hace unos días sobre...")
2. Una sola pregunta cerrada al final que sea fácil de responder con sí/no
3. Tono humano, nada corporativo
4. Asunto: diferente al original ("{orig_subject}"), más directo
5. NO incluyas firma
6. Si tiene señal "{signal_label}", mencioná ese contexto brevemente

Respondé SOLO con JSON: {{"subject": "...", "body_html": "..."}}"""

    try:
        ai = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 400, "temperature": 0.7},
            timeout=30)
        raw = ai.json()["choices"][0]["message"]["content"].strip()
        parsed = json.loads(raw)
        subject  = parsed.get("subject","Follow-up — Etendo ERP")
        body_txt = parsed.get("body_html","")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error IA: {e}")

    pixel_url = f"https://etendo-dashboard-api.onrender.com/pixel/{urllib.parse.quote(email)}.gif"
    signature = """<div style="margin-top:24px;border-top:1px solid #e6e6e6;padding-top:14px;font-family:Arial,sans-serif;font-size:13px;line-height:1.4;color:#333">
  <div style="font-size:16px;color:#1a2b6d;font-weight:700">Victoria Miguez</div>
  <div style="font-size:12px;color:#7a7a7a;text-transform:uppercase;letter-spacing:.4px">Encargada del Área Comercial<br>Etendo ERP</div>
  <div style="margin-top:10px">
    🌐 <a href="https://www.etendo.software" style="text-decoration:none;color:#1a2b6d">www.etendo.software</a><br>
    ✉️ <a href="mailto:victoria.miguez@etendo.software" style="text-decoration:none;color:#1a2b6d">victoria.miguez@etendo.software</a>
  </div>
  <div style="margin-top:10px"><a href="https://calendar.app.google/HyVddorYyUecHuJz9" style="color:#1a2b6d;text-decoration:underline">Reservá 30 min</a></div>
</div>"""
    body_html = (
        "<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.7;color:#222'>"
        + body_txt + "</div>" + signature
        + f'<img src="{pixel_url}" width="1" height="1" style="display:none">'
    )

    try:
        resp = requests.post(N8N_OUTREACH_WEBHOOK,
            json={"to": email, "subject": subject, "html": body_html}, timeout=20)
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"n8n error {resp.status_code}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al enviar: {e}")

    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    cf["attempts"]     = cf.get("attempts", 0) + 1
    cf["last_sent_at"] = now_iso
    cf["followup_sent_at"] = now_iso
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/contacts?id=eq.{contact_id}",
        headers={**_SB_HEADERS(), "Prefer": "return=minimal"},
        json={"custom_fields": cf}, timeout=10)

    return {"ok": True, "to": email, "subject": subject, "attempts": cf["attempts"]}


# ── Startup warmup ─────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_warmup():
    def _warmup():
        try:
            data = build_metrics(30)
            _cache_set(30, data)
            print("✅ Cache pre-warmed for period=30")
        except Exception as e:
            print(f"⚠️  Warmup failed: {e}")
    threading.Thread(target=_warmup, daemon=True).start()
