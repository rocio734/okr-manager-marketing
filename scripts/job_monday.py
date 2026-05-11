#!/usr/bin/env python3
"""
Job Lunes — OKR Manager.

Para cada team configurado (reports/okr_coach_configs/*.json):
  1. Lee KRs desde Etendo (API JWT).
  2. Busca contexto de ciclos anteriores (completadas, rechazadas).
  3. Crea cycle nuevo en Supabase.
  4. Para cada KR genera 3 iniciativas con LLM (sin repetir anteriores).
  5. Inserta initiatives en status=proposed.
  6. Manda email al aprobador con link a /approval-monday.html.

Uso:
  python3 job_monday.py                           # todos los teams
  python3 job_monday.py --team marketing          # solo Marketing
  python3 job_monday.py --dry-run                 # no escribe nada
"""
import argparse, json, sys
from datetime import date, timedelta
from _etendo import (all_team_configs, etendo_login, fetch_team_krs,
                     sb_request, llm_call, send_email,
                     APPROVER_EMAIL, SITE_URL)


def fetch_historical_context(kr_id):
    """Devuelve contexto completo de ciclos anteriores para un KR."""
    try:
        past = sb_request("GET",
            f"initiatives?kr_id=eq.{kr_id}"
            f"&select=id,title,execution_plan,bottleneck,fundamento,status,rejection_reason,cycle_id,created_at"
            f"&order=created_at.desc&limit=60") or []
    except Exception:
        return {}

    # Separar por estado
    completed   = [i for i in past if i["status"] in ("completed", "suggested_completed")]
    in_progress = [i for i in past if i["status"] == "approved"]
    rejected    = [i for i in past if i["status"] in ("rejected", "archived")]

    # Obtener tasks del ciclo más reciente (in_progress o completadas)
    recent_ids = [i["id"] for i in (in_progress + completed)[:6]]
    tasks_by_init = {}
    if recent_ids:
        try:
            ids_filter = ",".join(str(i) for i in recent_ids)
            tasks = sb_request("GET",
                f"tasks?initiative_id=in.({ids_filter})"
                f"&select=initiative_id,title,status&limit=60") or []
            for t in tasks:
                tasks_by_init.setdefault(t["initiative_id"], []).append(t)
        except Exception:
            pass

    return {
        "completed":   completed,
        "in_progress": in_progress,
        "rejected":    rejected,
        "tasks_by_init": tasks_by_init,
    }


def _fmt_init(i, tasks_by_init=None):
    """Formatea una iniciativa con su plan, bottleneck y tasks para el prompt."""
    lines = [f"  • [{i['status']}] {i['title']}"]
    if i.get("bottleneck"):
        lines.append(f"    Bottleneck: {i['bottleneck']}")
    if i.get("execution_plan"):
        lines.append(f"    Plan: {i['execution_plan'][:200]}")
    if i.get("rejection_reason"):
        lines.append(f"    Motivo rechazo: {i['rejection_reason']}")
    if tasks_by_init and i["id"] in tasks_by_init:
        tt = tasks_by_init[i["id"]]
        done = sum(1 for t in tt if t["status"] == "completed")
        lines.append(f"    Tasks: {done}/{len(tt)} completadas")
    return "\n".join(lines)


def generate_initiatives(kr, team_name, ctx=None):
    ctx = ctx or {}
    completed   = ctx.get("completed", [])
    in_progress = ctx.get("in_progress", [])
    rejected    = ctx.get("rejected", [])
    tasks_by_init = ctx.get("tasks_by_init", {})

    # Calcular progreso real
    current  = kr.get("current")
    target   = kr.get("target")
    baseline = kr.get("baseline")
    try:
        gap_pct = round((current - baseline) / (target - baseline) * 100) if target != baseline else 0
    except Exception:
        gap_pct = "—"

    completed_block   = "\n".join(_fmt_init(i) for i in completed[:10]) or "  (ninguna aún)"
    in_progress_block = "\n".join(_fmt_init(i, tasks_by_init) for i in in_progress[:5]) or "  (ninguna en curso)"
    rejected_block    = "\n".join(_fmt_init(i) for i in rejected[:8]) or "  (ninguna)"

    prompt = f"""Sos un coach senior de OKRs para el equipo de {team_name} en Etendo (ERP Agentic para pymes España).

═══ KR A MOVER ESTA SEMANA ═══
- Nombre: {kr['name']}
- Baseline: {baseline} → Actual: {current} → Target: {target}
- Progreso: {gap_pct}% del camino recorrido
- Objective: {kr.get('objective') or '—'}

═══ INICIATIVAS EN CURSO (aprobadas, no completadas) ═══
Tené en cuenta estas para NO duplicarlas y para que las nuevas las complementen:
{in_progress_block}

═══ HISTORIAL — QUÉ FUNCIONÓ ═══
Construí sobre lo que ya avanzó. No repetir estas:
{completed_block}

═══ HISTORIAL — QUÉ NO FUNCIONÓ ═══
NO volver a proponer estas ni variantes obvias:
{rejected_block}

═══ TU TAREA ═══
Generá EXACTAMENTE 3 iniciativas NUEVAS esta semana que:
1. Complementen (no dupliquen) las iniciativas en curso
2. Ataquen bottlenecks distintos a los ya intentados
3. Sean accionables en 5 días laborables
4. Tengan impacto directo y medible en el KR

Para cada iniciativa, devolvé JSON con:
- "title": acción concreta ≤80 chars, verbo infinitivo
- "execution_plan": 3-5 pasos concretos separados por '; '
- "bottleneck_identificado": cuello de botella específico que atacás
- "fundamento": POR QUÉ esto mueve el número — dato, benchmark, framework o evidencia concreta

Devolvé SOLO un JSON array sin texto antes/después:
[{{"title":"...","execution_plan":"...","bottleneck_identificado":"...","fundamento":"..."}}]"""

    return json.loads(llm_call(prompt, max_tokens=2000))


def run_team(cfg, dry_run, next_week=False):
    team_id   = cfg["team"]["id"]
    team_name = cfg["team"]["name"]
    team_slug = team_name.lower().replace(" ", "_")
    period    = cfg["period"]["name"]
    role_id   = cfg["etendo"]["role_id"]

    print(f"\n=== TEAM: {team_name} ({team_id}) — {period} ===")

    jwt = etendo_login(role_id)
    krs = fetch_team_krs(jwt, period, team_id)
    print(f"KRs encontrados: {len(krs)}")
    if not krs:
        print("Sin KRs — skip.")
        return

    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    if next_week:
        monday = monday + timedelta(weeks=1)
    sunday = monday + timedelta(days=6)

    if dry_run:
        print(f"[dry-run] Crearía cycle {team_name} {monday}..{sunday}")
        for kr in krs:
            ctx = fetch_historical_context(kr["id"])
            print(f"\n  KR: {kr['name']} (current={kr.get('current')})")
            print(f"     en_curso={len(ctx.get('in_progress',[]))} completadas={len(ctx.get('completed',[]))} rechazadas={len(ctx.get('rejected',[]))}")
            try:
                inits = generate_initiatives(kr, team_name, ctx)
                for i, it in enumerate(inits, 1):
                    print(f"    {i}. {it.get('title','')}")
                    print(f"       bottleneck: {it.get('bottleneck_identificado','—')}")
            except Exception as e:
                print(f"    ✗ Error LLM: {e}")
        return

    # Verificar si ya existe algún ciclo para esta semana (cualquier estado)
    existing = sb_request("GET",
        f"cycles?team=eq.{team_slug}&week_start=eq.{monday}&select=id,status&limit=1")
    if existing:
        existing_status = existing[0]["status"]
        cycle_id = existing[0]["id"]
        if existing_status == "pending_initiative_approval":
            print(f"Cycle existente reusado: id={cycle_id}")
            sb_request("PATCH", f"cycles?id=eq.{cycle_id}", {"kr_snapshot": krs})
        else:
            print(f"Ciclo para {monday} ya existe con status={existing_status} — no se regeneran iniciativas.")
            return
    else:
        cycle = sb_request("POST", "cycles", {
            "team":        team_slug,
            "week_start":  str(monday),
            "week_end":    str(sunday),
            "status":      "pending_initiative_approval",
            "kr_snapshot": krs,
        })[0]
        cycle_id = cycle["id"]
        print(f"Cycle creado: id={cycle_id}")

    rows = []
    for kr in krs:
        ctx = fetch_historical_context(kr["id"])
        try:
            inits = generate_initiatives(kr, team_name, ctx)
        except Exception as e:
            print(f"  ✗ Error LLM en KR {kr['name']}: {e}")
            continue
        for it in inits:
            rows.append({
                "cycle_id":       cycle_id,
                "kr_id":          kr["id"],
                "kr_name":        kr["name"],
                "title":          (it.get("title") or "").strip()[:240],
                "execution_plan": it.get("execution_plan") or "",
                "bottleneck":     it.get("bottleneck_identificado") or "",
                "fundamento":     it.get("fundamento") or "",
                "status":         "proposed",
                "created_by":     "agent",
            })
    if not rows:
        print("  No se generaron iniciativas.")
        return

    sb_request("POST", "initiatives", rows)
    print(f"  Insertadas {len(rows)} iniciativas en Supabase.")

    try:
        html = f"""
        <h2>OKR Manager — Lunes {monday.strftime('%d %b %Y')}</h2>
        <p>El agente generó <strong>{len(rows)} iniciativas</strong> para {len(krs)} KRs del team
        <strong>{team_name}</strong>.</p>
        <p><a href="{SITE_URL}/approval-monday.html"
              style="display:inline-block;padding:12px 24px;background:#FFD700;color:#1a1a2e;
                     text-decoration:none;border-radius:8px;font-weight:700;">
          Revisar y aprobar el batch
        </a></p>
        """
        send_email(APPROVER_EMAIL, f"OKR {team_name} — {len(rows)} iniciativas para aprobar", html)
        print(f"  Email enviado a {APPROVER_EMAIL}")
    except Exception as e:
        print(f"  ✗ Error mandando mail: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", help="Slug del archivo .json en reports/okr_coach_configs/ (ej. marketing)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--next-week", action="store_true", help="Generar ciclo para la semana siguiente en lugar de la actual")
    args = ap.parse_args()

    configs = all_team_configs()
    if not configs:
        print("No hay configs en reports/okr_coach_configs/.")
        sys.exit(1)
    if args.team:
        configs = [c for c in configs if c["team"]["name"].lower().startswith(args.team.lower())]
        if not configs:
            print(f"No encontré team que empiece con '{args.team}'")
            sys.exit(1)

    for cfg in configs:
        run_team(cfg, args.dry_run, next_week=args.next_week)
    print("\nDone.")


if __name__ == "__main__":
    main()
