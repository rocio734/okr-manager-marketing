#!/usr/bin/env python3
"""
job_content.py — Generador de contenido semanal con persona "Consultor Gartner"

Genera 4-6 posts/semana sectorizado para LinkedIn e Instagram.
Cada post referencia investigación real de Gartner, Forrester o TEDx
y la conecta con los desafíos del sector target.

Uso:
  python3 job_content.py                    # semana actual
  python3 job_content.py --dry-run          # sin guardar en Supabase
  python3 job_content.py --sector retail    # solo un sector
  python3 job_content.py --week 2026-06-30  # semana específica (lunes)
"""

import os, sys, json, urllib.request, urllib.parse, argparse, re
import xml.etree.ElementTree as ET
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from _etendo import sb_request

# ── Config ────────────────────────────────────────────────────────────────────

def _env(key):
    for path in [os.path.join(os.path.dirname(__file__), '..', '..', '.env'),
                 os.path.join(os.path.dirname(__file__), '..', '..', '.env.google')]:
        if os.path.exists(path):
            for line in open(path).read().splitlines():
                if line.startswith(key + '='):
                    return line.split('=', 1)[1].strip().strip('"')
    return os.environ.get(key, '')

OPENAI_KEY  = _env('OPENAI_API_KEY')
SERPAPI_KEY = _env('SERPAPI_KEY').replace('}', '')  # corregir typo en .env

SECTORS = ['manufacturing', 'distribution', 'retail', 'services']

SECTOR_LABELS = {
    'manufacturing': 'Manufactura / Industrial',
    'distribution':  'Distribución / Logística',
    'retail':        'Retail / Comercio',
    'services':      'Servicios Profesionales',
}

# Guía visual por sector para generar image_prompts coherentes con Etendo
IMAGE_CONTEXT = {
    'manufacturing': (
        "A mid-sized Spanish manufacturing plant (50-100 workers). "
        "Show a production floor with CNC machines or assembly lines, "
        "a floor manager reviewing real-time production data on a tablet or wall-mounted screen, "
        "inventory shelves with barcodes visible in the background. "
        "The digital element (dashboard, tablet UI, data overlay) should be subtle but present. "
        "Natural industrial light, clean and organized facility — not a megafactory. "
        "Spanish/Southern European aesthetic: warm tones, real workers."
    ),
    'distribution': (
        "A medium-scale Spanish distribution warehouse. "
        "Show forklift operators, organized pallets with SKU labels, "
        "a logistics coordinator with a tablet checking multi-depot stock levels. "
        "Optional: a delivery van being loaded, a route planning screen on a laptop. "
        "Emphasize visibility and control — the story is 'I know where everything is.' "
        "Bright warehouse lighting, practical and operational feel."
    ),
    'retail': (
        "A modern Spanish retail store blending physical and digital commerce. "
        "Show a store manager at a POS terminal or tablet checking unified inventory, "
        "product shelves in background, possibly a laptop showing an e-commerce dashboard. "
        "The feel: a real SME retailer (not a superchain) who has digitalized operations. "
        "Clean, well-lit store environment, warm Mediterranean light."
    ),
    'services': (
        "A modern professional services office in Spain (consultancy, agency, or B2B firm). "
        "Show 2-3 professionals collaborating around a screen showing project dashboards, "
        "billing or resource allocation data visible on a monitor. "
        "Clean open-plan office, natural light, contemporary furniture. "
        "The digital element: a clear ERP-style dashboard with charts and task lists. "
        "Professional but human — real people solving real business problems."
    ),
}

SECTOR_CONTEXT = {
    'manufacturing': (
        "empresas industriales y de fabricación en España. "
        "Sus dolores: trazabilidad de producción, gestión de inventario en tiempo real, "
        "integración de planta con back-office, cumplimiento normativo (ISO, Verifactu). "
        "Gartner angle: Manufacturing Operations Management, Industry 4.0, smart factory."
    ),
    'distribution': (
        "empresas de distribución y logística en España. "
        "Sus dolores: visibilidad de cadena de suministro, optimización de rutas, "
        "gestión de almacenes multi-depósito, control de costes de envío. "
        "Gartner angle: Supply Chain Technology, Last-Mile Delivery, Warehouse Management."
    ),
    'retail': (
        "empresas de retail y comercio en España (tiendas físicas + e-commerce). "
        "Sus dolores: gestión omnicanal de inventario, previsión de demanda, "
        "integración con marketplaces, cumplimiento fiscal (Verifactu, SII). "
        "Gartner angle: Retail Technology, Unified Commerce, Demand Planning."
    ),
    'services': (
        "empresas de servicios profesionales en España (consultoría, agencias, servicios B2B). "
        "Sus dolores: rentabilidad de proyectos, control de horas y recursos, "
        "facturación recurrente, automatización de procesos internos. "
        "Gartner angle: Professional Services Automation, AI-augmented ERP, Hyperautomation."
    ),
}

# Calendario semanal: qué tipo de post va cada día
WEEKLY_SLOTS = [
    {'day': 'monday',    'platform': 'linkedin',  'format': 'carousel', 'source': 'gartner',  'sector_index': 0},
    {'day': 'tuesday',   'platform': 'linkedin',  'format': 'post',     'source': 'industry', 'sector_index': 1},
    {'day': 'wednesday', 'platform': 'instagram', 'format': 'carousel', 'source': 'gartner',  'sector_index': 2},
    {'day': 'thursday',  'platform': 'linkedin',  'format': 'post',     'source': 'tedx',     'sector_index': 3},
    {'day': 'friday',    'platform': 'linkedin',  'format': 'post',     'source': 'gartner',  'sector_index': 0},
    {'day': 'friday',    'platform': 'instagram', 'format': 'post',     'source': 'industry', 'sector_index': 1},
]


# ── SerpAPI — buscar contenido fresco ─────────────────────────────────────────

# Keywords por sector para filtrar RSS
# Para Gartner: keywords específicas del sector (artículos técnicos)
GARTNER_KEYWORDS = {
    'manufacturing': ['manufactur', 'factory', 'industry 4', 'industrial', 'production',
                      'automation', 'smart factory', 'ERP', 'supply chain', 'IoT'],
    'distribution':  ['supply chain', 'logistics', 'warehouse', 'distribution', 'last-mile',
                      'delivery', 'inventory', 'fulfillment', 'transport'],
    'retail':        ['retail', 'commerce', 'omnichannel', 'consumer', 'e-commerce', 'shopping',
                      'store', 'marketplace', 'demand'],
    'services':      ['professional services', 'automation', 'future of work', 'productivity',
                      'AI', 'ERP', 'consulting', 'project', 'billing', 'hyperautomation'],
}

# Para TED: keywords amplias — las charlas TED son de ideas, el LLM las conecta al sector
TED_KEYWORDS = ['technology', 'future', 'innovation', 'business', 'work', 'digital',
                'AI', 'data', 'transform', 'automat', 'leadership', 'economy',
                'robots', 'machine', 'entrepren', 'produc', 'supply', 'sustain']


def _serp(query, n=5):
    if not SERPAPI_KEY:
        return []
    url = (f"https://serpapi.com/search.json?q={urllib.parse.quote(query)}"
           f"&hl=es&gl=es&num={n}&api_key={SERPAPI_KEY}")
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read())
            results = []
            for item in data.get('organic_results', [])[:n]:
                results.append({
                    'title':   item.get('title', ''),
                    'snippet': item.get('snippet', ''),
                    'url':     item.get('link', ''),
                })
            return results
    except Exception as e:
        print(f"  [SERP] {e}")
        return []


class _Follow308(urllib.request.HTTPErrorProcessor):
    """Extiende urllib para seguir redirects 308 (Permanent Redirect)."""
    def http_response(self, request, response):
        if response.status == 308:
            location = response.headers.get('Location')
            if location:
                request = urllib.request.Request(location, headers=dict(request.headers))
                return self.parent.open(request)
        return super().http_response(request, response)
    https_response = http_response

_OPENER = urllib.request.build_opener(_Follow308())


def _fetch_rss(feed_url, sector=None, keywords=None, n=4):
    """Parsea un feed RSS y devuelve items filtrados por keywords.
    Si se pasa sector, usa GARTNER_KEYWORDS. Si se pasa keywords, las usa directamente."""
    try:
        req = urllib.request.Request(feed_url)
        req.add_header('User-Agent', 'Mozilla/5.0 (compatible; research-bot/1.0)')
        with _OPENER.open(req, timeout=15) as r:
            root = ET.fromstring(r.read())
    except Exception as e:
        print(f"  [RSS] {feed_url}: {e}")
        return []

    if keywords is None:
        keywords = GARTNER_KEYWORDS.get(sector, [])
    results = []

    # RSS 2.0: .//item  |  Atom: .//entry
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    items = root.findall('.//item') or root.findall('.//atom:entry', ns)

    for item in items:
        title   = (item.findtext('title') or
                   item.findtext('atom:title', '', ns)).strip()
        desc    = (item.findtext('description') or
                   item.findtext('atom:summary', '', ns) or
                   item.findtext('atom:content', '', ns)).strip()
        link    = (item.findtext('link') or
                   (item.find('atom:link', ns) or ET.Element('')).get('href', '')).strip()

        # Limpiar HTML del snippet
        desc = re.sub(r'<[^>]+>', ' ', desc)
        desc = re.sub(r'\s+', ' ', desc).strip()[:600]

        # Filtrar por relevancia al sector
        text = (title + ' ' + desc).lower()
        if keywords and not any(k.lower() in text for k in keywords):
            continue

        results.append({'title': title, 'snippet': desc, 'url': link})
        if len(results) >= n:
            break

    return results


def _fetch_article_text(url, max_chars=800):
    """Descarga una página y extrae texto limpio (para enriquecer snippets de Gartner)."""
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (compatible; research-bot/1.0)')
        with _OPENER.open(req, timeout=12) as r:
            html = r.read().decode('utf-8', errors='ignore')
        # Quitar scripts, styles y etiquetas
        html = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        # Buscar párrafo con sustancia (>100 chars seguidos sin basura)
        chunks = [c.strip() for c in text.split('.') if len(c.strip()) > 80]
        return '. '.join(chunks[:6])[:max_chars]
    except Exception:
        return ''


def fetch_research(sector, source_type):
    """Obtiene investigación reciente según la fuente: RSS directo o SerpAPI."""

    # ── TED: RSS oficial — keywords amplias, el LLM conecta al sector ─────────
    if source_type == 'tedx':
        results = _fetch_rss('https://feeds.feedburner.com/TEDTalks_video', sector=None,
                             keywords=TED_KEYWORDS, n=4)
        if results:
            print(f"  [RSS/TED] {len(results)} charlas encontradas para {sector}")
            return results
        # Fallback a SerpAPI si el RSS no devuelve resultados relevantes
        print(f"  [RSS/TED] Sin resultados relevantes, usando SerpAPI...")
        ted_queries = {
            'manufacturing': 'TEDx talk manufacturing automation Industry 4.0 future',
            'distribution':  'TEDx talk supply chain logistics resilience future',
            'retail':        'TEDx talk retail omnichannel consumer future commerce',
            'services':      'TEDx talk future of work AI automation professional services',
        }
        return _serp(ted_queries.get(sector, f'TED talk {sector} future'), n=4)

    # ── Gartner: RSS newsroom + fetch de artículo para más contexto ───────────
    if source_type == 'gartner':
        gartner_queries = {
            'manufacturing': 'site:gartner.com manufacturing ERP "2025" OR "2026"',
            'distribution':  'site:gartner.com supply chain logistics "2025" OR "2026"',
            'retail':        'site:gartner.com retail commerce "2025" OR "2026"',
            'services':      'site:gartner.com "professional services" OR ERP automation "2025" OR "2026"',
        }
        serp_results = _serp(gartner_queries.get(sector, f'site:gartner.com {sector} 2026'), n=3)

        # Para el primer resultado, intentamos obtener el texto completo del artículo
        enriched = []
        for r in serp_results:
            if r.get('url') and 'gartner.com' in r['url']:
                full_text = _fetch_article_text(r['url'])
                if full_text and len(full_text) > len(r['snippet']):
                    r['snippet'] = full_text
            enriched.append(r)

        if enriched:
            print(f"  [Gartner] {len(enriched)} artículos obtenidos para {sector}")
            return enriched

        # Fallback genérico si site: no devuelve nada
        fallback_queries = {
            'manufacturing': 'Gartner ERP manufacturing Industry 4.0 trends 2025 2026',
            'distribution':  'Gartner supply chain technology logistics trends 2025 2026',
            'retail':        'Gartner retail technology unified commerce trends 2025 2026',
            'services':      'Gartner professional services automation ERP AI 2025 2026',
        }
        return _serp(fallback_queries.get(sector, f'Gartner {sector} trends 2026'), n=4)

    # ── Industry: SerpAPI con estadísticas del mercado español ────────────────
    industry_queries = {
        'manufacturing': 'estadisticas manufactura digital España transformación 2025',
        'distribution':  'estadisticas logística cadena suministro España 2025',
        'retail':        'estadisticas ecommerce retail España omnichannel 2025',
        'services':      'estadisticas servicios profesionales automatización España 2025',
    }
    return _serp(industry_queries.get(sector, f'ERP {sector} España 2025'), n=4)


# ── Anthropic — generar contenido ─────────────────────────────────────────────

def _claude(prompt, max_tokens=2000):
    body = json.dumps({
        "model": "gpt-4o",
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": PERSONA},
            {"role": "user",   "content": prompt},
        ],
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body, method="POST"
    )
    req.add_header("Authorization", f"Bearer {OPENAI_KEY}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
        return data['choices'][0]['message']['content']


PERSONA = """Eres un consultor senior de tecnología empresarial con 15 años de experiencia.
Has colaborado con analistas de Gartner y Forrester. Ahora trabajas con Etendo,
un ERP composable español diseñado para PYMEs.

Tu voz en redes sociales:
- Citás investigación real de Gartner, Forrester, IDC o McKinsey (datos y tendencias)
- Interpretás esas tendencias para el contexto español y de las PYMEs
- Conectás el insight con un problema real del sector target
- Al final, conectás sutilmente con la solución que ofrece Etendo
- Tono: directo, autoridad sin arrogancia, datos primero
- NUNCA suenas a vendedor. Suenas a alguien que genuinamente quiere educar.
- Escribís en español (España), castellano neutro profesional
- Para LinkedIn: post reflexivo con gancho fuerte en primera línea
- Para Instagram: más visual, más emocional, mismo rigor pero más accesible
"""


def generate_post(slot, sector, research, week_start):
    """Genera un post individual usando Claude."""

    research_text = "\n".join(
        f"- {r['title']}: {r['snippet']} ({r['url']})"
        for r in research[:3]
    ) or "No hay resultados de búsqueda — usá tu conocimiento de Gartner 2024-2025."

    format_instructions = {
        'post': (
            "Genera un POST para redes sociales (texto plano, sin slides).\n"
            "Estructura:\n"
            "1. GANCHO (1-2 líneas potentes, dato o afirmación provocadora)\n"
            "2. CONTEXTO (2-3 párrafos cortos: qué dice Gartner/TEDx, por qué importa al sector)\n"
            "3. IMPLICACIÓN (qué significa para una empresa del sector en España)\n"
            "4. CTA suave (pregunta o reflexión que invite a comentar)\n"
            "Máximo 1300 caracteres para LinkedIn, 2200 para Instagram.\n"
            "Devolvé JSON: {\"title\": \"...\", \"body\": \"...\", \"hashtags\": [\"...\"], \"image_prompt\": \"...\"}"
        ),
        'carousel': (
            "Genera un CARRUSEL (5-7 slides).\n"
            "Slide 1: Portada con título impactante\n"
            "Slides 2-5: Cada uno con un insight o dato del sector\n"
            "Slide final: CTA + mención a Etendo\n"
            "Cada slide: título corto (5-8 palabras) + cuerpo (2-3 líneas max)\n"
            "Devolvé JSON: {\"title\": \"...\", \"body\": \"intro/caption del post\", "
            "\"slides\": [{\"title\": \"...\", \"body\": \"...\"}], \"hashtags\": [\"...\"], "
            "\"image_prompt\": \"...\"}"
        ),
    }

    platform_note = (
        "LinkedIn: tono profesional, datos y análisis, audiencia: directores y gerentes de empresa."
        if slot['platform'] == 'linkedin' else
        "Instagram: más visual y emocional, misma autoridad pero más accesible, audiencia mixta."
    )

    is_carousel = slot['format'] == 'carousel'
    image_instruction = (
        f"IMAGE PROMPT — {'imagen de portada del carrusel (slide 1)' if is_carousel else 'imagen del post'}:\n"
        f"Contexto visual base del sector: {IMAGE_CONTEXT[sector]}\n"
        f"Reglas:\n"
        f"- Adaptá ese contexto al tema específico del post\n"
        f"  Ejemplos: si el post habla de trazabilidad → empleado escaneando productos con tablet;\n"
        f"  si habla de automatización → dashboard con flujos en pantalla; "
        f"si habla de costes → directivo con reporte financiero en monitor\n"
        + (
        f"- Para carrusel: imagen de portada impactante que invite a deslizar; "
        f"si el ángulo es datos/Gartner → directivo analizando métricas en pantalla grande\n"
        if is_carousel else ""
        ) +
        f"- NUNCA texto dentro de la imagen\n"
        f"- Estilo: photorealistic, professional, warm natural light, Spanish SME context\n"
        f"- EVITAR: stock photos genéricos, gente sonriendo sin contexto, megaempresas, "
        f"estética americana corporativa\n"
        f"- El prompt debe ser en inglés, detallado (60-100 palabras), listo para DALL-E o Midjourney\n"
    )

    prompt = f"""SECTOR TARGET: {SECTOR_LABELS[sector]}
Contexto del sector: {SECTOR_CONTEXT[sector]}

PLATAFORMA: {slot['platform'].upper()}
{platform_note}

FORMATO: {slot['format'].upper()}
{format_instructions[slot['format']]}

FUENTE DE INSPIRACIÓN: {slot['source'].upper()}
Investigación encontrada:
{research_text}

{image_instruction}

INSTRUCCIONES ADICIONALES:
- Semana del {week_start}
- Si el source es 'gartner': abrí con un dato o stat de Gartner (podés citar el Hype Cycle,
  Magic Quadrant, o una encuesta de Gartner del sector)
- Si el source es 'tedx': abrí con una idea poderosa al estilo TED ("La pregunta no es si
  tu empresa va a automatizarse, sino cuándo")
- Si el source es 'industry': abrí con un dato del mercado español o europeo
- Mencioná Etendo máximo una vez, al final, como solución natural (no como anuncio)
- Incluí 3-5 hashtags relevantes en español e inglés

Devolvé SOLO el JSON solicitado, sin texto adicional.
"""

    try:
        raw = _claude(prompt, max_tokens=2000)
        # Limpiar markdown si viene con ```json
        raw = raw.strip()
        if raw.startswith('```'):
            raw = raw.split('```')[1]
            if raw.startswith('json'):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"  [LLM] Error generando post: {e}")
        return {
            'title': f"[Error] {sector} {slot['format']}",
            'body': str(e),
            'hashtags': [],
        }


# ── Main ──────────────────────────────────────────────────────────────────────

def get_week_start(week_arg=None):
    if week_arg:
        from datetime import datetime
        d = datetime.strptime(week_arg, '%Y-%m-%d').date()
        return d - timedelta(days=d.weekday())
    today = date.today()
    return today - timedelta(days=today.weekday())


def run(dry_run=False, sector_filter=None, week_arg=None):
    week_start = get_week_start(week_arg)
    print(f"Generando contenido semana {week_start} …")

    # Rotar sectores según número de semana (para no repetir siempre el mismo)
    week_num = week_start.isocalendar()[1]
    rotated = SECTORS[week_num % len(SECTORS):] + SECTORS[:week_num % len(SECTORS)]

    posts = []
    for i, slot in enumerate(WEEKLY_SLOTS):
        sector = rotated[slot['sector_index'] % len(rotated)]
        if sector_filter and sector != sector_filter:
            continue

        label = f"{slot['day']} {slot['platform']} {slot['format']} [{sector}]"
        print(f"  → {label}")

        research = fetch_research(sector, slot['source'])
        content  = generate_post(slot, sector, research, str(week_start))

        source_url = research[0]['url'] if research else None
        source_title = research[0]['title'] if research else None

        post = {
            'week_start':   str(week_start),
            'platform':     slot['platform'],
            'format':       slot['format'],
            'sector':       sector,
            'day_slot':     slot['day'],
            'source_type':  slot['source'],
            'source_title': source_title,
            'source_url':   source_url,
            'title':        content.get('title', ''),
            'body':         content.get('body', ''),
            'slides':       content.get('slides'),
            'hashtags':     content.get('hashtags', []),
            'image_prompt': content.get('image_prompt'),
            'status':       'draft',
        }
        posts.append(post)

        print(f"     ✓ \"{post['title'][:60]}\"")

    print(f"\n{len(posts)} posts generados.")

    if dry_run:
        print("\n[DRY RUN] No se guardó nada. Preview:\n")
        for p in posts:
            print(f"{'─'*60}")
            print(f"[{p['day_slot'].upper()}] {p['platform']} | {p['format']} | {p['sector']}")
            print(f"Título: {p['title']}")
            print(f"Body:   {p['body'][:200]}…" if len(p['body']) > 200 else f"Body: {p['body']}")
            if p.get('slides'):
                print(f"Slides: {len(p['slides'])} slides")
            print(f"Tags:   {' '.join(p['hashtags'])}")
    else:
        # Borrar drafts de la semana antes de insertar (idempotente)
        sb_request('DELETE', f"content_queue?week_start=eq.{week_start}&status=eq.draft")
        result = sb_request('POST', 'content_queue', posts)
        print(f"✅ {len(result)} posts guardados en content_queue (status=draft)")
        print("Revisá y aprobá en el dashboard antes de publicar.")

    return posts


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--sector', choices=SECTORS)
    parser.add_argument('--week', help='Fecha lunes de la semana (YYYY-MM-DD)')
    args = parser.parse_args()
    run(dry_run=args.dry_run, sector_filter=args.sector, week_arg=args.week)
