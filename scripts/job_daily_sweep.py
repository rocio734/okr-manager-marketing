#!/usr/bin/env python3
"""
Daily sweep — para cada iniciativa approved con avances o tasks done en las
últimas 26h, pregunta al LLM si está completa. Si sí, marca status=suggested_completed.
"""
import json
from datetime import datetime, timedelta, timezone
from _etendo import sb_request, llm_call


def llm_is_completed(init, tasks, entries):
    tasks_done = [t for t in tasks if t["status"] == "done"]
    tasks_pend = [t for t in tasks if t["status"] != "done"]
    entries_text = "\n".join(f"- {e['author_name']}: {e['body'][:200]}" for e in entries)
    prompt = f"""Sos un PM evaluando si una iniciativa está terminada.

INICIATIVA:
- Título: {init['title']}
- Plan: {init.get('execution_plan') or '—'}
- Bottleneck: {init.get('bottleneck') or '—'}

TASKS DEL BACKLOG:
- Done ({len(tasks_done)}): {[t['title'] for t in tasks_done]}
- Pendientes ({len(tasks_pend)}): {[t['title'] for t in tasks_pend]}

AVANCES CARGADOS POR EL EQUIPO ({len(entries)}):
{entries_text or '(ninguno)'}

¿La iniciativa está efectivamente terminada según esta evidencia?

Respondé SOLO con un JSON:
{{"completed": true|false, "reason": "1 línea explicando"}}"""
    return json.loads(llm_call(prompt, max_tokens=200))


def main():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=26)).isoformat()
    inits = sb_request("GET", "initiatives?status=eq.approved&select=*")
    if not inits:
        print("Nada que evaluar.")
        return
    for init in inits:
        tasks   = sb_request("GET", f"tasks?initiative_id=eq.{init['id']}&select=*") or []
        entries = sb_request("GET", f"progress_entries?initiative_id=eq.{init['id']}&select=*&order=created_at.desc&limit=20") or []
        recent_entry = any(e["created_at"] > cutoff for e in entries)
        recent_task  = any((t.get("completed_at") or "") > cutoff for t in tasks)
        if not (recent_entry or recent_task):
            continue
        try:
            verdict = llm_is_completed(init, tasks, entries)
        except Exception as e:
            print(f"  ✗ LLM error en init {init['id']}: {e}")
            continue
        if verdict.get("completed"):
            sb_request("PATCH", f"initiatives?id=eq.{init['id']}", {"status": "suggested_completed"})
            print(f"  ✓ Sugerida completa: {init['title']} — {verdict.get('reason','')}")


if __name__ == "__main__":
    main()
