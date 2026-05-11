#!/usr/bin/env python3
"""
Job Visión del Mercado — OKR Manager.
Analiza competencia en Google (SerpAPI), estado del mercado ERP en España,
y genera acciones recomendadas. Guarda resultado en Supabase (tabla market_intel).

Uso:
  python3 job_market_intel.py
  python3 job_market_intel.py --team marketing
"""
import argparse, json, os, sys, urllib.request, urllib.parse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _etendo import llm_call, sb_request

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

COMPETITORS = [
    {"name": "Odoo",     "domain": "odoo.com"},
    {"name": "Holded",   "domain": "holded.com"},
    {"name": "Sage",     "domain": "sage.com"},
    {"name": "A3ERP",    "domain": "a3software.com"},
    {"name": "SAP B1",   "domain": "sap.com"},
]

QUERIES = [
    ("erp pymes españa",          "es", "Demanda ES"),
    ("mejor erp empresa mediana", "es", "Demanda ES"),
    ("erp verifactu 2026",        "es", "Verifactu"),
    ("erp con inteligencia artificial", "es", "AI ERP"),
    ("odoo vs alternativas erp",  "es", "Competencia directa"),
    ("etendo erp",                "es", "Brand"),
]


def serp_search(query, country="es"):
    if not SERPAPI_KEY:
        return {}
    params = urllib.parse.urlencode({
        "q": query, "hl": "es", "gl": country,
        "num": 10, "api_key": SERPAPI_KEY
    })
    req = urllib.request.Request(f"https://serpapi.com/search.json?{params}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  [SERP] Error: {e}")
        return {}


def analyze_serp(data, query):
    organic = data.get("organic_results", [])
    ads     = data.get("ads", [])
    result  = {
        "query": query,
        "etendo_position": None,
        "etendo_in_ads": False,
        "competitors": [],
        "ads_count": len(ads),
        "top5": []
    }
    for i, r in enumerate(organic[:5], 1):
        link  = r.get("link", "").lower()
        title = r.get("title", "")
        result["top5"].append({"pos": i, "title": title[:80], "link": link})
        if "etendo" in link:
            result["etendo_position"] = i
        for c in COMPETITORS:
            if c["domain"] in link and c["name"] not in result["competitors"]:
                result["competitors"].append(c["name"])
    for ad in ads:
        if "etendo" in ad.get("link", "").lower():
            result["etendo_in_ads"] = True
    return result


def run(team="marketing"):
    print(f"=== Visión del Mercado — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    # 1) Análisis SERP por query
    serp_results = []
    for query, country, label in QUERIES:
        print(f"  SERP: {query!r} ({country})...")
        data    = serp_search(query, country)
        analysis = analyze_serp(data, query)
        analysis["label"] = label
        serp_results.append(analysis)

    # 2) Construir contexto para LLM
    serp_summary = "\n".join([
        f"- [{r['label']}] '{r['query']}': Etendo pos={r['etendo_position'] or 'fuera top10'}, "
        f"competidores={r['competitors'] or 'ninguno en top10'}, "
        f"top5: {[x['title'][:50] for x in r['top5']]}"
        for r in serp_results
    ])

    prompt = f"""Eres analista de inteligencia competitiva para Etendo, un ERP Agentic para pymes españolas.

DATOS DEL MERCADO HOY ({datetime.now().strftime('%d/%m/%Y')}):
{serp_summary}

COMPETIDORES PRINCIPALES: Odoo (open source, market leader), Holded (cloud ES), Sage (legacy), A3ERP (contabilidad ES), SAP B1 (enterprise).

POSICIONAMIENTO DE ETENDO: "Agentic ERP" — el único ERP que ejecuta acciones con agentes IA. Foco: pymes España, verifactu, automatización de procesos.

Con estos datos, genera un análisis estructurado en JSON con esta estructura exacta:
{{
  "resumen_ejecutivo": "2-3 frases sobre el estado del mercado hoy",
  "posicion_etendo": {{
    "fortalezas": ["...", "..."],
    "brechas": ["...", "..."]
  }},
  "competidores": [
    {{
      "nombre": "...",
      "amenaza": "alta|media|baja",
      "movimiento_detectado": "...",
      "diferenciador_vs_etendo": "..."
    }}
  ],
  "oportunidades": [
    {{
      "descripcion": "...",
      "canal": "SEO|Paid|LinkedIn|Contenido|Outbound",
      "impacto": "alto|medio|bajo",
      "accion_concreta": "..."
    }}
  ],
  "acciones_semana": [
    {{
      "accion": "...",
      "responsable": "Marketing|Comercial|Producto",
      "urgencia": "hoy|esta_semana|este_mes"
    }}
  ]
}}

Responde SOLO con el JSON, sin markdown ni texto adicional."""

    print("  Analizando con LLM...")
    raw = llm_call(prompt, max_tokens=2000)

    try:
        analysis = json.loads(raw)
    except Exception:
        # Intentar extraer JSON si hay texto extra
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        analysis = json.loads(m.group()) if m else {"error": raw[:500]}

    # 3) Guardar en Supabase
    payload = {
        "team":       team,
        "generated_at": datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
        "serp_data":  serp_results,
        "analysis":   analysis,
    }
    try:
        result = sb_request("POST", "market_intel", payload)
        print(f"  ✓ Guardado en Supabase (id={result[0].get('id') if result else '?'})")
    except Exception as e:
        print(f"  ✗ Error Supabase: {e}")
        # Guardar localmente como fallback
        out = Path("/tmp/market_intel_latest.json")
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"  → Guardado en {out}")

    print("\nAnálisis:")
    print(json.dumps(analysis, indent=2, ensure_ascii=False))
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--team", default="marketing")
    args = parser.parse_args()
    run(args.team)
