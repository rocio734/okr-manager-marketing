#!/usr/bin/env python3
"""
Generate backlog — busca initiatives status=approved sin tasks y genera 3-5 tasks
por LLM que sirven para medir avance del KR.
"""
import json
from _etendo import sb_request, llm_call


def llm_tasks(initiative):
    prompt = f"""Sos un product manager generando un backlog operativo.

Iniciativa aprobada:
- Título: {initiative['title']}
- KR que mueve: {initiative['kr_name']}
- Plan de ejecución: {initiative.get('execution_plan') or '—'}
- Bottleneck que ataca: {initiative.get('bottleneck') or '—'}

Generá entre 3 y 5 tasks concretas que sirvan para MEDIR el avance.
Cada task debe ser:
- Verificable (alguien la marca como done con evidencia)
- Específica (no "investigar X", sino "publicar X" / "enviar Y" / "configurar Z")
- Atómica (1-3 días de trabajo)

JSON array, cada elemento con:
- "title": string corto
- "description": 1 línea con criterio de done

Devolvé SOLO el JSON array."""
    return json.loads(llm_call(prompt, max_tokens=800))


def main():
    inits = sb_request("GET", "initiatives?status=eq.approved&select=*")
    if not inits:
        print("No hay iniciativas approved.")
        return

    for init in inits:
        existing = sb_request("GET", f"tasks?initiative_id=eq.{init['id']}&select=id&limit=1")
        if existing:
            continue
        print(f"Generando backlog: {init['title']}")
        try:
            tasks = llm_tasks(init)
        except Exception as e:
            print(f"  ✗ LLM error: {e}")
            continue
        rows = [{
            "initiative_id": init["id"],
            "title":         (t.get("title") or "").strip()[:240],
            "description":   t.get("description") or "",
            "status":        "pending",
        } for t in tasks if t.get("title")]
        if rows:
            sb_request("POST", "tasks", rows)
            print(f"  + {len(rows)} tasks insertadas.")


if __name__ == "__main__":
    main()
