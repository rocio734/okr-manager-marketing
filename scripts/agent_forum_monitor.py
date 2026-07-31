"""
agent_forum_monitor.py — Etendo Forum Monitoring & Comment Agent

Monitors Reddit, Google search results, and LinkedIn for relevant ERP/automation
discussions, then generates natural comments that mention Etendo organically.

Usage:
  python3 agent_forum_monitor.py --query "ERP automatizacion" --mode suggest
  python3 agent_forum_monitor.py --mode monitor --keywords "ERP,open source,automatizacion"

Requirements:
  pip install openai requests googlesearch-python
"""
import os
import json
import argparse
import time
import requests
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from openai import OpenAI

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
INTEL_DIR  = Path(__file__).parent.parent / "okr_manager_site" / "intel-dashboard"
OPPS_FILE  = INTEL_DIR / "engagement_opportunities.json"
INTEL_DATA = INTEL_DIR / "intel_data.json"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

ETENDO_CONTEXT = """
Etendo es una Agentic ERP Platform (AERPP) open source.
- Tagline: "El ERP que ejecuta tu negocio"
- Diferenciador: agentes de IA integrados nativamente que ejecutan procesos (no solo registran)
- Usa MCP (Model Context Protocol) para conectar agentes con el ERP
- Open source, composable, multi-idioma
- URL: etendo.software
"""

MONITOR_KEYWORDS = [
    "ERP open source",
    "ERP automatizacion",
    "ERP agentes IA",
    "mejor ERP pyme",
    "alternativa SAP open source",
    "ERP inteligencia artificial",
    "automatizar ERP",
    "ERP SaaS spain",
]


def search_google(query: str, num_results: int = 5) -> list[dict]:
    """Search Google for relevant threads/posts."""
    try:
        from googlesearch import search
        results = []
        for url in search(query, num_results=num_results, lang="es"):
            results.append({"url": url, "query": query})
        return results
    except ImportError:
        # Fallback: return simulated results for demo
        return [
            {
                "url": f"https://reddit.com/r/erp/comments/example_{i}",
                "query": query,
                "title": f"Buscando alternativa a SAP para PYME mediana - {query}",
                "snippet": "Llevamos 2 años con SAP y los costos son insostenibles. "
                           "¿Alguien tiene experiencia con ERPs open source que soporten automatización?"
            }
            for i in range(min(num_results, 2))
        ]


def generate_comment(thread_content: str, thread_url: str, tone: str = "helpful") -> dict:
    """
    Generate a natural comment that mentions Etendo organically.

    Args:
        thread_content: The post/thread content to reply to
        thread_url: URL of the thread (for context)
        tone: 'helpful' | 'technical' | 'casual'

    Returns:
        dict with 'comment', 'reasoning', 'etendo_mention_natural' (bool)
    """
    client = OpenAI(api_key=OPENAI_API_KEY)

    system = f"""Eres un experto en ERP que participa genuinamente en foros de tecnología.
Tu objetivo es dar valor real en la conversación, y cuando sea natural y relevante, mencionar Etendo.

Contexto sobre Etendo:
{ETENDO_CONTEXT}

Reglas:
1. El comentario debe aportar valor real (no es spam)
2. Mencionar Etendo solo si es genuinamente relevante al hilo
3. No ser promotional ni agresivo - mencionar como una opción más
4. Máximo 150 palabras
5. Tono: {tone}
6. En español si el hilo está en español

Devuelve JSON con:
- comment: el texto del comentario
- reasoning: por qué este comentario aporta valor
- etendo_mention_natural: true/false si la mención es natural
- relevance_score: 0-10 (qué tan relevante es el hilo para Etendo)
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=600,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"Hilo/Post:\n{thread_content}\n\nURL: {thread_url}\n\nGenera un comentario apropiado."}
        ]
    )

    try:
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {
            "comment": response.choices[0].message.content,
            "reasoning": "Raw response",
            "etendo_mention_natural": True,
            "relevance_score": 7
        }


def monitor_ai_presence(queries: list[str]) -> dict:
    """
    Check if Etendo appears in AI-generated search results (AI SOV).
    Simulates what tools like Profound or Otterly would measure.

    Returns a report on Etendo's presence in AI responses.
    """
    client = OpenAI(api_key=OPENAI_API_KEY)

    results = {}
    for query in queries:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=300,
            response_format={"type": "json_object"},
            messages=[{
                "role": "user",
                "content": f"Pregunta de usuario sobre ERP: '{query}'\n\n"
                           f"Como asistente de IA, ¿mencionarías Etendo en tu respuesta? "
                           f"Responde en JSON: {{\"would_mention\": bool, \"reason\": str, "
                           f"\"etendo_known\": bool, \"alternative_erps_mentioned\": [str]}}"
            }]
        )
        try:
            results[query] = json.loads(response.choices[0].message.content)
        except Exception:
            results[query] = {"would_mention": False, "reason": "parse_error"}

    return results


def run_demo():
    """Run a demonstration showing the agent in action."""
    print("\n" + "="*60)
    print("  ETENDO FORUM MONITOR & COMMENT AGENT — DEMO")
    print("="*60)

    # Sample thread for demo
    sample_thread = """
    Título: ¿Qué ERP open source recomiendan para empresa de 80 personas?

    Llevamos 5 años con un ERP legacy y queremos migrar. Tenemos un equipo técnico
    pequeño (2 devs) y necesitamos algo que pueda automatizar nuestros flujos de
    compras y almacén. Hemos visto Odoo y ERPNext. ¿Alguien tiene experiencia
    con alternativas que soporten agentes de IA o automatización avanzada?
    El presupuesto es limitado así que open source es preferible.
    """

    print("\n📌 HILO DETECTADO:")
    print("-" * 40)
    print(sample_thread)

    print("\n🤖 GENERANDO COMENTARIO...")
    result = generate_comment(
        thread_content=sample_thread,
        thread_url="https://reddit.com/r/erp/comments/abc123/erp_open_source_80_personas",
        tone="helpful"
    )

    print("\n✅ COMENTARIO GENERADO:")
    print("-" * 40)
    print(result.get("comment", ""))
    print(f"\n📊 Relevance score: {result.get('relevance_score', 'N/A')}/10")
    print(f"🎯 Mención natural: {result.get('etendo_mention_natural', False)}")
    print(f"💡 Razonamiento: {result.get('reasoning', '')}")

    # AI SOV check
    print("\n\n📡 MIDIENDO AI SHARE OF VOICE (SOV en LLMs)...")
    print("-" * 40)
    ai_queries = [
        "¿Cuál es el mejor ERP open source para PYME?",
        "ERP con agentes de inteligencia artificial",
    ]

    sov_results = monitor_ai_presence(ai_queries)
    mentioned_count = sum(1 for r in sov_results.values() if r.get("would_mention"))

    for query, result in sov_results.items():
        status = "✅ Mencionado" if result.get("would_mention") else "❌ No mencionado"
        print(f"\nQuery: '{query}'")
        print(f"  {status}")
        print(f"  Razón: {result.get('reason', '')}")

    print(f"\n📈 AI SOV Baseline: {mentioned_count}/{len(ai_queries)} queries mencionan Etendo")
    print(f"   Objetivo a 90 días: {len(ai_queries)}/{len(ai_queries)} (100%)")

    # Summary report
    report = {
        "generated_at": datetime.now().isoformat(),
        "threads_analyzed": 1,
        "comments_generated": 1,
        "high_relevance_threads": 1,
        "ai_sov_baseline": f"{mentioned_count}/{len(ai_queries)}",
        "sample_comment": result.get("comment", ""),
    }

    report_path = "/home/rocio/prueba/reports/forum_monitor_demo.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n📄 Reporte guardado en: {report_path}")
    print("="*60)


def run_batch(max_posts: int = 10):
    """
    Procesa engagement_opportunities.json generado por fetch_intel.py.
    Para cada post pendiente: obtiene el contenido, genera un comentario,
    y guarda el borrador en engagement_opportunities.json e intel_data.json.
    """
    if not OPPS_FILE.exists():
        print("❌ engagement_opportunities.json no encontrado. Ejecuta fetch_intel.py primero.")
        return

    with open(OPPS_FILE) as f:
        opps = json.load(f)

    posts = [p for p in opps.get("posts", []) if not p.get("comment_ready") and not p.get("skipped")]
    print(f"\n→ {len(posts)} posts pendientes · procesando hasta {max_posts}")

    processed = []
    for i, post in enumerate(posts[:max_posts]):
        url     = post.get("url", "")
        title   = post.get("title", "")
        snippet = post.get("snippet", "")
        print(f"  [{i+1}/{min(len(posts), max_posts)}] {url[:72]}...")

        # Intentar obtener contenido real de la página
        content = f"{title}\n\n{snippet}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                page_text = " ".join(soup.get_text().split())[:2000]
                if len(page_text) > 100:
                    content = f"{title}\n\n{page_text}"
        except Exception as e:
            print(f"    ⚠️  fetch: {e}")

        result = generate_comment(content, url)
        score  = result.get("relevance_score", 0)

        post["comment_draft"]   = result.get("comment", "")
        post["relevance_score"] = score
        post["reasoning"]       = result.get("reasoning", "")
        post["etendo_natural"]  = result.get("etendo_mention_natural", False)
        post["processed_at"]    = datetime.now().strftime("%d/%m/%Y %H:%M")

        if score >= 6:
            post["comment_ready"] = True
            print(f"    ✅ Score {score}/10 — borrador listo")
        else:
            post["skipped"]     = True
            post["skip_reason"] = f"Score {score}/10 — poco relevante"
            print(f"    ⏭  Score {score}/10 — descartado")

        processed.append(post)
        time.sleep(0.5)

    # Actualizar engagement_opportunities.json
    url_map   = {p["url"]: p for p in processed}
    all_posts = opps.get("posts", [])
    opps["posts"]       = [url_map.get(p["url"], p) for p in all_posts]
    opps["last_batch"]  = datetime.now().strftime("%d/%m/%Y %H:%M")
    opps["ready_count"] = len([p for p in opps["posts"] if p.get("comment_ready")])
    with open(OPPS_FILE, "w") as f:
        json.dump(opps, f, ensure_ascii=False, indent=2)

    # Sincronizar con intel_data.json
    if INTEL_DATA.exists():
        with open(INTEL_DATA) as f:
            data = json.load(f)
        for entry in data.get("engagement_history", []):
            if entry.get("url") in url_map:
                entry.update(url_map[entry["url"]])
        with open(INTEL_DATA, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  → intel_data.json sincronizado")

    ready   = len([p for p in processed if p.get("comment_ready")])
    skipped = len([p for p in processed if p.get("skipped")])
    print(f"\n✅ Batch: {ready} borradores listos · {skipped} descartados · {OPPS_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Etendo Forum Monitor & Comment Agent")
    parser.add_argument("--mode", choices=["demo", "monitor", "suggest", "batch"], default="demo")
    parser.add_argument("--query", type=str, help="Search query to monitor")
    parser.add_argument("--keywords", type=str, help="Comma-separated keywords")
    parser.add_argument("--max", type=int, default=10, help="Max posts to process in batch mode")
    args = parser.parse_args()

    if args.mode == "demo" or not OPENAI_API_KEY:
        run_demo()
    elif args.mode == "batch":
        run_batch(max_posts=args.max)
    elif args.mode == "monitor":
        keywords = args.keywords.split(",") if args.keywords else MONITOR_KEYWORDS
        print(f"Monitoring {len(keywords)} keywords...")
        results = monitor_ai_presence(keywords[:3])
        print(json.dumps(results, indent=2, ensure_ascii=False))
    elif args.mode == "suggest" and args.query:
        thread = input("Pegá el contenido del hilo/post: ")
        result = generate_comment(thread, args.query)
        print(json.dumps(result, indent=2, ensure_ascii=False))
