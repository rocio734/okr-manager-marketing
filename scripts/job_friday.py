#!/usr/bin/env python3
"""
Job Viernes — para cada cycle in_progress, agrega evidencia y propone nuevos
valores de KR. Inserta en kr_proposals (pending_approval) y manda email.

Fuentes de datos consideradas:
  - Iniciativas/tasks/progress_entries del ciclo (Supabase)
  - CRM Etendo: leads, strategic_fit, meetings, pipeline, deals
  - Google Search Console: impresiones, clics orgánicos non-brand
  - Google Analytics 4: sesiones CPC, key events, tasa de conversión
  - Google Ads: spend, conversiones, CPL por campaña
  - Market intel reciente (Supabase)
"""
import argparse, json, os, urllib.request, urllib.parse
from datetime import date, timedelta
from _etendo import (sb_request, llm_call, send_email,
                     APPROVER_EMAIL, SITE_URL,
                     ETENDO_BASE, etendo_login)

# Rol Comercial — único con acceso a ECLM_Lead
COMERCIAL_ROLE_ID = "8351131DFF384725AB08E06773FE6144"

# ── Fetch CRM ─────────────────────────────────────────────────────────────────

def _crm_token():
    try:
        return etendo_login(COMERCIAL_ROLE_ID)
    except Exception:
        return ""


def fetch_crm_snapshot():
    """Devuelve métricas clave del CRM para la semana actual.

    Campos confirmados en ECLM_Lead:
      - leadStatus: in_progress | proposal_sent | negotiation | won_deal | lost_deal | disqualified | cold_archived
      - strategicFit: strategic_fit_yes | strategic_fit_no | (vacío)   ← campo correcto (NO leadQualy)
      - scorePurchaseIntention: 1–5                                      ← campo correcto (NO purchaseIntentionScore)
      - scoreIndex: número
      - leadScore: número
      - meetingDate: fecha reunión agendada
      - creationDate: fecha creación
      - firstContactDate: fecha primer contacto
    """
    jwt = _crm_token()
    if not jwt:
        return {}
    try:
        leads = []
        start = 0
        while True:
            params = urllib.parse.urlencode({"_startRow": start, "_endRow": start + 100})
            req = urllib.request.Request(f"{ETENDO_BASE}/api/datasource/ECLM_Lead?{params}")
            req.add_header("Authorization", f"Bearer {jwt}")
            with urllib.request.urlopen(req, timeout=20) as r:
                batch = json.loads(r.read()).get("response", {}).get("data", [])
            if not batch:
                break
            leads.extend(batch)
            start += 100
            if len(batch) < 100:
                break

        INACTIVE = {"lost_deal", "won_deal", "disqualified", "cold_archived"}
        active        = [l for l in leads if (l.get("leadStatus") or "") not in INACTIVE]
        fit_yes       = [l for l in active if l.get("strategicFit") == "strategic_fit_yes"]
        # scorePurchaseIntention es el campo real en el CRM (confirmado 2026-05-20)
        hot           = [l for l in fit_yes if int(l.get("scorePurchaseIntention") or 0) >= 3]
        won           = [l for l in leads if l.get("leadStatus") == "won_deal"]

        # Negociación: no existe leadStatus fijo — se detecta por keywords en summary
        _negotiation_keywords = ["negociación", "negociacion", "negociando",
                                  "evaluando propuesta", "revisando propuesta",
                                  "etapa de cierre", "en proceso de cierre"]
        def _in_negotiation(lead):
            if lead.get("leadStatus") == "negotiation":
                return True
            summ = (lead.get("summary") or "").lower()
            return any(k in summ for k in _negotiation_keywords)
        negotiation = [l for l in active if _in_negotiation(l)]

        # Meetings: no usar meetingDate (no se actualiza consistentemente) — detectar en summary
        _meeting_keywords = ["reunión", "reunion", "meeting", "demo realiz",
                              "llamada exitosa", "llamada ok", "tuvimos llamada",
                              "hicimos demo", "se realizó demo", "demo completada",
                              "reunimos", "nos reunimos", "presentamos", "demo hecha"]
        cutoff_30d_str = (date.today() - timedelta(days=29)).isoformat()
        meetings = [l for l in leads if any(
            k in (l.get("summary") or "").lower() for k in _meeting_keywords
        )]
        # % hot con propuesta: buscar en summary o leadStatus
        # No existe campo proposalSent — se detecta por keywords en summary o por leadStatus
        _proposal_keywords = ["propuesta enviada", "propuesta presentada", "envié propuesta",
                               "mandé propuesta", "mandamos propuesta", "enviamos propuesta",
                               "envió propuesta", "propuesta de", "enviamos la propuesta",
                               "mandamos la propuesta", "envie propuesta", "mande propuesta"]
        def _has_proposal(lead):
            if lead.get("leadStatus") in ("proposal_sent", "negotiation"):
                return True
            summ = (lead.get("summary") or "").lower()
            return any(k in summ for k in _proposal_keywords)
        proposal_sent = [l for l in hot if _has_proposal(l)]
        unclassified  = [l for l in active if not l.get("strategicFit") or l.get("strategicFit") == "unclassified"]

        # Score promedio de los leads fit (para medir calidad del pipeline)
        spi_values = [int(l.get("scorePurchaseIntention") or 0) for l in fit_yes if l.get("scorePurchaseIntention")]
        avg_spi = round(sum(spi_values) / len(spi_values), 1) if spi_values else None

        # Tiempo primer contacto — usa whatsAppContactDate (primer toque automatizado via Elena)
        # firstContactDate no existe en el CRM; whatsAppContactDate es el campo más cercano disponible
        from datetime import datetime as dt
        contact_times = []
        cutoff_60d = (date.today() - timedelta(days=60)).isoformat()
        recent_leads = [l for l in leads if (l.get("creationDate") or "")[:10] >= cutoff_60d]
        for l in recent_leads:
            created = l.get("creationDate") or ""
            wp_date = l.get("whatsAppContactDate") or ""
            if created and wp_date:
                try:
                    created_dt = dt.fromisoformat(created[:10])
                    contact_dt = dt.fromisoformat(wp_date[:10])
                    diff_h = (contact_dt - created_dt).total_seconds() / 3600
                    if 0 <= diff_h <= 720:
                        contact_times.append(diff_h)
                except Exception:
                    pass
        avg_contact_h = round(sum(contact_times) / len(contact_times), 1) if contact_times else None

        # Leads fit creados en los últimos 30 días (para CPL real = Ads spend / fit leads)
        cutoff_30d = (dt.today() - timedelta(days=29)).strftime("%Y-%m-%d")
        new_fit_30d = [
            l for l in fit_yes
            if (l.get("creationDate") or "")[:10] >= cutoff_30d
        ]

        return {
            "total_leads_active":    len(active),
            "strategic_fit_yes":     len(fit_yes),
            "hot_leads":             len(hot),
            "unclassified_leads":    len(unclassified),
            "meetings_booked":       len(meetings),
            "won_deals":             len(won),
            "in_negotiation":        len(negotiation),
            "negotiation_count":     len(negotiation),
            "proposals_sent":        len(proposal_sent),
            "pct_hot_with_proposal": round(len(proposal_sent) / len(hot) * 100) if hot else 0,
            "avg_spi_fit_leads":     avg_spi,
            "avg_first_contact_h":   avg_contact_h,
            "new_fit_leads_30d":     len(new_fit_30d),   # para CPL real mensual
        }
    except Exception as e:
        print(f"  [CRM] Error: {e}")
        return {}


# ── Fetch Search Console ──────────────────────────────────────────────────────

def fetch_search_console():
    """Devuelve impresiones y clics orgánicos de los últimos 28 días vía OAuth."""
    try:
        token = _google_access_token("GOOGLE_REFRESH_TOKEN_GA4_SC")
        if not token:
            return {}

        env_file = os.path.join(os.path.dirname(__file__), "..", "..", ".env.google")
        site = "sc-domain:etendo.software"
        for line in open(env_file).read().splitlines():
            if line.startswith("SC_SITE"):
                site = line.split("=", 1)[1].strip().strip('"')

        end_date   = date.today() - timedelta(days=3)
        start_date = end_date - timedelta(days=27)
        site_enc   = urllib.parse.quote(site, safe="")
        sc_url     = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{site_enc}/searchAnalytics/query"

        def sc_query(extra=None):
            body = {"startDate": str(start_date), "endDate": str(end_date), "dimensions": [], "rowLimit": 1}
            if extra:
                body.update(extra)
            req = urllib.request.Request(sc_url, data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()).get("rows", [{}])[0]

        totals = sc_query()
        nb     = sc_query({"dimensionFilterGroups": [{"filters": [
            {"dimension": "query", "operator": "notContains", "expression": "etendo"}
        ]}]})

        # Top países esta semana (últimos 7 días)
        end_w   = date.today() - timedelta(days=1)
        start_w = end_w - timedelta(days=6)
        req_w = urllib.request.Request(sc_url,
            data=json.dumps({"startDate": str(start_w), "endDate": str(end_w),
                             "dimensions": ["country"], "rowLimit": 5}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req_w, timeout=15) as r:
            top_countries = {
                row["keys"][0]: {"clicks": row["clicks"], "impressions": row["impressions"]}
                for row in json.loads(r.read()).get("rows", [])
            }

        return {
            "period_days":           28,
            "total_impressions":     int(totals.get("impressions", 0)),
            "total_clicks":          int(totals.get("clicks", 0)),
            "nonbrand_clicks":       int(nb.get("clicks", 0)),
            "nonbrand_impressions":  int(nb.get("impressions", 0)),
            "avg_position":          round(totals.get("position", 0), 1),
            "top_countries_7d":      top_countries,
            "spain_clicks_7d":       top_countries.get("esp", {}).get("clicks", 0),
            "argentina_clicks_7d":   top_countries.get("arg", {}).get("clicks", 0),
        }
    except Exception as e:
        print(f"  [SearchConsole] Error: {e}")
        return {}


# ── Google OAuth helper ──────────────────────────────────────────────────────

def _google_access_token(refresh_token_env: str) -> str:
    """Obtiene access token usando el refresh token guardado en .env.google."""
    env_file = os.path.join(os.path.dirname(__file__), "..", "..", ".env.google")
    env_vars = {}
    if os.path.exists(env_file):
        for line in open(env_file).read().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"')

    # Fallback: env vars del sistema (GitHub Actions secrets)
    client_id     = env_vars.get("GOOGLE_OAUTH_CLIENT_ID", "") or os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = env_vars.get("GOOGLE_OAUTH_CLIENT_SECRET", "") or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    refresh_token = env_vars.get(refresh_token_env, "") or os.environ.get(refresh_token_env, "")

    if not all([client_id, client_secret, refresh_token]):
        return ""

    resp = urllib.request.urlopen(urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        }).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ), timeout=10)
    return json.loads(resp.read()).get("access_token", "")


# ── Fetch GA4 ─────────────────────────────────────────────────────────────────

def fetch_ga4() -> dict:
    """Sesiones CPC, key events y bounce rate de los últimos 7 días."""
    try:
        token = _google_access_token("GOOGLE_REFRESH_TOKEN_GA4_SC")
        if not token:
            return {}

        env_file = os.path.join(os.path.dirname(__file__), "..", "..", ".env.google")
        prop = "353675924"
        for line in open(env_file).read().splitlines():
            if line.startswith("GA4_PROPERTY_ID"):
                prop = line.split("=", 1)[1].strip().strip('"')

        end_date   = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=6)

        def run(payload):
            req = urllib.request.Request(
                f"https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport",
                data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())

        # Totales CPC
        r = run({
            "dateRanges": [{"startDate": str(start_date), "endDate": str(end_date)}],
            "metrics": [
                {"name": "sessions"}, {"name": "keyEvents"},
                {"name": "engagementRate"}, {"name": "bounceRate"},
            ],
            "dimensionFilter": {"filter": {"fieldName": "sessionMedium",
                "stringFilter": {"value": "cpc", "matchType": "EXACT"}}},
        })
        row = r.get("rows", [{}])[0].get("metricValues", [{}, {}, {}, {}])
        total_sessions  = int(row[0].get("value", 0))
        total_key_events = int(row[1].get("value", 0))
        engagement_rate = float(row[2].get("value", 0))
        bounce_rate     = float(row[3].get("value", 0))

        # Por país
        r2 = run({
            "dateRanges": [{"startDate": str(start_date), "endDate": str(end_date)}],
            "dimensions": [{"name": "country"}],
            "metrics": [{"name": "sessions"}, {"name": "keyEvents"}],
            "dimensionFilter": {"filter": {"fieldName": "sessionMedium",
                "stringFilter": {"value": "cpc", "matchType": "EXACT"}}},
            "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
            "limit": 5,
        })
        by_country = {
            row["dimensionValues"][0]["value"]: {
                "sessions":   int(row["metricValues"][0]["value"]),
                "key_events": int(row["metricValues"][1]["value"]),
            }
            for row in r2.get("rows", [])
        }

        # Ventana 30 días para CPL mensual (consistente con el resto de KRs)
        start_30 = end_date - timedelta(days=29)
        r_30 = run({
            "dateRanges": [{"startDate": str(start_30), "endDate": str(end_date)}],
            "metrics": [{"name": "keyEvents"}, {"name": "sessions"}],
            "dimensionFilter": {"filter": {"fieldName": "sessionMedium",
                "stringFilter": {"value": "cpc", "matchType": "EXACT"}}},
        })
        row_30 = r_30.get("rows", [{}])[0].get("metricValues", [{}, {}])
        monthly_key_events = int(row_30[0].get("value", 0))
        monthly_sessions   = int(row_30[1].get("value", 0))

        return {
            "period_days":          7,
            "cpc_sessions":         total_sessions,
            "cpc_key_events":       total_key_events,
            "engagement_rate":      round(engagement_rate * 100, 1),
            "bounce_rate":          round(bounce_rate * 100, 1),
            "by_country":           by_country,
            "argentina_sessions":   by_country.get("Argentina", {}).get("sessions", 0),
            "argentina_key_events": by_country.get("Argentina", {}).get("key_events", 0),
            "spain_sessions":       by_country.get("Spain", {}).get("sessions", 0),
            "spain_key_events":     by_country.get("Spain", {}).get("key_events", 0),
            "monthly_key_events":   monthly_key_events,
            "monthly_sessions":     monthly_sessions,
        }
    except Exception as e:
        print(f"  [GA4] Error: {e}")
        return {}


# ── Fetch Google Ads ──────────────────────────────────────────────────────────

def fetch_google_ads() -> dict:
    """Gasto, conversiones y CPL de campañas activas — últimos 7 días."""
    try:
        token = _google_access_token("GOOGLE_REFRESH_TOKEN_ADS")
        if not token:
            return {}

        env_file = os.path.join(os.path.dirname(__file__), "..", "..", ".env.google")
        env_vars = {}
        for line in open(env_file).read().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"')

        dev_token   = env_vars.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
        mcc_id      = env_vars.get("GOOGLE_ADS_MCC_CUSTOMER_ID", "")
        customer_id = env_vars.get("GOOGLE_ADS_CLIENT_CUSTOMER_ID", "")
        api_version = env_vars.get("GOOGLE_ADS_API_VERSION", "v20")

        if not all([dev_token, customer_id]):
            return {}

        end_date   = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=6)

        query = f"""
            SELECT campaign.name, campaign.status,
                metrics.cost_micros, metrics.clicks,
                metrics.impressions, metrics.conversions, metrics.ctr
            FROM campaign
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
              AND campaign.status = 'ENABLED'
        """
        headers = {
            "Authorization": f"Bearer {token}",
            "developer-token": dev_token,
            "Content-Type": "application/json",
        }
        if mcc_id:
            headers["login-customer-id"] = mcc_id

        req = urllib.request.Request(
            f"https://googleads.googleapis.com/{api_version}/customers/{customer_id}/googleAds:search",
            data=json.dumps({"query": query}).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            results = json.loads(r.read()).get("results", [])

        campaigns = {}
        total_spend = total_conversions = total_clicks = 0
        for row in results:
            name  = row["campaign"]["name"]
            m     = row["metrics"]
            spend = int(m.get("costMicros", 0)) / 1_000_000
            convs = float(m.get("conversions", 0))
            clicks = int(m.get("clicks", 0))
            campaigns[name] = {
                "spend": round(spend, 2),
                "conversions": round(convs, 1),
                "clicks": clicks,
                "cpa": round(spend / convs, 2) if convs > 0 else 0,
            }
            total_spend       += spend
            total_conversions += convs
            total_clicks      += clicks

        # Ventana 30 días para CPL mensual
        start_30 = end_date - timedelta(days=29)
        m_query = f"""
            SELECT metrics.cost_micros
            FROM campaign
            WHERE segments.date BETWEEN '{start_30}' AND '{end_date}'
              AND campaign.status = 'ENABLED'
        """
        req_m = urllib.request.Request(
            f"https://googleads.googleapis.com/{api_version}/customers/{customer_id}/googleAds:search",
            data=json.dumps({"query": m_query}).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req_m, timeout=15) as r:
            m_results = json.loads(r.read()).get("results", [])
        monthly_spend = sum(int(row["metrics"].get("costMicros", 0)) for row in m_results) / 1_000_000

        return {
            "period_days":        7,
            "total_spend":        round(total_spend, 2),
            "total_conversions":  round(total_conversions, 1),
            "total_clicks":       total_clicks,
            "cpl_ads":            round(total_spend / total_conversions, 2) if total_conversions > 0 else 0,
            "campaigns":          campaigns,
            "monthly_spend":      round(monthly_spend, 2),
        }
    except Exception as e:
        print(f"  [GoogleAds] Error: {e}")
        return {}


# ── Fetch market_intel reciente ───────────────────────────────────────────────

def fetch_recent_market_intel(team):
    try:
        rows = sb_request("GET", f"market_intel?team=eq.{team}&order=generated_at.desc&limit=1&select=generated_at,analysis")
        if not rows:
            return {}
        row = rows[0]
        a = row.get("analysis") or {}
        return {
            "generated_at": row.get("generated_at", "")[:10],
            "resumen":      a.get("resumen_ejecutivo", ""),
            "oportunidades": [o.get("descripcion", "") for o in (a.get("oportunidades") or [])[:3]],
        }
    except Exception:
        return {}


# ── Cálculo determinístico de KRs con datos reales ───────────────────────────
# Para KRs que tienen una fórmula exacta, el valor se calcula en Python.
# El LLM SOLO redacta la justificación — nunca decide el número.

def deterministic_value(kr_name: str, crm: dict, sc: dict, ga4: dict, ads: dict):
    """
    Devuelve (value, source_note) si el KR tiene cálculo determinístico,
    o (None, None) si debe estimarlo el LLM.
    """
    name = kr_name.lower()

    # CPL real = gasto Ads 30d / leads fit creados en CRM 30d
    if any(k in name for k in ["cpl", "coste por lead"]):
        spend     = (ads or {}).get("monthly_spend", 0)
        new_fit   = (crm or {}).get("new_fit_leads_30d", 0)
        if spend > 0 and new_fit > 0:
            return round(spend / new_fit, 2), f"€{spend} gasto Ads 30d / {new_fit} leads fit = €{round(spend/new_fit,2)}"
        if spend > 0 and new_fit == 0:
            return None, "gasto pero 0 leads fit — CPL incalculable"

    # Impresiones Google Search = SC total_impressions 28d
    if any(k in name for k in ["impresion", "impresión"]) and "search" in name:
        val = (sc or {}).get("total_impressions")
        if val:
            return val, f"Search Console 28d: {val:,} impresiones totales"

    # Sesiones orgánicas non-brand = SC nonbrand_clicks 28d
    if "sesion" in name and ("orgánic" in name or "organc" in name or "non-brand" in name or "search console" in name):
        val = (sc or {}).get("nonbrand_clicks")
        if val is not None:
            return val, f"Search Console 28d: {val} clics non-brand"

    # Tiempo primer contacto = promedio whatsAppContactDate vs creationDate
    if "tiempo" in name and "contacto" in name:
        val = (crm or {}).get("avg_first_contact_h")
        if val is not None:
            return val, f"CRM: promedio {val}h (whatsAppContactDate vs creationDate, últimos 60d)"

    # Meetings/mes = detectados en summary CRM
    if "meeting" in name or ("reunión" in name and "mes" in name) or ("meetings" in name):
        val = (crm or {}).get("meetings_booked")
        if val is not None:
            return val, f"CRM summary: {val} reuniones/llamadas detectadas en notas"

    # Leads en negociación = detectados en summary
    if "negociaci" in name:
        val = (crm or {}).get("negotiation_count")
        if val is not None:
            return val, f"CRM summary: {val} leads con keywords de negociación"

    # Leads strategic_fit_yes = directo del CRM
    if "strategic_fit" in name or ("leads" in name and "fit" in name):
        val = (crm or {}).get("strategic_fit_yes")
        if val is not None:
            return val, f"CRM: {val} leads con strategic_fit_yes activos"

    return None, None


# ── LLM propose ──────────────────────────────────────────────────────────────

def llm_propose(kr, current_value, evidence, external):
    inits_done     = [i for i in evidence["initiatives"] if i["status"] in ("completed", "suggested_completed")]
    inits_progress = [i for i in evidence["initiatives"] if i["status"] in ("approved", "in_progress")]
    tasks_done     = [t for t in evidence["tasks"] if t["status"] == "done"]
    tasks_total    = len(evidence["tasks"])
    entries        = evidence["entries"]

    # Evidencia interna (iniciativas completadas + en progreso)
    ev_lines = []
    for i in inits_done:
        # Contar tasks completadas de esta iniciativa
        done_count = len([t for t in tasks_done if t.get("initiative_id") == i["id"]])
        total_count = len([t for t in evidence["tasks"] if t.get("initiative_id") == i["id"]])
        ev_lines.append(f"  ✓ COMPLETADA esta semana: {i['title']} ({done_count}/{total_count} tasks done)")
        if i.get("execution_plan"):
            ev_lines.append(f"    Qué se hizo: {i['execution_plan'][:150]}")
    for i in inits_progress:
        done_count = len([t for t in tasks_done if t.get("initiative_id") == i["id"]])
        total_count = len([t for t in evidence["tasks"] if t.get("initiative_id") == i["id"]])
        ev_lines.append(f"  ⏳ EN PROGRESO esta semana: {i['title']} ({done_count}/{total_count} tasks done)")
    for t in tasks_done[:8]:
        ev_line = f"  ✓ Task ejecutada: {t['title']}"
        if t.get("evidence_url"):
            ev_line += f" → {t['evidence_url']}"
        ev_lines.append(ev_line)
    for e in entries[:8]:
        ev_lines.append(f"  · Avance ({e.get('author_name','?')}): {e.get('body','')[:200]}")

    # Datos externos relevantes por KR
    ext_lines = []
    kr_name_lower = kr["name"].lower()

    crm  = external.get("crm", {})
    sc   = external.get("search_console", {})
    ga4  = external.get("ga4", {})
    ads  = external.get("google_ads", {})
    mi   = external.get("market_intel", {})

    if crm:
        if any(k in kr_name_lower for k in ["lead", "fit", "strategic", "pipeline"]):
            ext_lines.append(
                f"  CRM — Leads activos: {crm.get('total_leads_active')}"
                f" | Fit YES (strategicFit=strategic_fit_yes): {crm.get('strategic_fit_yes')}"
                f" | Hot (fit+SPI≥3): {crm.get('hot_leads')}"
                f" | Sin clasificar: {crm.get('unclassified_leads')}"
            )
            if crm.get("avg_spi_fit_leads"):
                ext_lines.append(f"  CRM — SPI promedio fit leads: {crm.get('avg_spi_fit_leads')} (escala 1–5)")
        if any(k in kr_name_lower for k in ["meeting", "reunión", "contacto"]):
            ext_lines.append(f"  CRM — Meetings registrados: {crm.get('meetings_booked')}")
        if any(k in kr_name_lower for k in ["propuesta", "negociación", "deal", "cierre"]):
            ext_lines.append(
                f"  CRM — En negociación: {crm.get('in_negotiation')}"
                f" | Propuestas enviadas: {crm.get('proposals_sent')}"
                f" | Won deals: {crm.get('won_deals')}"
            )
            ext_lines.append(f"  CRM — % hot leads con propuesta: {crm.get('pct_hot_with_proposal')}%")
        if "tiempo" in kr_name_lower or "primer contacto" in kr_name_lower:
            ext_lines.append(f"  CRM — Tiempo promedio primer contacto: {crm.get('avg_first_contact_h')} horas")
        if "cpl" in kr_name_lower or "coste" in kr_name_lower:
            ext_lines.append(
                f"  CRM — Leads fit YES: {crm.get('strategic_fit_yes')}"
                f" | Hot leads: {crm.get('hot_leads')} — usar fit_yes como denominador CPL"
            )

    if sc:
        if any(k in kr_name_lower for k in ["impresión", "impresiones", "google search"]):
            ext_lines.append(f"  Search Console (28d) — Impresiones totales: {sc.get('total_impressions'):,} | Non-brand: {sc.get('nonbrand_impressions'):,}")
        if any(k in kr_name_lower for k in ["sesión", "orgánico", "non-brand", "search console"]):
            ext_lines.append(f"  Search Console (28d) — Clics non-brand: {sc.get('nonbrand_clicks'):,} | Totales: {sc.get('total_clicks'):,}")

    if ga4:
        if any(k in kr_name_lower for k in ["sesión", "tráfico", "visita", "cpc", "paid"]):
            ext_lines.append(
                f"  GA4 (7d) — Sesiones CPC: {ga4.get('cpc_sessions')} | Key events: {ga4.get('cpc_key_events')}"
                f" | Engagement: {ga4.get('engagement_rate')}% | Bounce: {ga4.get('bounce_rate')}%"
            )
            ext_lines.append(
                f"  GA4 (7d) — Argentina: {ga4.get('argentina_sessions')} ses / {ga4.get('argentina_key_events')} conv"
                f" | España: {ga4.get('spain_sessions')} ses / {ga4.get('spain_key_events')} conv"
            )
        if any(k in kr_name_lower for k in ["cpl", "coste", "conversión", "lead pago"]):
            ext_lines.append(
                f"  GA4 (7d) — Key events (form_submit_web) reales: {ga4.get('cpc_key_events')}"
                f" — usar este número como conversiones reales, NO el de Google Ads"
            )

    if ads:
        if any(k in kr_name_lower for k in ["cpl", "coste", "gasto", "inversión", "paid", "google ads"]):
            ext_lines.append(
                f"  Google Ads (7d) — Gasto total: €{ads.get('total_spend')} | Clicks: {ads.get('total_clicks')}"
                f" | Conv (Ads): {ads.get('total_conversions')} | CPL Ads: €{ads.get('cpl_ads')}"
            )
            for camp_name, camp in (ads.get("campaigns") or {}).items():
                ext_lines.append(
                    f"    · {camp_name}: €{camp['spend']} | {camp['clicks']} clicks"
                    f" | {camp['conversions']} conv | CPA €{camp['cpa']}"
                )
        if any(k in kr_name_lower for k in ["cpl", "coste"]):
            # CPL real = gasto Ads 30d / leads strategic_fit_yes creados en CRM últimos 30d
            # No usamos GA4 key events porque muchos formularios son B2C (Argentina mobile)
            m_ads_spend  = ads.get("monthly_spend", 0) if ads else 0
            new_fit_30d  = crm.get("new_fit_leads_30d", 0) if crm else 0
            real_cpl     = round(m_ads_spend / new_fit_30d, 2) if new_fit_30d > 0 else None
            if real_cpl:
                ext_lines.append(
                    f"  CPL REAL últimos 30 días (€{m_ads_spend} gasto Ads / {new_fit_30d} leads fit en CRM): €{real_cpl}"
                    f" — usar este como valor del KR (target: €50, baseline: €87)"
                )
            elif m_ads_spend > 0:
                ext_lines.append(
                    f"  CPL REAL: €{m_ads_spend} gasto Ads / 0 leads fit este mes = sin leads calificados"
                    f" — valor del KR debe reflejar ausencia de resultados"
                )

    if mi and mi.get("resumen"):
        if any(k in kr_name_lower for k in ["mención", "linkedin", "quora", "orgánico"]):
            ext_lines.append(f"  Mercado ({mi.get('generated_at')}) — {mi.get('resumen','')[:200]}")

    prompt = f"""Sos un analyst senior de OKRs. Proponés el nuevo valor real de un KR basándote en evidencia objetiva.

KR: {kr['name']}
Valor actual registrado: {current_value or '—'}
Target del período: {kr.get('target') or '—'}
Baseline: {kr.get('baseline') or '—'}

═══ EVIDENCIA INTERNA (iniciativas y tasks de este ciclo) ═══
{chr(10).join(ev_lines) if ev_lines else '  (sin evidencia registrada esta semana)'}

═══ DATOS EXTERNOS REALES ═══
{chr(10).join(ext_lines) if ext_lines else '  (sin datos externos disponibles)'}

INSTRUCCIONES:
- Priorizá los DATOS EXTERNOS REALES cuando están disponibles y son recientes
- Si hay iniciativas COMPLETADAS esta semana cuyo impacto todavía no aparece en los datos externos (lag normal de 3-7 días en GA4, Search Console, CRM), consideralas como evidencia cualitativa de progreso inminente y proponé un valor levemente superior al actual
- Si hay iniciativas EN PROGRESO, consideralas como señal de que el KR va a mejorar la semana siguiente
- Si no hay ni datos externos ni iniciativas ejecutadas, mantené el valor actual
- El rationale debe citar qué dato o iniciativa justifica el número propuesto y mencionar si hay lag esperado

Devolvé SOLO este JSON:
{{"proposed_value": <número>, "rationale": "<2-4 líneas citando evidencia concreta>"}}"""

    return json.loads(llm_call(prompt, max_tokens=600))


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", help="Filtrar por team (slug)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--any-status", action="store_true",
                    help="Buscar ciclo por fecha actual en lugar de requerir status=in_progress")
    args = ap.parse_args()

    if args.any_status:
        today = date.today().isoformat()
        q = f"cycles?week_start=lte.{today}&week_end=gte.{today}&select=*&order=week_start.desc&limit=1"
        if args.team:
            q += f"&team=eq.{args.team}"
    else:
        q = "cycles?status=eq.in_progress&select=*"
        if args.team:
            q += f"&team=eq.{args.team}"

    cycles = sb_request("GET", q)
    if not cycles:
        print("Sin cycles activos para esta semana.")
        return

    # Obtener datos externos una sola vez (son compartidos entre KRs)
    print("  Obteniendo datos externos...")
    team_slug = (args.team or (cycles[0]["team"] if cycles else "marketing"))
    external = {
        "crm":            fetch_crm_snapshot(),
        "search_console": fetch_search_console(),
        "ga4":            fetch_ga4(),
        "google_ads":     fetch_google_ads(),
        "market_intel":   fetch_recent_market_intel(team_slug),
    }
    print(f"  CRM: {len(external['crm'])} métricas"
          f" | SC: {len(external['search_console'])} métricas"
          f" | GA4: {len(external['ga4'])} métricas"
          f" | Ads: {len(external['google_ads'])} métricas"
          f" | MI: {'sí' if external['market_intel'].get('resumen') else 'no'}")

    for cycle in cycles:
        print(f"\n=== Cycle {cycle['id']} — team {cycle['team']} ({cycle['week_start']}..{cycle['week_end']}) ===")
        snapshot = cycle.get("kr_snapshot") or []
        inits    = sb_request("GET", f"initiatives?cycle_id=eq.{cycle['id']}&select=*") or []
        init_ids = ",".join(str(i["id"]) for i in inits) or "0"
        tasks    = sb_request("GET", f"tasks?initiative_id=in.({init_ids})&select=*") or []
        entries  = sb_request("GET", f"progress_entries?initiative_id=in.({init_ids})&select=*") or []

        proposals = []
        for kr in snapshot:
            kr_inits    = [i for i in inits  if i["kr_id"] == kr["id"]]
            kr_init_ids = {i["id"] for i in kr_inits}
            kr_tasks    = [t for t in tasks  if t["initiative_id"] in kr_init_ids]
            kr_entries  = [e for e in entries if e["initiative_id"] in kr_init_ids]
            evidence    = {"initiatives": kr_inits, "tasks": kr_tasks, "entries": kr_entries}
            try:
                pr = llm_propose(kr, kr.get("current"), evidence, external)
            except Exception as e:
                print(f"  ✗ LLM error en KR {kr['name']}: {e}")
                continue

            # Validación determinística: si hay cálculo exacto, lo imponemos
            det_val, det_note = deterministic_value(
                kr["name"],
                external.get("crm", {}),
                external.get("search_console", {}),
                external.get("ga4", {}),
                external.get("google_ads", {}),
            )
            if det_val is not None:
                if pr.get("proposed_value") != det_val:
                    print(f"  ⚠ KR {kr['name'][:40]}: LLM propuso {pr.get('proposed_value')} → corregido a {det_val} ({det_note})")
                pr["proposed_value"] = det_val
                pr["rationale"] = f"[DATO CALCULADO] {det_note}. " + (pr.get("rationale") or "")

            proposals.append({
                "cycle_id":       cycle["id"],
                "kr_id":          kr["id"],
                "kr_name":        kr["name"],
                "current_value":  kr.get("current"),
                "proposed_value": pr.get("proposed_value"),
                "target_value":   kr.get("target"),
                "rationale":      pr.get("rationale"),
                "evidence_summary": {
                    "n_initiatives_done": len([i for i in kr_inits if i["status"] in ("completed","suggested_completed")]),
                    "n_tasks_done":       len([t for t in kr_tasks if t["status"] == "done"]),
                    "n_progress_entries": len(kr_entries),
                    "crm_available":      bool(external["crm"]),
                    "sc_available":       bool(external["search_console"]),
                },
                "status": "pending_approval",
            })

        if args.dry_run:
            for p in proposals:
                print(f"  KR {p['kr_name']}: {p['current_value']} → {p['proposed_value']}")
                print(f"    Rationale: {p['rationale']}")
            continue

        if proposals:
            sb_request("DELETE", f"kr_proposals?cycle_id=eq.{cycle['id']}&status=eq.pending_approval")
            sb_request("POST", "kr_proposals", proposals)
            sb_request("PATCH", f"cycles?id=eq.{cycle['id']}", {"status": "pending_kr_approval"})
            print(f"  + {len(proposals)} propuestas insertadas. Cycle → pending_kr_approval.")

        try:
            html = f"""
            <h2>OKR Manager — Viernes {date.today().strftime('%d %b %Y')}</h2>
            <p>El agente generó <strong>{len(proposals)} propuestas</strong> para el team
            <strong>{cycle['team']}</strong> con datos de CRM, Search Console y market intel.</p>
            <p><a href="{SITE_URL}/kr-proposals-friday.html"
                  style="display:inline-block;padding:12px 24px;background:#FFD700;color:#1a1a2e;
                         text-decoration:none;border-radius:8px;font-weight:700;">
              Revisar valores KR a aprobar
            </a></p>
            """
            send_email(APPROVER_EMAIL, f"OKR {cycle['team']} — Valores KR a aprobar", html)
            print(f"  Email enviado a {APPROVER_EMAIL}")
        except Exception as e:
            print(f"  ✗ Error mandando mail: {e}")


if __name__ == "__main__":
    main()
