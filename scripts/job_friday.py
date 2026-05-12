#!/usr/bin/env python3
"""
Job Viernes — para cada cycle in_progress, agrega evidencia y propone nuevos
valores de KR. Inserta en kr_proposals (pending_approval) y manda email.

Fuentes de datos consideradas:
  - Iniciativas/tasks/progress_entries del ciclo (Supabase)
  - CRM Etendo: leads, strategic_fit, meetings, pipeline, deals
  - Google Search Console: impresiones, clics orgánicos non-brand
  - Google Ads: spend, conversiones, CPL (vía Sheet si disponible)
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
    """Devuelve métricas clave del CRM para la semana actual."""
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

        active = [l for l in leads if (l.get("leadStatus") or "") not in ("lost_deal", "won_deal", "disqualified")]
        fit_yes = [l for l in active if l.get("strategicFit") == "strategic_fit_yes"]
        meetings = [l for l in leads if l.get("meetingDate")]
        won = [l for l in leads if l.get("leadStatus") == "won_deal"]
        negotiation = [l for l in active if l.get("leadStatus") in ("negotiation", "proposal_sent")]
        proposal_sent = [l for l in active if l.get("leadStatus") == "proposal_sent"]
        hot = [l for l in fit_yes if (l.get("purchaseIntentionScore") or 0) >= 3]

        # Tiempo primer contacto (promedio de los últimos 10 leads con fecha)
        contact_times = []
        for l in leads[:50]:
            created = l.get("creationDate") or l.get("created_at", "")
            first_contact = l.get("firstContactDate") or l.get("firstContact", "")
            if created and first_contact:
                try:
                    from datetime import datetime
                    c = datetime.fromisoformat(created[:19])
                    f = datetime.fromisoformat(first_contact[:19])
                    diff_h = (f - c).total_seconds() / 3600
                    if 0 <= diff_h <= 720:
                        contact_times.append(diff_h)
                except Exception:
                    pass
        avg_contact_h = round(sum(contact_times) / len(contact_times), 1) if contact_times else None

        return {
            "total_leads_active":    len(active),
            "strategic_fit_yes":     len(fit_yes),
            "meetings_booked":       len(meetings),
            "won_deals":             len(won),
            "in_negotiation":        len(negotiation),
            "proposals_sent":        len(proposal_sent),
            "hot_leads_with_fit":    len(hot),
            "pct_hot_with_proposal": round(len(proposal_sent) / len(hot) * 100) if hot else 0,
            "avg_first_contact_h":   avg_contact_h,
        }
    except Exception as e:
        print(f"  [CRM] Error: {e}")
        return {}


# ── Fetch Search Console ──────────────────────────────────────────────────────

def fetch_search_console():
    """Devuelve impresiones y clics orgánicos de los últimos 28 días."""
    creds_file = os.environ.get("SEARCH_CONSOLE_CREDENTIALS_FILE", "")
    site       = os.environ.get("SEARCH_CONSOLE_SITE", "sc-domain:etendo.software")
    if not creds_file or not os.path.exists(creds_file):
        return {}
    try:
        import time, base64
        with open(creds_file) as f:
            creds = json.load(f)
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

        now    = int(time.time())
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=")
        claim  = base64.urlsafe_b64encode(json.dumps({
            "iss": creds["client_email"], "scope": "https://www.googleapis.com/auth/webmasters.readonly",
            "aud": "https://oauth2.googleapis.com/token", "exp": now + 3600, "iat": now,
        }).encode()).rstrip(b"=")
        privkey = serialization.load_pem_private_key(creds["private_key"].encode(), password=None)
        sig = base64.urlsafe_b64encode(privkey.sign(
            f"{header.decode()}.{claim.decode()}".encode(),
            asym_padding.PKCS1v15(), hashes.SHA256()
        )).rstrip(b"=")
        jwt_tok = f"{header.decode()}.{claim.decode()}.{sig.decode()}"

        tok_req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt_tok}).encode()
        )
        with urllib.request.urlopen(tok_req, timeout=10) as r:
            access_token = json.loads(r.read())["access_token"]

        end_date   = date.today() - timedelta(days=3)
        start_date = end_date - timedelta(days=27)
        payload = json.dumps({
            "startDate": str(start_date), "endDate": str(end_date),
            "dimensions": [], "rowLimit": 1,
        }).encode()
        sc_req = urllib.request.Request(
            f"https://www.googleapis.com/webmasters/v3/sites/{urllib.parse.quote(site, safe='')}/searchAnalytics/query",
            data=payload, method="POST"
        )
        sc_req.add_header("Authorization", f"Bearer {access_token}")
        sc_req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(sc_req, timeout=15) as r:
            totals = json.loads(r.read()).get("rows", [{}])[0]

        # Non-brand clicks (excluir queries con "etendo")
        nb_payload = json.dumps({
            "startDate": str(start_date), "endDate": str(end_date),
            "dimensions": [], "rowLimit": 1,
            "dimensionFilterGroups": [{"filters": [{"dimension": "query", "operator": "notContains", "expression": "etendo"}]}],
        }).encode()
        sc_req2 = urllib.request.Request(
            f"https://www.googleapis.com/webmasters/v3/sites/{urllib.parse.quote(site, safe='')}/searchAnalytics/query",
            data=nb_payload, method="POST"
        )
        sc_req2.add_header("Authorization", f"Bearer {access_token}")
        sc_req2.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(sc_req2, timeout=15) as r:
            nb = json.loads(r.read()).get("rows", [{}])[0]

        return {
            "period_days":      28,
            "total_impressions": int(totals.get("impressions", 0)),
            "total_clicks":     int(totals.get("clicks", 0)),
            "nonbrand_clicks":  int(nb.get("clicks", 0)),
            "nonbrand_impressions": int(nb.get("impressions", 0)),
        }
    except Exception as e:
        print(f"  [SearchConsole] Error: {e}")
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


# ── LLM propose ──────────────────────────────────────────────────────────────

def llm_propose(kr, current_value, evidence, external):
    inits_done = [i for i in evidence["initiatives"] if i["status"] in ("completed", "suggested_completed")]
    tasks_done = [t for t in evidence["tasks"] if t["status"] == "done"]
    entries    = evidence["entries"]

    # Evidencia interna (iniciativas/tasks/avances)
    ev_lines = []
    for i in inits_done:
        ev_lines.append(f"  ✓ Iniciativa completada: {i['title']}")
        if i.get("execution_plan"):
            ev_lines.append(f"    Plan ejecutado: {i['execution_plan'][:150]}")
    for t in tasks_done[:10]:
        ev_line = f"  ✓ Task: {t['title']}"
        if t.get("evidence_url"):
            ev_line += f" → {t['evidence_url']}"
        ev_lines.append(ev_line)
    for e in entries[:8]:
        ev_lines.append(f"  · Avance ({e.get('author_name','?')}): {e.get('body','')[:200]}")

    # Datos externos relevantes por KR
    ext_lines = []
    kr_name_lower = kr["name"].lower()

    crm = external.get("crm", {})
    sc  = external.get("search_console", {})
    mi  = external.get("market_intel", {})

    if crm:
        if any(k in kr_name_lower for k in ["lead", "fit", "strategic"]):
            ext_lines.append(f"  CRM — Leads activos: {crm.get('total_leads_active')} | Strategic fit YES: {crm.get('strategic_fit_yes')}")
        if any(k in kr_name_lower for k in ["meeting", "reunión", "contacto"]):
            ext_lines.append(f"  CRM — Meetings registrados: {crm.get('meetings_booked')}")
        if any(k in kr_name_lower for k in ["propuesta", "negociación", "deal"]):
            ext_lines.append(f"  CRM — En negociación: {crm.get('in_negotiation')} | Propuestas enviadas: {crm.get('proposals_sent')} | Won: {crm.get('won_deals')}")
            ext_lines.append(f"  CRM — Hot leads con propuesta: {crm.get('pct_hot_with_proposal')}%")
        if "tiempo" in kr_name_lower or "primer contacto" in kr_name_lower:
            ext_lines.append(f"  CRM — Tiempo promedio primer contacto: {crm.get('avg_first_contact_h')} horas")
        if "cpl" in kr_name_lower or "coste" in kr_name_lower:
            ext_lines.append(f"  CRM — Leads fit YES: {crm.get('strategic_fit_yes')} (denominador para calcular CPL real)")

    if sc:
        if any(k in kr_name_lower for k in ["impresión", "impresiones", "google search"]):
            ext_lines.append(f"  Search Console (28d) — Impresiones totales: {sc.get('total_impressions'):,} | Non-brand: {sc.get('nonbrand_impressions'):,}")
        if any(k in kr_name_lower for k in ["sesión", "orgánico", "non-brand", "search console"]):
            ext_lines.append(f"  Search Console (28d) — Clics non-brand: {sc.get('nonbrand_clicks'):,} | Totales: {sc.get('total_clicks'):,}")

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
- Priorizá los DATOS EXTERNOS REALES sobre las estimaciones de iniciativas
- Si hay datos CRM o Search Console concretos, usalos como base del proposed_value
- Si no hay evidencia suficiente, mantené el valor actual (no inventés progreso)
- El rationale debe citar explícitamente qué dato justifica el número propuesto

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
        "market_intel":   fetch_recent_market_intel(team_slug),
    }
    print(f"  CRM: {len(external['crm'])} métricas | SC: {len(external['search_console'])} métricas | MI: {'sí' if external['market_intel'].get('resumen') else 'no'}")

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
            proposals.append({
                "cycle_id":       cycle["id"],
                "kr_id":          kr["id"],
                "kr_name":        kr["name"],
                "current_value":  kr.get("current"),
                "proposed_value": pr.get("proposed_value"),
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
