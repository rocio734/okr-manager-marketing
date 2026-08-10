"""
Etendo Intelligence — Scraping diario completo
Fuentes de leads:
  1. Google Custom Search — señales de intención
  2. Apollo.io — base de datos B2B
  3. Google Maps API — empresas reales por sector y ciudad
  4. Scraping partners de Odoo, Holded, Sage

Enriquecimiento:
  1. Hunter.io — emails por dominio
  2. Apollo.io — contactos directos

Persistencia:
  - intel_data.json (local, hasta 300 leads)
  - Google Sheets (acumulativo, permanente)

Secrets necesarios:
  BRAVE_API_KEY, GOOGLE_MAPS_API_KEY
  GOOGLE_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_JSON
  HUNTER_API_KEY, APOLLO_API_KEY
"""
import os, re, json, hashlib, requests, datetime, time
from pathlib import Path
from bs4 import BeautifulSoup

# Scrapling — scraping adaptativo con bypass anti-bot
try:
    from scrapling.fetchers import Fetcher, StealthyFetcher
    from scrapling.spiders import Spider, Response as SpiderResponse
    # Activar modo adaptativo global — guarda huellas de elementos
    # para relocalizarlos automáticamente si el HTML cambia
    StealthyFetcher.adaptive = True
    SCRAPLING_OK = True
    print("✅ Scrapling disponible con modo adaptativo activado")
except ImportError:
    SCRAPLING_OK = False
    print("⚠️  Scrapling no disponible — usando requests como fallback")

REPO_DIR  = Path(__file__).parent
HTML_FILE = REPO_DIR / "index.html"
DATA_FILE = REPO_DIR / "intel_data.json"

# Cargar .env desde el directorio raíz del repo (dos niveles arriba de intel-dashboard/)
_ENV_FILE = REPO_DIR.parent.parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

BRAVE_API_KEY   = os.environ.get("BRAVE_API_KEY","")
GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY","")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID","")
GOOGLE_SA_JSON  = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON","")
HUNTER_KEY      = os.environ.get("HUNTER_API_KEY","")
GMAIL_USER      = os.environ.get("GMAIL_USER","")
GMAIL_PASS      = os.environ.get("GMAIL_PASSWORD_ROCIO","")
APOLLO_KEY      = os.environ.get("APOLLO_API_KEY","")
SUPABASE_URL    = os.environ.get("SUPABASE_URL","")
SUPABASE_KEY    = os.environ.get("SUPABASE_SERVICE_KEY","")

OUTREACH_SOURCE  = "intel_dashboard"
STAGE_NUEVO_LEAD = "2f7828bf-51eb-4a5e-a645-026a7e06834b"
PIPELINE_ID      = "11d2089f-a64e-4001-b8af-9210787f3fce"

TODAY = datetime.date.today().strftime("%d/%m/%Y")
NOW   = datetime.datetime.now().strftime("%d/%m/%Y %H:%M UTC")
HEADERS = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36","Accept-Language":"es-ES,es;q=0.9"}

COMPETITORS = [
    {"name":"Odoo","id":"odoo","url":"https://www.odoo.com/es","pricing_url":"https://www.odoo.com/es/pricing","blog_url":"https://www.odoo.com/es/blog","partners_url":"https://www.odoo.com/es/partners"},
    {"name":"SAP Business One","id":"sap","url":"https://www.sap.com/spain/products/erp/business-one.html","pricing_url":"https://www.sap.com/spain/products/erp/business-one.html","blog_url":"https://www.sap.com/spain/products/erp/business-one.html","partners_url":"https://www.sap.com/spain/partner-finder.html"},
    {"name":"Holded","id":"holded","url":"https://www.holded.com","pricing_url":"https://www.holded.com/es/precios","blog_url":"https://www.holded.com/es/blog","partners_url":"https://www.holded.com/es/partners"},
    {"name":"Sage","id":"sage","url":"https://www.sage.com/es-es/erp/","pricing_url":"https://www.sage.com/es-es/erp/","blog_url":"https://www.sage.com/es-es/blog/","partners_url":"https://www.sage.com/es-es/partners/find-a-partner/"},
]

_DOW   = datetime.date.today().weekday()   # 0=Lun … 6=Dom
_YEAR  = datetime.date.today().year
_CITIES  = ["Madrid","Barcelona","Valencia","Bilbao","Sevilla","Zaragoza","Málaga",
             "Alicante","Murcia","Valladolid","Pamplona","San Sebastián","Vigo","La Coruña"]
_SECTORS = ["industrial","logística","construcción","alimentación","tecnología",
             "química","textil","automoción","distribución","energía"]
_ACTIVITIES = ["fabricacion","distribucion","produccion","exportacion","almacenamiento",
                "suministro","obras","instalacion","mantenimiento industrial","servicios industriales"]
# Selección diaria con más variedad — 3 ciudades + 2 sectores + 2 actividades por día
import hashlib as _h
_DAY_SEED = int(_h.md5(str(datetime.date.today()).encode()).hexdigest(), 16)
_CITY    = _CITIES[_DAY_SEED % len(_CITIES)]
_CITY2   = _CITIES[(_DAY_SEED // 13) % len(_CITIES)]
_SECTOR  = _SECTORS[_DAY_SEED % len(_SECTORS)]
_SECTOR2 = _SECTORS[(_DAY_SEED // 7) % len(_SECTORS)]
_ACT     = _ACTIVITIES[_DAY_SEED % len(_ACTIVITIES)]
_ACT2    = _ACTIVITIES[(_DAY_SEED // 11) % len(_ACTIVITIES)]

# Pool ampliado — se elige un subconjunto aleatorio cada día para evitar
# que las mismas queries devuelvan los mismos dominios semana tras semana
_LEAD_SEARCH_POOL = [
    {"query":f'empresa {_SECTOR} {_CITY} {_ACT} web corporativa',"signal":"erp","signal_label":"Empresa ICP","signal_class":"s-erp"},
    {"query":f'empresa {_SECTOR2} {_CITY2} {_ACT2} España',"signal":"erp","signal_label":"Empresa ICP","signal_class":"s-erp"},
    {"query":f'empresa logistica distribucion {_CITY} almacen gestion',"signal":"erp","signal_label":"Empresa logística","signal_class":"s-erp"},
    {"query":f'empresa logistica {_CITY2} transporte España operador',"signal":"erp","signal_label":"Empresa logística","signal_class":"s-erp"},
    {"query":f'fabricante {_SECTOR} España {_CITY} exportacion',"signal":"erp","signal_label":"Fabricante España","signal_class":"s-erp"},
    {"query":f'empresa {_SECTOR} {_CITY} exportacion mercado europeo',"signal":"erp","signal_label":"Empresa exportadora","signal_class":"s-erp"},
    {"query":f'empresa construccion ingenieria {_CITY} proyectos obras',"signal":"erp","signal_label":"Empresa construcción","signal_class":"s-erp"},
    {"query":f'constructora {_CITY2} España proyectos residenciales industriales',"signal":"erp","signal_label":"Empresa construcción","signal_class":"s-erp"},
    {"query":f'empresa alimentaria {_CITY} produccion distribucion',"signal":"erp","signal_label":"Empresa alimentación","signal_class":"s-erp"},
    {"query":f'empresa quimica farmaceutica {_CITY} fabricacion España',"signal":"erp","signal_label":"Empresa química","signal_class":"s-erp"},
    {"query":f'empresa textil confeccion {_CITY} produccion España',"signal":"erp","signal_label":"Empresa textil","signal_class":"s-erp"},
    {"query":f'empresa automocion componentes {_CITY} proveedor tier',"signal":"erp","signal_label":"Empresa automoción","signal_class":"s-erp"},
    {"query":f'empresa distribucion mayorista {_CITY} catalogo productos',"signal":"erp","signal_label":"Distribuidor","signal_class":"s-erp"},
    {"query":f'empresa {_SECTOR} {_CITY2} contratacion publica licitacion obra',"signal":"migrate","signal_label":"Licitación","signal_class":"s-mig"},
]
# Elegir 8 queries distintas cada día usando el seed del día
import random as _rnd
_rnd.seed(_DAY_SEED)
LEAD_SEARCHES = _rnd.sample(_LEAD_SEARCH_POOL, min(8, len(_LEAD_SEARCH_POOL)))

# Búsquedas de oportunidades de engagement (foros, posts, hilos con comentarios abiertos)
ENGAGEMENT_SEARCHES = [
    {"query": f'site:reddit.com ERP España {_YEAR}',                                           "label": "Reddit ES"},
    {"query": f'site:reddit.com "ERP" OR "enterprise software" pyme automatización',           "label": "Reddit ERP"},
    {"query": f'site:reddit.com "Odoo" OR "SAP" OR "ERP" España migrar alternativa',           "label": "Reddit migración"},
    {"query": f'site:reddit.com "agentic ERP" OR "ERP IA" OR "ERP agentes"',                  "label": "Reddit Agentic"},
    {"query": f'site:quora.com ERP España pyme automatización recomendación',                   "label": "Quora ES"},
    {"query": f'site:quora.com "ERP" "open source" recommendation Spain',                      "label": "Quora EN"},
    {"query": f'site:forocoches.com ERP software empresa gestión',                             "label": "Forocoches"},
    {"query": f'site:news.ycombinator.com "ERP" OR "agentic" enterprise Spain',               "label": "HackerNews"},
    {"query": f'site:stackoverflow.com "ERP" OR "agentic ERP" open source',                   "label": "StackOverflow"},
    {"query": f'site:dev.to "ERP" OR "agentic" enterprise automation {_YEAR}',                "label": "dev.to"},
    {"query": f'"qué ERP" OR "qué erp recomendáis" OR "mejor ERP pyme" España {_YEAR} foro', "label": "Pregunta ERP"},
    {"query": f'"cambiar de ERP" OR "abandonar Odoo" OR "dejar SAP" España {_YEAR} foro',    "label": "Cambio ERP"},
]

# Solo dominios donde se puede comentar (foros y comunidades reales)
ENGAGEMENT_DOMAINS = {
    "reddit.com", "quora.com", "forocoches.com",
    "news.ycombinator.com", "ycombinator.com",
    "stackoverflow.com", "dev.to",
    "forofinanciero.com", "comunidad.ieb.es",
    "g2.com", "capterra.es", "softwareadvice.es", "getapp.es",
}

_MAPS_POOL = [
    {"query":"empresa industrial manufacturing","sector":"Industrial"},
    {"query":"empresa logistica distribucion almacen","sector":"Logística"},
    {"query":"empresa servicios profesionales B2B","sector":"Servicios"},
    {"query":"empresa construccion ingenieria civil","sector":"Construcción"},
    {"query":"empresa alimentacion bebidas fabricacion","sector":"Alimentación"},
    {"query":"empresa quimica plasticos fabricacion","sector":"Química"},
    {"query":"empresa textil confeccion moda","sector":"Textil"},
    {"query":"empresa automocion componentes proveedor","sector":"Automoción"},
    {"query":"empresa farmaceutica laboratorio","sector":"Farmacéutico"},
    {"query":"empresa electronica tecnologia hardware","sector":"Tecnología"},
    {"query":"empresa muebles madera fabricacion","sector":"Madera/Mueble"},
    {"query":"empresa metalurgica acero fabricacion","sector":"Metalurgia"},
    {"query":"empresa papel carton envases fabricacion","sector":"Envases"},
    {"query":"empresa transporte maritime aereo carga","sector":"Transporte"},
    {"query":"empresa mayorista distribuidor nacional","sector":"Distribución"},
    {"query":"empresa energias renovables solar instalacion","sector":"Energía"},
    {"query":"empresa agricultura agroindustria exportacion","sector":"Agro"},
    {"query":"empresa mantenimiento industrial servicios","sector":"Mantenimiento"},
    {"query":"empresa imprenta artes graficas packaging","sector":"Artes Gráficas"},
    {"query":"empresa instalaciones climatizacion HVAC","sector":"Instalaciones"},
]

SPAIN_CITIES = [
    "Madrid,Spain","Barcelona,Spain","Valencia,Spain","Bilbao,Spain",
    "Zaragoza,Spain","Sevilla,Spain","Málaga,Spain","Alicante,Spain",
    "Murcia,Spain","Valladolid,Spain","Pamplona,Spain","Vigo,Spain",
    "La Coruña,Spain","San Sebastián,Spain","Tarragona,Spain","Girona,Spain",
]

# Rotar 14 queries y 8 ciudades distintas cada día — más cobertura geográfica y sectorial
_rnd.seed(_DAY_SEED + 1)
MAPS_SEARCHES = _rnd.sample(_MAPS_POOL, min(14, len(_MAPS_POOL)))
_MAPS_CITIES  = _rnd.sample(SPAIN_CITIES, min(8, len(SPAIN_CITIES)))

SKIP_DOMAINS = {
    # Competidores ERP
    "odoo","sap","sage","holded","netsuite","cegid","zucchetti","abas",
    "navision","dynamics","visma","syca","solmicro","a3erp","epicor",
    # Plataformas tech gigantes (falsos positivos del partner spider)
    "linkedin","infojobs","wikipedia","youtube","google","bing","microsoft","ibm",
    "facebook","twitter","instagram","tiktok","apple","wordpress","termsfeed",
    "medium","reddit","quora","pinterest","shopify","wix","squarespace",
    "github","dropbox","slack","zoom","paypal","stripe","amazon","hubspot",
    "salesforce","oracle","adobe","netflix","spotify","airbnb","uber",
    "bebee","g2","capterra","softwareadvice","getapp","trustradius",
    # Medios de comunicación (no son prospectos)
    "elpais","expansion","cincodias","elconfidencial","xataka","eleconomista",
    "cinco","rankia","ihlservices","hpcwire","forbes","gartner","mckinsey",
    "hosteltur","hostelco","tourinews","retailactual","logisticaytransporte",
    # Portales de empleo (falsos positivos de búsquedas "consultor ERP")
    "indeed","monster","glassdoor","milanuncios","wallapop","jobtoday",
    "tecnoempleo","computrabajo","jobatus","empleo","trabajo","turijobs",
    "infoempleo","laboris","jooble","adzuna","simplyhired","talentcom",
    # Directorios, comparadores y medios tech (no son empresas prospect)
    "comparasoftware","selecthub","softwarereviews","gartner","forrester",
    "trustpilot","clutch","puntoerp","revistaerp","erpfocus","panorama",
    "erpsoftware360","pcmag","techradar","zdnet","computerworld",
    # Directorios B2B y agregadores (no son prospectos)
    "kompass","proveedores.com","metalindustria","madrid.plus","planreforma",
    "empresite","infocif","axesor","einforma","ranking-empresas","empresasdeespana",
    "paginasamarillas","europages","directoriodeempresas",
    "datoscif","iberinform","informa.es","sabi.bvdinfo","einforma",
    # Entidades gubernamentales, portales de ciudad y fundaciones
    "comunidad.madrid","junta","gencat","xunta","gva.es","larioja.org",
    "inmujeres","fundacion","fundació","pamplona.com","ayuntamiento",
    # Medios de comunicación generalistas
    "elmundo","elpais","elconfidencial","abc.es","lavanguardia","eldiario",
    "20minutos","marca.com","sport.es","as.com",
    # Gimnasios, clubes deportivos (falsos positivos por búsquedas geográficas)
    "gym","crossfit","fitness","deporte","futbol","baloncesto",
    # Software RR.HH. y reclutamiento (no son ICP — usan HR software, no ERP)
    "bizneo","factorial","kenjo","sesame","workday","bamboohr","personio",
    "catenon","norconrec","gnorcon","talentsearchpeople","clairejoster",
    "adqualis","krell-consulting","psicotec","marketingdirecto",
    # Fintech/nóminas especializadas (no son ICP de ERP)
    "checkitbancario","freematica","nominasol",
    # Agencias de marketing/SEO (no son ICP)
    "staminamarketing","seomalagaweb",
    # Portales de empleo adicionales
    "infoempleo","talentsearch","jobandtalent","cornerjob","infojobs",
    "jooble","jobijoba","jobeka","opcionempleo","expertone",
    # Implementadores y vendedores de ERP (venden ERP, no lo buscan)
    "advancesoluciones","sevillaerp","aerya","aelis","databay","dasolo",
    "melit","microtech","inforges","miga.es","tecnologiaestrategica",
    "gruptelematic","softwariza3","practicsbs","alanait","corponet",
    # Comparadores y directorios de software
    "comparasoft","comparaindustria","sortlist","blog.saleslayer","saleslayer",
    # WhatsApp y chats (falsos positivos)
    "wa.me",
    # Blogs de competidores (no son la empresa, son su blog)
    "blog.corponet","blog.saleslayer",
    # Agregadores de agencias
    "ekamat","corposuite",
    # Administraciones públicas y portales gubernamentales (no son prospectos)
    "hacienda.navarra","navarra.es","sedeelectronica","gobierto","contratacion.gob",
    "contratosdelsector","contrataciondelestado","licitaciones","boe.es","bocm.es",
    "juntaex.es","jcyl.es","aragon.es","iet.csic","csic.es","bdc.es","inap.es",
    "sede.gob","administracion.gob","mites.gob","mitma.gob","mincotur.gob",
    "mapama.gob","mefp.gob","mjusticia.gob","interior.gob","exteriores.gob",
    "congreso.es","senado.es","boe.es","agenciatributaria","aeat.es",
    "seguridad-social","sepe.es","servef.gva","inem.es","soib.es",
    # Periódicos y portales de noticias locales (falsos positivos geográficos)
    "laverdad.es","lasprovincias.es","levante-emv","elperiodico","europapress",
    "cadenaser","ondacero","cope.es","rtve.es","elespanol.com","vozpopuli",
    "lainformacion","huffingtonpost","publico.es","elplural","ctxt.es",
    "diarioinformacion","diariomallorca","diariosur","diariodesevilla",
    "diariodenavarra","noticias.nav","noticiasdenavarra","elcorreo","deia",
    # Portales de licitaciones y contratos públicos
    "perfiles.gob","perfil.contratante","perfil-contratante","plataformadecontratacion",
    "pmcm.es","rcspain.es","pmbok","licitacion.es",
    # Medios de prensa empresarial/económica (no son prospectos)
    "eleconomista","bolsamania","estrategiasdeinversion","invertia","capitalbolsa",
    "quienesquien","empresasinforma","sabi.bvd","axesor.es",
}

# Caché de URLs ya vistas para saber si usar auto_save o adaptive
_scrapling_seen_urls = set()

def fetch(url, timeout=15, dynamic=False):
    """
    Fetcher universal con Scrapling.
    - dynamic=True: StealthyFetcher con JS rendering y bypass anti-bot
    - dynamic=False: Fetcher HTTP rápido con TLS fingerprint spoofing
    - Primera vez que ve una URL: auto_save=True (guarda huella de elementos)
    - Siguientes veces: adaptive=True (relocaliza si el HTML cambió)
    - fallback: requests si Scrapling no está disponible
    """
    global _scrapling_seen_urls
    first_time = url not in _scrapling_seen_urls
    _scrapling_seen_urls.add(url)

    if SCRAPLING_OK:
        try:
            if dynamic:
                page = StealthyFetcher.fetch(
                    url, headless=True, network_idle=True,
                    timeout=timeout*1000
                )
            else:
                page = Fetcher().get(url, timeout=timeout, stealthy_headers=True)
            return page if page else None  # Devolver page object para usar .css()
        except Exception as e:
            print(f"    ⚠️ Scrapling {url[:60]}: {e}")

    # Fallback a requests — devuelve string
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"    ⚠️ {url[:60]}: {e}")
        return None

def fetch_html(url, timeout=15, dynamic=False):
    """Devuelve solo el HTML como string (compatibilidad con BeautifulSoup)."""
    result = fetch(url, timeout, dynamic)
    if result is None: return ""
    if isinstance(result, str): return result
    return result.html_content or ""

def scrapling_css(page_or_html, selector, auto_save=False, adaptive=False):
    """
    Extrae elementos con CSS selector usando Scrapling si disponible.
    auto_save=True: primera ejecución, guarda huellas
    adaptive=True: ejecuciones siguientes, relocaliza si cambió el HTML
    """""
    if SCRAPLING_OK and not isinstance(page_or_html, str):
        try:
            return page_or_html.css(selector, auto_save=auto_save, adaptive=adaptive)
        except Exception as e:
            print(f"    ⚠️ CSS selector {selector}: {e}")
    # Fallback BeautifulSoup
    soup = BeautifulSoup(page_or_html if isinstance(page_or_html, str) else page_or_html.html_content, "html.parser")
    return soup.select(selector)

def text_hash(t): return hashlib.md5(t.encode()).hexdigest()[:12]

def clean(html_or_page):
    html = html_or_page if isinstance(html_or_page, str) else (getattr(html_or_page, 'html_content', '') or '')
    soup = BeautifulSoup(html,"html.parser")
    for t in soup(["script","style","nav","footer","header"]): t.decompose()
    return " ".join(soup.get_text().split())[:5000]

PLAN_KEYWORDS = ["básico","esencial","starter","estándar","profesional","pro","business","enterprise","premium","plus","advanced","basic","standard","free","gratis"]

def extract_prices(html_or_page):
    html = html_or_page if isinstance(html_or_page, str) else (getattr(html_or_page, 'html_content', '') or '')
    soup = BeautifulSoup(html,"html.parser")
    p = {"tiers":[],"publishes":False}
    found = re.findall(r'(\d+[\.,]?\d*)\s*€\s*(?:/\s*(?:mes|month|usuario|user|usr))?', soup.get_text(), re.I)
    plan_names = []
    for tag in soup.find_all(["h1","h2","h3","h4","th","strong","b","span"],limit=50):
        t = tag.get_text(strip=True)
        if 2 < len(t) < 35 and any(k in t.lower() for k in PLAN_KEYWORDS):
            if t not in plan_names: plan_names.append(t)
    if found:
        p["publishes"] = True
        u = sorted(set(float(x.replace(",",".")) for x in found if 0 < float(x.replace(",",".")) < 50000))
        labels = plan_names[:len(u)] if plan_names else [f"Plan {i+1}" for i in range(len(u))]
        p["tiers"] = [{"label":labels[i] if i<len(labels) else f"Plan {i+1}", "price":f"€{v:.0f}/mes"} for i,v in enumerate(u[:4])]
    return p

def extract_features(html_or_page):
    html = html_or_page if isinstance(html_or_page, str) else (getattr(html_or_page, 'html_content', '') or '')
    soup = BeautifulSoup(html,"html.parser")
    keywords = ["ia","inteligencia","nuevo","nueva","lanza","mejora","feature","agent","copilot",
                "update","automatiz","versión","release","integra","módulo","precio","descuento",
                "oferta","gratis","trial","prueba","partner","certif","award","fusion","adquiere","compra"]
    out=[]
    for tag in soup.find_all(["h1","h2","h3","h4","title"],limit=20):
        t=tag.get_text(strip=True)
        if len(t)>15 and any(k in t.lower() for k in keywords):
            out.append(t[:120])
    # También extraer meta description
    meta = soup.find("meta", attrs={"name":"description"})
    if meta and meta.get("content",""):
        out.insert(0, meta["content"][:120])
    return out[:5]

def brave_comp_news(comp_name):
    queries = [
        f'"{comp_name}" España novedad OR lanzamiento OR precio OR actualización 2026',
        f'"{comp_name}" ERP nuevo módulo OR integración OR IA 2026',
    ]
    items = []
    for q in queries:
        for r in brave_search(q, num=3):
            if comp_name.lower() in r.get("title","").lower() or comp_name.lower() in r.get("snippet","").lower():
                items.append(r.get("title","")[:100])
    return list(dict.fromkeys(items))[:4]

def detect_sector(text):
    t=text.lower()
    if any(w in t for w in ["industr","manufactur","fabricac","taller","metalurg"]): return "Industrial"
    if any(w in t for w in ["logístic","distribuc","almacén","transport","cadena"]): return "Logística"
    if any(w in t for w in ["servicios","consultor","asesor","despacho","gestor"]): return "Servicios"
    if any(w in t for w in ["telecom","tecnolog","software","digital","informátic"]): return "Tech/Telecom"
    if any(w in t for w in ["retail","comercio","tienda","alimentac","bebida"]): return "Retail"
    if any(w in t for w in ["construcc","ingeniería","obra","arquitect","inmobil"]): return "Construcción"
    return "General"

def score_lead(text, signal, url="", title=""):
    # Artículos/guías/job posts se quedan en score 1 sin importar el signal
    if is_article(url, title):
        return 1
    s = 1
    if any(w in text for w in ["busca","necesita","implantación","migrar","proyecto erp","selección","licitación"]): s += 1
    if any(w in text for w in ["empresa","pyme","s.l.","s.a.","grupo","industrial","factory","ltd"]): s += 1
    if any(w in text for w in ["blog","artículo","guía","cómo elegir","qué es","comparativa"]): s -= 1
    if signal in ["migrate","partner"]: s += 1
    return max(1, min(3, s))

def domain_from_url(url):
    return re.sub(r'^https?://(www\.)?','',url).split('/')[0].split('?')[0].strip().lower()

def make_lead(company,domain,sector,signal,label,cls,src_url,snippet,score,source_type="search"):
    return {"company":company,"domain":domain,"sector":sector,"signal":signal,"signal_label":label,"signal_class":cls,"source_url":src_url,"snippet":snippet[:150],"score":score,"date":TODAY,"source_type":source_type,"email":"—","contact_name":"—","contact_pos":"—","phone":"—","linkedin_org":"—"}

# Patrones en la URL que indican artículo/guía/oferta, no una empresa
_ARTICLE_URL_PATTERNS = [
    "/blog/", "/articulo", "/guia", "/noticias/", "/news/",
    "/comparativa", "/que-es-", "/como-elegir", "/mejores-erp",
    "/empleo/", "/oferta", "/trabajo/", "/vacante", "/empleo-en",
    "/opinion/", "/post/", "/entry/",
]
# Patrones en el título que indican contenido editorial, no una empresa
_ARTICLE_TITLE_PATTERNS = [
    "¿por qué", "por qué las empresas", "guía de", "los mejores",
    "cómo elegir", "qué es", "comparativa", "se necesita",
    "empleos en", "trabaja como", "oferta de trabajo", "se busca",
    "convocatoria", "licitación pública",
    # Directorios y agregadores
    "directorio de", "mejores empresas", "empresas de ", "listado de",
    "empresas del sector", "registro de empresas", "top empresas",
    "registros de", "seleccionamos empresas",
]

def should_skip(domain):
    return not domain or any(s in domain for s in SKIP_DOMAINS)

def is_article(url, title=""):
    url_l   = url.lower()
    title_l = title.lower()
    return (
        any(p in url_l for p in _ARTICLE_URL_PATTERNS) or
        any(p in title_l for p in _ARTICLE_TITLE_PATTERNS)
    )

# ── APIs ────────────────────────────────────────────────────────────────────
def duckduckgo_search(query, num=5):
    """Fallback gratuito cuando Brave está sin créditos (402).
    Usa StealthyFetcher (headless) cuando Scrapling está disponible para bypassear bot detection."""
    from urllib.parse import unquote as _uq
    html = ""
    # Intento con StealthyFetcher (headless, bypassea bot checks de DDG)
    if SCRAPLING_OK:
        try:
            search_url = "https://html.duckduckgo.com/html/?q=" + requests.utils.quote(query) + "&kl=es-es"
            page = StealthyFetcher.fetch(search_url, headless=True, network_idle=True,
                                         timeout=20000, disable_resources=True)
            html = page.html_content if page and not isinstance(page, str) else ""
        except Exception:
            html = ""
    # Fallback: requests simple (funciona en algunos entornos de ejecución)
    if not html or "result__a" not in html:
        try:
            r = requests.get("https://html.duckduckgo.com/html/",
                params={"q": query, "kl": "es-es"},
                headers={**HEADERS, "Accept": "text/html,application/xhtml+xml"},
                timeout=15)
            if r.status_code == 200 and "result__a" in r.text:
                html = r.text
        except Exception:
            pass
    if not html:
        return []
    results = []
    for href, title in re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)', html):
        m = re.search(r'uddg=([^&"]+)', href)
        real_url = _uq(m.group(1)) if m else href
        if not real_url.startswith("http") or "duckduckgo.com/y.js" in real_url:
            continue
        results.append({"title": title.strip(), "url": real_url, "snippet": ""})
        if len(results) >= num:
            break
    return results

def brave_search(query, num=5):
    if BRAVE_API_KEY:
        try:
            r=requests.get("https://api.search.brave.com/res/v1/web/search",
                headers={"Accept":"application/json","Accept-Encoding":"gzip","X-Subscription-Token":BRAVE_API_KEY},
                params={"q":query,"count":min(num,20),"country":"es","search_lang":"es","text_decorations":0},timeout=15)
            if r.status_code == 402:
                print("    ⚠️ Brave sin créditos (402) — usando DuckDuckGo")
            elif r.status_code == 200:
                return [{"title":i.get("title",""),"url":i.get("url",""),"snippet":i.get("description","")} for i in r.json().get("web",{}).get("results",[])]
            else:
                r.raise_for_status()
        except Exception as e:
            if "402" not in str(e):
                print(f"    ⚠️ Brave Search: {e}")
    return duckduckgo_search(query, num)

def apollo_companies():
    """3 llamadas rotadas por grupo de sectores — hasta 75 empresas/run vs 15 antes."""
    if not APOLLO_KEY: return []
    import random
    _NOT_IND = ["Information Technology and Services", "Computer Software", "Internet"]
    # 3 grupos de sectores con alta demanda ERP — rotar para no repetir siempre el mismo
    _SECTOR_GROUPS = [
        ["Manufacturing", "Automotive", "Chemicals", "Paper & Forest Products"],
        ["Logistics and Supply Chain", "Wholesale", "Retail", "Transportation/Trucking/Railroad"],
        ["Construction", "Food & Beverages", "Textiles", "Plastics"],
    ]
    # El grupo del día rota según seed diario; los otros 2 también se incluyen (distintas páginas)
    base_group = _DAY_SEED % 3
    all_orgs = []
    for i in range(3):
        group_idx = (base_group + i) % 3
        page = random.randint(1 + i * 3, 4 + i * 3)  # páginas separadas para no repetir
        try:
            r = requests.post(
                "https://api.apollo.io/v1/mixed_companies/search",
                headers={"Content-Type": "application/json", "X-Api-Key": APOLLO_KEY},
                json={
                    "page": page,
                    "per_page": 25,
                    "organization_locations": ["Spain"],
                    "organization_num_employees_ranges": ["10,500"],
                    "industries": _SECTOR_GROUPS[group_idx],
                    "not_industries": _NOT_IND,
                },
                timeout=20
            )
            r.raise_for_status()
            batch = r.json().get("organizations", [])
            all_orgs.extend(batch)
            print(f"    Apollo grupo {group_idx+1}: {len(batch)} empresas (pág {page})")
            time.sleep(0.5)
        except Exception as e:
            print(f"    ⚠️ Apollo grupo {group_idx+1}: {e}")
    return all_orgs

def apollo_contact(domain):
    if not APOLLO_KEY: return {}
    # Apollo cambió auth en 2024 — intentar endpoint nuevo (/api/v1) y legacy (/v1)
    _TITLES = ["CEO","Director General","Director Operaciones","Director Financiero",
               "CTO","Gerente General","Owner","Founder","Responsable","Socio"]
    _ENDPOINTS = [
        ("https://api.apollo.io/api/v1/people/search",
         {"page":1,"per_page":1,"organization_domains":[domain],"person_titles":_TITLES}),
        ("https://api.apollo.io/v1/mixed_people/search",
         {"page":1,"per_page":1,"organization_domains":[domain],"person_titles":_TITLES}),
    ]
    for url, payload in _ENDPOINTS:
        try:
            r=requests.post(url,
                headers={"Content-Type":"application/json","X-Api-Key":APOLLO_KEY,
                         "Cache-Control":"no-cache"},
                json=payload, timeout=20)
            if r.status_code == 403:
                continue  # endpoint no disponible en este plan — probar siguiente
            r.raise_for_status()
            pp=r.json().get("people",[])
            if not pp: return {}
            p=pp[0]
            return {"email":p.get("email","—") or "—",
                    "name":f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                    "position":p.get("title","—"),
                    "linkedin":p.get("linkedin_url","—") or "—",
                    "phone":(p.get("phone_numbers") or [{}])[0].get("raw_number","—")}
        except Exception as e:
            print(f"    ⚠️ Apollo contact {domain} [{url[-30:]}]: {e}")
    return {}

def hunter_search(domain):
    if not HUNTER_KEY: return {}
    try:
        r=requests.get("https://api.hunter.io/v2/domain-search",params={"domain":domain,"api_key":HUNTER_KEY,"limit":3},timeout=15)
        r.raise_for_status()
        d=r.json().get("data",{})
        emails=d.get("emails",[])
        if not emails: return {"phone":d.get("phone_number","—") or "—"}
        kw=["ceo","director","gerente","manager","cto","owner","founder","presidente"]
        emails.sort(key=lambda e:(-sum(1 for k in kw if k in (e.get("position","") or "").lower()),-(e.get("confidence",0))))
        b=emails[0]
        return {"email":b.get("value","—"),"name":f"{b.get('first_name','')} {b.get('last_name','')}".strip(),"position":b.get("position","—"),"linkedin":b.get("linkedin","—"),"phone":d.get("phone_number","—") or "—"}
    except Exception as e:
        print(f"    ⚠️ Hunter {domain}: {e}")
        return {}

_EMAIL_RE = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@(?!(?:example|test|sentry|domain|yourcompany|empresa)\b)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'
)
_PHONE_RE = re.compile(r'(?:\+34\s?)?(?:6\d{2}|7\d{2}|8\d{2}|9\d{2})[\s.\-]?\d{3}[\s.\-]?\d{3}')
_CONTACT_PATHS = [
    "/contacto", "/contactar", "/contact", "/contact-us",
    "/sobre-nosotros", "/sobre-nosotras", "/equipo", "/team",
    "/quienes-somos", "/who-we-are",
]
_SKIP_EMAIL_DOMAINS = {
    "sentry.io","wixpress.com","example.com","test.com","domain.com",
    "wordpress.com","squarespace.com","godaddy.com","hostinger.com",
    "gmail.com","hotmail.com","outlook.com",
}
# Patrones para detectar el ERP que ya usa la empresa
_ERP_FINGERPRINTS = {
    "Odoo":     ["odoo.com","/web#action=","openerp","res.partner","powered by odoo"],
    "Holded":   ["holded.com","app.holded.com","holded erp"],
    "Sage":     ["sage.com","sage 50","sage 200","sage x3","sage murano"],
    "SAP":      ["sap.com","sap business one","sap b1","business one","mysap"],
    "A3ERP":    ["a3software.com","a3erp","a3 erp","a3con "],
    "Navision": ["dynamics nav","business central","nav 2018","bc 365"],
}
# Nombre completo en páginas de equipo: "Juan García" / "María José López"
_FULLNAME_RE = re.compile(
    r'\b([A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}(?:\s+(?:de\s+|del\s+|la\s+)?[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}){1,3})\b'
)

def _scrape_contact_page(base_url):
    """Visita /contacto y páginas de equipo con Scrapling y extrae emails + teléfonos."""
    found_emails, found_phones = [], []
    _all_html = []
    tried = set()
    base = base_url.rstrip("/")

    def _harvest(html):
        if not html: return
        _all_html.append(html)
        for m in re.findall(r'mailto:([^"\'>\s]+)', html):
            e = m.split("?")[0].strip().lower()
            if _EMAIL_RE.match(e): found_emails.append(e)
        for t in re.findall(r'tel:([^"\'>\s]+)', html):
            p = t.strip()
            if _PHONE_RE.search(p): found_phones.append(p)
        normalized = html.replace("[at]","@").replace("(at)","@").replace(" at ","@") \
                         .replace("[dot]",".").replace("(dot)",".")
        for e in _EMAIL_RE.findall(normalized):
            dom = e.split("@")[-1].lower()
            if dom not in _SKIP_EMAIL_DOMAINS:
                found_emails.append(e.lower())
        for p in _PHONE_RE.findall(html):
            found_phones.append(p.strip())

    def _get(url, timeout=10):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout, verify=True)
            r.raise_for_status()
            return r.text
        except requests.exceptions.SSLError:
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(url, headers=HEADERS, timeout=timeout, verify=False)
                r.raise_for_status()
                return r.text
            except Exception:
                return None
        except Exception:
            return None

    # 1. Homepage
    hp = fetch_html(base, timeout=12) or _get(base, timeout=12)
    _harvest(hp)
    tried.add(base)

    # 2. Páginas de contacto y equipo
    for path in _CONTACT_PATHS:
        if len(found_emails) >= 2: break
        url = base + path
        if url in tried: continue
        tried.add(url)
        html = fetch_html(url, timeout=10) or _get(url, timeout=10)
        if html and len(html) > 500:
            _harvest(html)

    # Detectar ERP actual en todo el HTML recopilado
    all_html_lower = " ".join(h.lower() for h in _all_html if h)
    detected_erp = "—"
    for erp_name, patterns in _ERP_FINGERPRINTS.items():
        if any(p in all_html_lower for p in patterns):
            detected_erp = erp_name
            break

    # Dedup y priorizar: dominio propio > genéricos conocidos
    own_domain = base.replace("https://","").replace("http://","").replace("www.","").split("/")[0].split(".")[0]
    emails_own = [e for e in dict.fromkeys(found_emails) if own_domain in e]
    emails_other = [e for e in dict.fromkeys(found_emails) if own_domain not in e
                    and e.split("@")[-1] not in _SKIP_EMAIL_DOMAINS]
    best_email = next(iter(dict.fromkeys(emails_own + emails_other + found_emails)), "—")
    best_phone = next(iter(dict.fromkeys(found_phones)), "—")

    # Extraer nombre de contacto de páginas de equipo
    contact_name = "—"
    for html in _all_html:
        if not html: continue
        # Buscar nombres cerca de cargos directivos en headings
        soup_txt = re.sub(r'<[^>]+>', ' ', html)
        for m in _FULLNAME_RE.finditer(soup_txt):
            candidate = m.group(1).strip()
            # Descartar si es nombre de empresa (más de 4 palabras) o contiene números
            words = candidate.split()
            if 2 <= len(words) <= 3 and not any(c.isdigit() for c in candidate):
                # Verificar que aparece cerca de palabras de cargo
                ctx = soup_txt[max(0,m.start()-150):m.end()+150].lower()
                if any(k in ctx for k in ["ceo","director","gerente","fundador","founder",
                                           "socio","responsable","presidente","cto","coo"]):
                    contact_name = candidate
                    break
        if contact_name != "—":
            break

    return {"email": best_email, "phone": best_phone, "name": contact_name,
            "current_erp": detected_erp, "source": "web_scrape"}


def enrich(domain):
    c={"email":"—","name":"—","position":"—","linkedin":"—","phone":"—"}
    if APOLLO_KEY:
        a=apollo_contact(domain)
        if a.get("email","—")!="—": c.update(a); return c
    if HUNTER_KEY:
        h=hunter_search(domain)
        if h.get("email","—")!="—": c.update(h); return c
    # Fallback: scraping directo de la web de la empresa
    try:
        base_url = f"https://{domain}"
        s = _scrape_contact_page(base_url)
        if s.get("email","—") != "—":
            c.update(s)
            print(f"    ✓ Web scrape {domain}: {s['email']}")
            return c
        elif s.get("phone","—") != "—":
            c["phone"] = s["phone"]  # guardamos teléfono aunque no haya email
    except Exception as e:
        print(f"    ⚠️ Web scrape {domain}: {e}")
    if HUNTER_KEY:
        h=hunter_search(domain)
        c.update(h)
    return c

def maps_places(query, city):
    if not GOOGLE_MAPS_KEY: return []
    try:
        geo=requests.get("https://maps.googleapis.com/maps/api/geocode/json",params={"address":city,"key":GOOGLE_MAPS_KEY},timeout=10)
        geo.raise_for_status()
        res=geo.json().get("results",[])
        if not res: return []
        loc=res[0]["geometry"]["location"]
        pl=requests.get("https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query":query,"location":f"{loc['lat']},{loc['lng']}","radius":25000,"language":"es","key":GOOGLE_MAPS_KEY},timeout=15)
        pl.raise_for_status()
        return pl.json().get("results",[])[:4]
    except Exception as e:
        print(f"    ⚠️ Maps {city}: {e}")
        return []

def maps_details(place_id):
    if not GOOGLE_MAPS_KEY: return {}
    try:
        r=requests.get("https://maps.googleapis.com/maps/api/place/details/json",
            params={"place_id":place_id,"fields":"name,website,formatted_phone_number,rating,user_ratings_total","language":"es","key":GOOGLE_MAPS_KEY},timeout=10)
        r.raise_for_status()
        return r.json().get("result",{})
    except: return {}


# ── Spider framework — crawling profundo de partners ───────────────────────
import asyncio

class PartnerSpider:
    """
    Spider que entra a cada página de partner individual para extraer
    web, teléfono, sector, clientes mencionados y contactos.
    Usa Scrapling con auto_save para recordar la estructura aunque cambie.
    """

    def __init__(self, partner_urls, competitor_name):
        self.partner_urls = partner_urls[:30]  # máximo 30 por competidor
        self.competitor_name = competitor_name
        self.results = []

    def crawl(self):
        """Crawl sincrónico — visita cada URL de partner."""
        if not SCRAPLING_OK:
            return []
        for url in self.partner_urls:
            try:
                page = Fetcher().get(url, timeout=15, stealthy_headers=True)
                if not page:
                    continue
                result = self._parse_partner(page, url)
                if result:
                    self.results.append(result)
                time.sleep(0.5)  # Gentil con el servidor
            except Exception as e:
                print(f"      ⚠️ Spider {url[:50]}: {e}")
        return self.results

    def _parse_partner(self, page, url):
        """Extrae información del perfil del partner."""
        domain = domain_from_url(url)
        if should_skip(domain):
            return None

        # Nombre de la empresa — auto_save para recordar el selector
        name_els = page.css("h1, .company-name, [class*='name']", auto_save=True)
        name = name_els[0].text if name_els else domain

        # Web externa del partner
        web_links = page.css("a[href*='http']:not([href*='odoo']):not([href*='holded']):not([href*='sage'])", auto_save=True)
        partner_web = ""
        for link in web_links:
            href = link.attrib.get("href", "")
            d = domain_from_url(href)
            if d and not should_skip(d) and d != domain:
                partner_web = href
                break

        # Teléfono
        phone_els = page.css("[class*='phone'], [class*='tel'], a[href*='tel:']", auto_save=True)
        phone = "—"
        if phone_els:
            phone = phone_els[0].text or phone_els[0].attrib.get("href","").replace("tel:","") or "—"

        # Sectores / industrias que mencionan
        text = page.get_text() if hasattr(page, 'get_text') else ""
        sector = detect_sector(text + " " + name)

        # Clientes mencionados
        client_els = page.css("[class*='client'], [class*='customer'], [class*='case']", auto_save=True)
        clients = [el.text for el in client_els[:3] if el.text and len(el.text) > 3]

        # Validar que el dominio externo sea una empresa real, no un gigante tech
        candidate = domain_from_url(partner_web) if partner_web else domain
        final_domain = candidate if (candidate and not should_skip(candidate)) else domain

        # Si el dominio final sigue siendo inválido, descartar
        if should_skip(final_domain):
            return None

        return {
            "name": name.strip()[:60],
            "domain": final_domain,
            "partner_url": url,
            "web": partner_web or f"https://{domain}",
            "phone": phone.strip()[:20],
            "sector": sector,
            "clients_mentioned": clients[:3],
            "competitor": self.competitor_name,
        }


# ── LinkedIn — enriquecimiento via StealthyFetcher ─────────────────────────
def linkedin_get_contact(company_name, domain):
    """
    Busca el perfil de LinkedIn de la empresa y extrae el contacto clave.
    Usa StealthyFetcher para bypasear el anti-bot de LinkedIn.
    Solo accede a perfiles públicos.
    """
    if not SCRAPLING_OK:
        return {}

    # Construir URL de búsqueda de empresa en LinkedIn
    query = requests.utils.quote(company_name)
    search_url = f"https://www.linkedin.com/search/results/companies/?keywords={query}"

    try:
        # LinkedIn requiere StealthyFetcher con headless para bypasear
        page = StealthyFetcher.fetch(
            search_url,
            headless=True,
            network_idle=True,
            timeout=20000,
            disable_resources=True,  # No cargar imágenes/media — más rápido
        )
        if not page:
            return {}

        # Extraer primer resultado de empresa — auto_save para adaptarse a cambios
        results = page.css(".entity-result__title-text, .search-entity-result", auto_save=True)
        if not results:
            return {}

        company_link_els = page.css(".entity-result__title-text a", auto_save=True)
        if not company_link_els:
            return {}

        company_li_url = company_link_els[0].attrib.get("href","")
        if not company_li_url:
            return {}

        # Entrar al perfil de la empresa
        company_page = StealthyFetcher.fetch(
            company_li_url,
            headless=True,
            network_idle=True,
            timeout=20000,
            disable_resources=True,
        )
        if not company_page:
            return {}

        # Extraer información del perfil público
        name_el   = company_page.css("h1.org-top-card-summary__title", auto_save=True)
        sector_el = company_page.css(".org-top-card-summary-info-list__info-item", auto_save=True)
        size_el   = company_page.css("[class*='employee-count']", auto_save=True)

        li_name   = name_el[0].text if name_el else company_name
        li_sector = sector_el[0].text if sector_el else "—"
        li_size   = size_el[0].text if size_el else "—"

        return {
            "linkedin_url": company_li_url,
            "linkedin_name": li_name.strip(),
            "linkedin_sector": li_sector.strip(),
            "linkedin_size": li_size.strip(),
        }

    except Exception as e:
        print(f"      ⚠️ LinkedIn {company_name}: {e}")
        return {}


def scrape_empresite():
    """Scrapes Empresite (eleconomista.es) por sector + provincia.
    Cada página da 30 empresas con nombre, web, teléfono y email en JSON-LD.
    Sin necesidad de Hunter.io para estas — están pre-enriquecidas.
    Rota sectores y provincias diariamente con el seed del día."""

    _ACTIVITIES = [
        ("FABRICACION-MAQUINARIA",          "Industrial"),
        ("TRANSPORTE-LOGISTICA-ALMACENAJE",  "Logística"),
        ("CONSTRUCCION-EDIFICACION",         "Construcción"),
        ("CONSTRUCCION-OBRAS",               "Construcción"),
        ("ALIMENTACION-BEBIDAS",             "Alimentación"),
        ("QUIMICA-PLASTICOS",                "Química"),
        ("MODA-TEXTIL",                      "Textil"),
        ("AUTOMOCION",                       "Automoción"),
        ("METALURGIA",                       "Metalurgia"),
        ("INDUSTRIA-METALICA",               "Industrial"),
        ("MADERA-MUEBLE",                    "Madera/Mueble"),
        ("DISTRIBUCION",                     "Distribución"),
        ("FARMACIA",                         "Farmacéutico"),
        ("OBRA-CIVIL",                       "Construcción"),
    ]
    _PROVINCES = [
        "MADRID","BARCELONA","VALENCIA","SEVILLA","MALAGA",
        "ZARAGOZA","ALICANTE","MURCIA","VALLADOLID","VIZCAYA",
        "GUIPUZCOA","PONTEVEDRA","CORUNA","CASTELLON","CORDOBA",
        "GRANADA","TOLEDO","BURGOS","LEON","SALAMANCA",
        "NAVARRA","TARRAGONA","GIRONA","LLEIDA","BALEARES",
    ]
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "Accept-Language": "es-ES,es;q=0.9",
        "Referer": "https://www.eleconomista.es/",
    }

    # Selección diaria rotada: 5 sectores × 4 provincias = 20 requests → ~600 empresas/día
    # Seed distinto del resto para no solapar con Maps
    import random as _r2
    _r2.seed(_DAY_SEED + 42)
    acts = _r2.sample(_ACTIVITIES, min(5, len(_ACTIVITIES)))
    provs = _r2.sample(_PROVINCES, min(4, len(_PROVINCES)))

    results = []
    seen_domains = set()

    for act_slug, sector in acts:
        for prov in provs:
            url = f"https://empresite.eleconomista.es/Actividad/{act_slug}/provincia/{prov}/"
            try:
                r = requests.get(url, headers=_HEADERS, timeout=15)
                if r.status_code not in (200,):
                    continue
                jblocks = re.findall(
                    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                    r.text, re.DOTALL
                )
                for block in jblocks:
                    try:
                        d = json.loads(block)
                        if d.get('@type') != 'ItemList':
                            continue
                        for entry in d.get('itemListElement', []):
                            item = entry.get('item', {})
                            raw_domain = item.get('@id', '')
                            if not raw_domain:
                                continue
                            # @id is like "www.domain.es" — normalize
                            d_clean = re.sub(r'^https?://(www\.)?', '', raw_domain).split('/')[0].lower()
                            if not d_clean or len(d_clean) < 4 or d_clean in seen_domains:
                                continue
                            if should_skip(d_clean):
                                continue
                            seen_domains.add(d_clean)
                            name = item.get('name', d_clean).strip()
                            # Skip liquidated companies and those clearly out of ICP
                            if any(kw in name.lower() for kw in
                                   ['liquidacion','concurso acreedores','disuelto','extinguida']):
                                continue
                            phone = str(item.get('telephone', '') or '').strip()
                            email = str(item.get('email', '') or '').strip()
                            city = item.get('address', {}).get('addressLocality', prov).split()[0].title()
                            results.append({
                                'name': name, 'domain': d_clean,
                                'phone': f"+34{phone}" if phone and not phone.startswith('+') else phone or '—',
                                'email': email or '—',
                                'sector': sector, 'city': city, 'province': prov,
                            })
                    except Exception:
                        continue
                time.sleep(0.8)   # respetuoso con el servidor
            except Exception as e:
                print(f"    ⚠️ Empresite {act_slug}/{prov}: {e}")
                continue

    print(f"    Empresite total: {len(results)} empresas en {len(acts)} sectores × {len(provs)} provincias")
    return results


def scrape_einforma():
    """eInforma CNAE directory (eleconomista.es group) — static HTML con microdata schema.org.
    50 empresas/página con nombre, dominio, ciudad y provincia.
    4 CNAE rotados × 2 páginas = ~400 empresas/run para enriquecer via Hunter/web-scraping.
    Filtra extintas/disueltas automáticamente."""

    _CNAE = [
        ("Industria-Manufacturera", "CNAE-C028-Fabricacion-De-Maquinaria-Y-Equipo-N-C-O-P-",     "Industrial"),
        ("Industria-Manufacturera", "CNAE-C013-Industria-Textil",                                 "Textil"),
        ("Industria-Manufacturera", "CNAE-C020-Industria-Quimica",                                "Química"),
        ("Industria-Manufacturera", "CNAE-C022-Fabricacion-De-Productos-De-Caucho-Y-Plasticos",   "Plásticos"),
        ("Industria-Manufacturera", "CNAE-C023-Fabricacion-De-Otros-Productos-Minerales-No-Metalicos", "Industrial"),
        ("Industria-Manufacturera", "CNAE-C011-Fabricacion-De-Bebidas",                           "Alimentación"),
        ("Industria-Manufacturera", "CNAE-C032-Otras-Industrias-Manufactureras",                  "Industrial"),
        ("Transporte-Y-Almacenamiento", "CNAE-494-Transporte-De-Mercancias-Por-Carretera-Y-Servicios-De-Mudanza", "Logística"),
        ("Industria-Manufacturera", "CNAE-C025-Fabricacion-De-Productos-Metalicos-Excepto-Maquinaria-Y-Equipo",  "Metalúrgico"),
        ("Construccion-Y-Sector-Inmobiliario", "CNAE-F041-Construccion-De-Edificios",             "Construcción"),
    ]
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "Accept-Language": "es-ES,es;q=0.9",
        "Referer": "https://www.einforma.com/",
    }
    _SKIP_KW = ['extinguida', 'extinguido', 'liquidaci', 'concurso', 'disuelto', 'disuelta', 'en baja']

    import html as _html_mod
    import random as _r3
    _r3.seed(_DAY_SEED + 99)

    # 4 CNAE rotados × 2 páginas = 8 requests → ~400 empresas/run
    selected = _r3.sample(_CNAE, min(4, len(_CNAE)))

    results = []
    seen_domains = set()

    for cat, slug, sector in selected:
        start_page = _r3.randint(1, 10)   # rota qué página fetch para no repetir siempre las mismas
        for page_off in range(2):
            page = start_page + page_off
            if page == 1:
                url = f"https://www.einforma.com/empresas/{cat}/{slug}.html"
            else:
                url = f"https://www.einforma.com/empresas/{cat}/{slug}/Empresas-{page}.html"
            try:
                r = requests.get(url, headers=_HEADERS, timeout=15)
                if r.status_code != 200:
                    continue
                entries = re.findall(
                    r'itemtype="http://schema\.org/LocalBusiness".*?'
                    r'itemprop="name">([^<]+)</span>.*?'
                    r'itemprop="addressLocality">([^<]+)</span>.*?'
                    r'itemprop="addressRegion">([^<]+)</span>.*?'
                    r'<td>(www\.[a-z0-9][a-z0-9.\-]{2,40}\.[a-z]{2,6})</td>',
                    r.text, re.DOTALL
                )
                for name_raw, city_raw, province_raw, domain_raw in entries:
                    name = _html_mod.unescape(name_raw).strip()
                    if any(kw in name.lower() for kw in _SKIP_KW):
                        continue
                    domain = domain_raw.replace('www.', '', 1).strip()
                    if not domain or domain in seen_domains or should_skip(domain):
                        continue
                    seen_domains.add(domain)
                    results.append({
                        'name': name, 'domain': domain, 'sector': sector,
                        'city': _html_mod.unescape(city_raw).strip(),
                        'province': _html_mod.unescape(province_raw).strip(),
                    })
                time.sleep(0.7)
            except Exception as e:
                print(f"    ⚠️ eInforma {slug[:25]}/p{page}: {e}")

    print(f"    eInforma total: {len(results)} empresas en {len(selected)} CNAE codes")
    return results


def scrape_cases_of_success():
    """Scrapes 'casos de éxito' de partners Dynamics/SAP: Davisa (81), ARBENTIA (20), Aitana (62).
    Empresas que YA USAN ERP competidor = prospectos migración score 3.
    Domain discovery: slug → HTTP HEAD (slug.es / slugsinbarras.es / slug.com).
    10 empresas/run rotadas diariamente del pool ~160."""

    _ERP_SUFFIXES = sorted([
        'business-central', 'dynamics-365', 'dynamics-nav', 'dynamics',
        'navision', 'sage-x3', 'sage-50', 'sage', 'microsoft-365', 'microsoft',
        'sharepoint', 'azure-ai', 'azure', 'qlik-cloud-saas', 'qlik-cloud', 'qlik',
        'power-bi', 'cloud-saas', 'ciberseguridad', 'erp', 'ai',
    ], key=len, reverse=True)

    _SKIP_SLUGS = {'feed', 'page', '', 'casos-de-exito', 'caso-de-exito', 'recursos'}
    _SECTOR_MAP = {
        'fabricacion': 'Fabricación', 'logistica': 'Logística',
        'alimentacion': 'Alimentación', 'distribucion': 'Distribución',
        'promocion-construccion': 'Construcción', 'servicios': 'Servicios',
        'retail': 'Retail', 'farmacia': 'Farmacéutico',
    }

    def _strip_erp(slug):
        changed = True
        while changed:
            changed = False
            for s in _ERP_SUFFIXES:
                if slug.endswith('-' + s):
                    slug = slug[:-len(s) - 1]
                    changed = True
                    break
        return slug or slug

    def _guess_domain(slug):
        clean = slug.replace('-', '')
        for candidate in [f"{slug}.es", f"{clean}.es", f"{slug}.com", f"{clean}.com"]:
            for scheme in ["https://www.", "https://"]:
                try:
                    resp = requests.head(f"{scheme}{candidate}", timeout=3,
                                        allow_redirects=True, verify=False)
                    if resp.status_code < 400:
                        final = re.sub(r'^https?://(www\.)?', '', resp.url).split('/')[0].lower()
                        return final
                except Exception:
                    continue
        return None

    all_cases = []

    # ── Davisa (81 casos, card data-sector + slug + h3 name) ──────────────
    try:
        r = requests.get("https://www.davisa.es/casos-de-exito/", headers=HEADERS, timeout=15)
        if r.status_code == 200:
            cards = re.findall(
                r'data-sector="([^"]+)".*?href="/casos-de-exito/([^/]+)/".*?<h3[^>]*>([^<]+)</h3>',
                r.text, re.DOTALL
            )
            for sector_raw, slug, name in cards:
                if slug in _SKIP_SLUGS:
                    continue
                all_cases.append({
                    'slug': slug, 'name': name.strip(),
                    'sector': _SECTOR_MAP.get(sector_raw, 'General'),
                    'erp': 'MS Dynamics',
                })
            print(f"    Davisa: {len(cards)} casos")
    except Exception as e:
        print(f"    ⚠️ Davisa: {e}")

    # ── ARBENTIA (20 casos, slugs de URL) ──────────────────────────────────
    try:
        r = requests.get("https://www.arbentia.com/recursos/casos-de-exito/", headers=HEADERS, timeout=15)
        if r.status_code == 200:
            slugs = list(dict.fromkeys(
                re.findall(r'recursos/casos-de-exito/([a-z0-9\-]+)/', r.text)
            ))
            before = len(all_cases)
            for slug in slugs:
                if slug in _SKIP_SLUGS:
                    continue
                name = ' '.join(w.capitalize() for w in slug.split('-'))
                all_cases.append({'slug': slug, 'name': name, 'sector': 'General', 'erp': 'MS Dynamics'})
            print(f"    ARBENTIA: {len(all_cases) - before} casos")
    except Exception as e:
        print(f"    ⚠️ ARBENTIA: {e}")

    # ── Aitana (62 casos en 5 páginas WordPress, strip sufijo ERP del slug) ─
    try:
        aitana_slugs = []
        for page in range(1, 4):   # páginas 1-3 = ~90 casos, dedup → ~45 únicos
            url = ("https://www.aitana.es/caso-de-exito/" if page == 1
                   else f"https://www.aitana.es/caso-de-exito/page/{page}/")
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            raw = re.findall(
                r'href="https://www\.aitana\.es/caso-de-exito/([a-z0-9\-]+)/"', r.text
            )
            aitana_slugs.extend(raw)
            time.sleep(0.4)
        before = len(all_cases)
        for slug in list(dict.fromkeys(aitana_slugs)):
            if slug in _SKIP_SLUGS:
                continue
            company_slug = _strip_erp(slug)
            if len(company_slug) < 3:
                continue
            name = ' '.join(w.capitalize() for w in company_slug.split('-'))
            all_cases.append({'slug': company_slug, 'name': name, 'sector': 'General', 'erp': 'MS Dynamics'})
        print(f"    Aitana: {len(all_cases) - before} casos")
    except Exception as e:
        print(f"    ⚠️ Aitana: {e}")

    # ── Domain discovery: 10 companies/run rotated daily ──────────────────
    import random as _rc
    _rc.seed(_DAY_SEED + 777)
    _rc.shuffle(all_cases)
    selected = all_cases[:10]

    results = []
    seen_slugs = set()
    for case in selected:
        if case['slug'] in seen_slugs:
            continue
        seen_slugs.add(case['slug'])
        domain = _guess_domain(case['slug'])
        if not domain or should_skip(domain):
            continue
        results.append({
            'name': case['name'], 'domain': domain,
            'erp_current': case['erp'], 'sector': case['sector'],
        })

    print(f"    → {len(results)}/10 dominios encontrados ({len(all_cases)} pool total)")
    return results


def scrape_erp_users():
    """Scrapes páginas de 'casos de éxito' de competidores ERP para encontrar empresas
    que YA USAN un ERP — son prospectos de migración de alta calidad (score 3).
    Returns list of {name, domain, web, erp_current, sector, source_url}"""
    ERP_USERS_PAGES = [
        # Distrito K (TeamSystem) — 159 empresas reales con links a sus webs
        {"url": "https://www.distritok.com/aplicaciones/casos-exito/", "erp": "TeamSystem/DistritoK"},
        # Solmicro/Zucchetti — red de distribución
        {"url": "https://www.solmicro.com/red-de-distribucion-erp/red-de-distribucion-erp", "erp": "Solmicro/Zucchetti"},
    ]
    SKIP_COMPETITOR_DOMAINS = {
        "distritok","teamsystem","solmicro","zucchetti","odoo","sap","sage","holded",
        "google","facebook","twitter","linkedin","instagram","youtube","cloudflare",
        "wp-content","cdn.","fonts.","analytics","jquery","bootstrap","microsoft",
        "wp-json","cookie","amazon","apple","mozilla",
    }
    results = []
    seen = set()

    for source in ERP_USERS_PAGES:
        try:
            r = requests.get(source["url"], headers=HEADERS, timeout=15, verify=True)
            if r.status_code != 200:
                print(f"    ⚠️ ERP users {source['erp']}: HTTP {r.status_code}")
                continue
            html = r.text
            erp = source["erp"]

            # Extract external company website URLs
            for url_match in re.findall(r'href="(https?://[^"]{5,80})"', html):
                # Get domain
                d = re.sub(r'^https?://(www\.)?', '', url_match).split('/')[0].lower().split('?')[0]
                if not d or len(d) < 4 or d in seen or should_skip(d):
                    continue
                if any(s in d for s in SKIP_COMPETITOR_DOMAINS):
                    continue
                if not re.match(r'^[a-z0-9][a-z0-9\-\.]{2,50}\.[a-z]{2,}$', d):
                    continue

                seen.add(d)
                results.append({
                    "name": d.split('.')[0].replace('-', ' ').title(),
                    "domain": d,
                    "web": url_match,
                    "erp_current": erp,
                    "sector": "General",
                    "source_url": source["url"],
                })

            print(f"    ERP users {erp}: {len([r for r in results if r.get('erp_current')==erp])} empresas")

        except Exception as e:
            print(f"    ⚠️ ERP users {source['erp']}: {e}")

    return results


def scrape_partners():
    all_p = []
    seen_domains = set()

    def _add(name, competitor, url, domain):
        name = name.strip()
        if not domain or domain in seen_domains or should_skip(domain): return
        if len(name) < 3 or len(name) > 80: return
        seen_domains.add(domain)
        all_p.append({"name": name, "competitor": competitor, "url": url, "domain": domain})

    def _name_from_title(t):
        return t.replace(" - Partners","").replace(" | Odoo","").split(" - ")[0].split(" | ")[0].strip()

    def _name_from_odoo_slug(url):
        """Extrae nombre de empresa del slug de perfil de Odoo: /es/partners/studio73-s-l-1506080 → Studio73 S L"""
        slug = url.rstrip('/').split('/es/partners/')[-1]
        slug = re.sub(r'-\d+$', '', slug)  # quita el ID numérico final
        return ' '.join(w.capitalize() for w in slug.split('-') if w and not w.isdigit())

    # ── Odoo partners España — Brave Search (múltiples ángulos) ──────────────
    odoo_queries = [
        '"partner de Odoo" OR "partner Odoo" España empresa',
        '"implementador Odoo" OR "consultor Odoo" España pyme',
        '"Gold Partner Odoo" OR "Silver Partner Odoo" España',
        '"Ready Partner Odoo" España',
        '"Odoo partner" Madrid implementación ERP',
        '"Odoo partner" Barcelona consultoría ERP',
        '"Odoo partner" Valencia OR Bilbao OR Sevilla España',
        '"partner oficial Odoo" España autorizado',
        'implementador Odoo España pyme certificado',
        '"Odoo Gold" OR "Odoo Silver" OR "Odoo Ready" España empresa',
    ]
    for q in odoo_queries:
        for r in brave_search(q, num=10):
            d = domain_from_url(r["url"])
            if d and "odoo.com" not in d:
                _add(_name_from_title(r["title"]), "Odoo", r["url"], d)
    print(f"    Odoo (Brave directo): {sum(1 for p in all_p if p['competitor']=='Odoo')}")

    # Odoo — perfiles del directorio via site: queries en Brave
    # El directorio de Odoo es JS-only, pero Brave lo tiene indexado.
    # Extraemos nombres del slug y los añadimos sin dominio; el flujo principal
    # (línea ~971) hará una búsqueda Brave para encontrar el dominio real.
    odoo_site_queries = [
        'site:odoo.com/es/partners Madrid',
        'site:odoo.com/es/partners Barcelona',
        'site:odoo.com/es/partners Valencia',
        'site:odoo.com/es/partners Bilbao',
        'site:odoo.com/es/partners Sevilla',
        'site:odoo.com/es/partners Zaragoza',
        'site:odoo.com/es/partners Málaga',
        'site:odoo.com/es/partners Murcia',
        'site:odoo.com/es/partners Alicante',
        'site:odoo.com/es/partners Valladolid',
        'site:odoo.com/es/partners Granada',
        'site:odoo.com/es/partners España Gold',
    ]
    odoo_site_before = len(all_p)
    for q in odoo_site_queries:
        for r in brave_search(q, num=10):
            url = r["url"]
            # Solo perfiles individuales: /es/partners/slug-con-id-numerico
            if re.match(r'https://www\.odoo\.com/es/partners/[a-z0-9][a-z0-9\-]+-\d+/?$', url):
                slug_name = _name_from_odoo_slug(url)
                if slug_name and slug_name not in [p["name"] for p in all_p]:
                    # Sin dominio — el flujo principal lo buscará con Brave
                    all_p.append({"name": slug_name, "competitor": "Odoo", "url": url, "domain": ""})
    print(f"    Odoo (perfiles directorio): {len(all_p) - odoo_site_before}")

    # ── SAP partners España ───────────────────────────────────────────────────
    sap_queries = [
        '"partner SAP Business One" España consultor implementador',
        '"SAP Business One" partner reseller España pyme',
        '"partner SAP B1" España',
        '"implementador SAP Business One" España',
        '"SAP Business One" Madrid OR Barcelona OR Valencia partner',
    ]
    for q in sap_queries:
        for r in brave_search(q, num=10):
            d = domain_from_url(r["url"])
            if d and "sap.com" not in d:
                _add(_name_from_title(r["title"]), "SAP", r["url"], d)
    print(f"    SAP partners: {sum(1 for p in all_p if p['competitor']=='SAP')}")

    # ── Sage partners España ──────────────────────────────────────────────────
    sage_queries = [
        '"partner Sage" España consultor ERP autorizado',
        '"distribuidor Sage" España pyme ERP',
        '"partner oficial Sage" España',
        '"Sage 200" OR "Sage X3" OR "Sage 50" partner España',
        'consultor "Sage ERP" España implementación autorizado',
    ]
    for q in sage_queries:
        for r in brave_search(q, num=10):
            d = domain_from_url(r["url"])
            if d and "sage.com" not in d:
                _add(_name_from_title(r["title"]), "Sage", r["url"], d)
    print(f"    Sage partners: {sum(1 for p in all_p if p['competitor']=='Sage')}")

    # ── Holded partners (scraping con JS) ─────────────────────────────────────
    # ── Holded solution partners (directorio estático con H2/H3) ─────────────
    holded_before = len(all_p)
    # Intentar primero el directorio de solution partners (tiene lista completa en HTML)
    holded_dir_urls = [
        "https://www.holded.com/es/directorio-solution-partners",
        "https://www.holded.com/es/partners",
    ]
    for h_url in holded_dir_urls:
        try:
            rh = requests.get(h_url, headers=HEADERS, timeout=12)
            if rh.status_code != 200:
                continue
            soup = BeautifulSoup(rh.text, "html.parser")
            # Links to external partner sites
            for a in soup.find_all("a", href=re.compile(r"^https?://(?!.*holded\.com)")):
                t = a.get_text(strip=True)
                d = domain_from_url(a.get("href",""))
                if 3 < len(t) < 80 and d:
                    _add(t, "Holded", a.get("href",""), d)
            # H2/H3 headings as fallback (company names embedded in page)
            for el in soup.select("h2,h3"):
                t = el.get_text(strip=True)
                if 3 < len(t) < 80 and not any(skip in t.lower() for skip in
                    ["holded","plan","precio","partner","todos","sobre","ayuda","blog"]):
                    _add(t, "Holded", h_url, "")
            if len(all_p) > holded_before:
                break  # got results, no need to try next URL
        except Exception as e:
            print(f"    ⚠️ Holded {h_url}: {e}")
    # Fallback con Scrapling dinámico
    if len(all_p) == holded_before:
        page = fetch("https://www.holded.com/es/partners", dynamic=True)
        html = page.html_content if page and not isinstance(page, str) else (page or "")
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for el in soup.select("h2,h3,[class*='partner'],[class*='agency']")[:40]:
                t = el.get_text(strip=True)
                if 3 < len(t) < 80:
                    _add(t, "Holded", "https://www.holded.com/es/partners", "")
    print(f"    Holded partners: {len(all_p) - holded_before}")

    # ── Ahora ERP — integrantes/distribuidores ────────────────────────────────
    ahora_before = len(all_p)
    try:
        ra = requests.get("https://www.ahora.es/integrantes/", headers=HEADERS, timeout=12)
        if ra.status_code == 200:
            soup = BeautifulSoup(ra.text, "html.parser")
            for a in soup.find_all("a", href=re.compile(r"^https?://(?!.*ahora\.es)")):
                t = a.get_text(strip=True)
                d = domain_from_url(a.get("href",""))
                if 3 < len(t) < 80 and d:
                    _add(t, "Ahora ERP", a.get("href",""), d)
            for el in soup.select("h2,h3,.partner-name,.company-name"):
                t = el.get_text(strip=True)
                if 3 < len(t) < 60:
                    _add(t, "Ahora ERP", "https://www.ahora.es/integrantes/", "")
    except Exception as e:
        print(f"    ⚠️ Ahora ERP: {e}")
    print(f"    Ahora ERP integrantes: {len(all_p) - ahora_before}")

    print(f"    Total partners encontrados: {len(all_p)}")
    return all_p  # sin límite artificial

# ── Datos históricos ────────────────────────────────────────────────────────
def load_data():
    if DATA_FILE.exists():
        d = json.load(open(DATA_FILE))
        if "engagement_history" not in d: d["engagement_history"] = []
        return d
    return {"comp_hashes":{},"changes_history":[],"leads_history":[],"engagement_history":[],"prices":{},"features":{}}

def save_data(data):
    with open(DATA_FILE,"w") as f: json.dump(data,f,ensure_ascii=False,indent=2)

# ── Búsqueda de posts interactuables para engagement ───────────────────────
def search_engagement_posts(data):
    print("→ Buscando posts para engagement...")
    new = []
    from datetime import datetime, timedelta
    cutoff = (datetime.today() - timedelta(days=14)).strftime("%d/%m/%Y")
    seen = {p.get("url","") for p in data["engagement_history"] if p.get("date","") >= cutoff}

    for s in ENGAGEMENT_SEARCHES:
        results = brave_search(s["query"], num=5)
        for r in results:
            url = r.get("url","")
            if not url or url in seen: continue
            domain = domain_from_url(url)

            # Solo pasar si es dominio interactuable conocido O la URL tiene patrones de post/hilo
            is_known_domain = any(eng in domain for eng in ENGAGEMENT_DOMAINS)
            is_thread_url   = any(p in url.lower() for p in ["/comments/","/post/","/thread/","/pregunta/","/question/","/discussion/","/t/","/r/"])
            if not is_known_domain and not is_thread_url:
                continue

            post = {
                "url":         url,
                "domain":      domain,
                "title":       r.get("title","")[:120],
                "snippet":     r.get("snippet","")[:300],
                "label":       s["label"],
                "date":        TODAY,
                "commented":   False,
                "comment_draft": "",
            }
            new.append(post)
            seen.add(url)
            data["engagement_history"].insert(0, post)

    # Mantener solo los últimos 200 en historial
    data["engagement_history"] = data["engagement_history"][:200]
    print(f"  → {len(new)} posts nuevos para engagement")
    return new

# ── Exportar oportunidades para el agente forum-monitor ────────────────────
def export_engagement_json(posts):
    out_path = REPO_DIR / "engagement_opportunities.json"
    pending = [p for p in posts if not p.get("commented", False)]
    with open(out_path, "w") as f:
        json.dump({
            "generated_at": NOW,
            "pending_count": len(pending),
            "posts": pending
        }, f, ensure_ascii=False, indent=2)
    print(f"  → engagement_opportunities.json: {len(pending)} posts pendientes")

# ── Scraping competidores ───────────────────────────────────────────────────
def scrape_competitors(data):
    print("→ Competidores...")
    results,changes=[],[]
    for comp in COMPETITORS:
        print(f"  {comp['name']}...")
        html=fetch(comp["url"], dynamic=("holded" in comp["id"] or "odoo" in comp["id"]))  # JS para Odoo y Holded
        if not html:
            results.append({"comp":comp,"change":"error","prices":{},"features":[]}); continue
        h=text_hash(clean(html)); ph=data["comp_hashes"].get(comp["id"],"")
        changed=h!=ph and ph!=""; is_new=ph==""
        data["comp_hashes"][comp["id"]]=h
        ph2=fetch(comp["pricing_url"]) if comp["pricing_url"]!=comp["url"] else html
        prices=extract_prices(ph2 or html); data["prices"][comp["id"]]=prices
        bh=fetch(comp["blog_url"]) if comp["blog_url"]!=comp["url"] else html
        scraped_feats=extract_features(bh or html)
        brave_feats=brave_comp_news(comp["name"])
        features=list(dict.fromkeys(brave_feats+scraped_feats))[:5]
        data["features"][comp["id"]]=features
        if changed:
            e={"date":TODAY,"competitor":comp["name"],"section":"Web principal","type":"Contenido modificado","detail":f"Cambios en {comp['url']}"}
            data["changes_history"].insert(0,e); changes.append(e)
        results.append({"comp":comp,"change":"new" if is_new else ("changed" if changed else "none"),"prices":prices,"features":features})
    return results,changes

# ── Búsqueda de leads ───────────────────────────────────────────────────────
def search_all_leads(data):
    print("→ Buscando leads...")
    new=[]
    # Dedup por fuente:
    #   - partner_scraping / partner_spider: historial completo
    #     (son consultoras Odoo/Holded — no son prospectos)
    #   - brave_search / apollo / google_maps: últimos 90 días
    #     (empresas reales — pueden reentrar en el próximo trimestre)
    from datetime import datetime, timedelta
    cutoff_90 = (datetime.today() - timedelta(days=90)).strftime("%d/%m/%Y")
    seen_partners = {l.get("domain","") for l in data["leads_history"]
                     if l.get("domain","") and l.get("source_type","") in
                     ("partner_scraping","partner_spider")}
    seen_90 = {l.get("domain","") for l in data["leads_history"]
               if l.get("domain","") and l.get("date","") >= cutoff_90
               and l.get("source_type","") in ("brave_search","apollo","google_maps")}
    seen = seen_partners | seen_90
    print(f"  Dominios en dedup — partners (total): {len(seen_partners)} | Brave/Apollo/Maps (90d): {len(seen_90)}")

    # 1. Google Custom Search
    print(f"  [1/4] Brave Search... (ciudad hoy: {_CITY}, sector: {_SECTOR})")
    for s in LEAD_SEARCHES:
        for r in brave_search(s["query"],num=5):
            d=domain_from_url(r["url"])
            if d in seen or should_skip(d): continue
            # Descartar artículos/guías/portales de empleo desde la URL y el título
            if is_article(r["url"], r.get("title","")):
                continue
            co=r["title"].split("|")[0].split("-")[0].strip()
            if len(co)>60: co=d
            txt=r["title"]+r["snippet"]
            lead=make_lead(co,d,detect_sector(txt),s["signal"],s["signal_label"],s["signal_class"],r["url"],r["snippet"],
                           score_lead(txt.lower(),s["signal"],r["url"],r.get("title","")),"brave_search")
            new.append(lead); seen.add(d)

    # 2. Apollo empresas
    print("  [2/4] Apollo.io...")
    # Palabras clave que indican compañías fuera del ICP — se descartan
    _BAD_NAME_KWORDS = [
        # Educación y formación
        "school","escuela","instituto","college","universidad","university",
        "academy","académia","posgrado","master","mba","postgrado","open academy",
        # Empleo y RRHH
        "jobs","empleo","bolsa","carrera","selección","headhunt","rrhh","talent",
        "etalentum","adqualis","melt group","meltgroup","randstad","adecco","manpower",
        # Moda y diseño
        "fashion","moda","diseño gráfico","grafica","gràffica",
        # Agencias de marketing/digital (ya los conseguimos por partner_scraping)
        "agencia creativa","marketing digital","seo agency",
        # Fuera de ICP: consultoras RRHH, psicología, fundaciones, medios, legal
        "consulting","consultor","consultora","psicotec","psico","aprender",
        "talento y liderazgo","hosteltur","hostelco","nunegal","adqualis",
        "fundación","fundacion","fundació","asociación","asociacion",
        # Medios y noticias (no son prospectos)
        "noticias","juridicas","forbes","expansion","diario","periódico",
        "revista","media","press","editorial","publicación",
        # Consultoras ISO/calidad (certifican a otras empresas, no son compradores ERP)
        "certificacion iso","calidad iso","iso 9001","consultor iso","auditoria iso",
        # Agencias web y marketing digital
        "agencia web","diseño web","marketing web","seo málaga","seo madrid",
        "mantenimiento web","páginas web","desarrollo web",
            # Gimnasios, ocio, fitness (falsos positivos geográficos)
        "fitness","deporte","crossfit","pilates",
    ]
    for org in apollo_companies():
        d=domain_from_url(org.get("primary_domain","") or org.get("website_url","") or "")
        if not d or d in seen or should_skip(d): continue
        # Aceptar .es siempre; para otros TLDs solo si Apollo confirma Spain en primary_domain
        # o la empresa tiene phone_number español. Descarta dominios sin señal española.
        if not d.endswith(".es"):
            phone = (org.get("primary_phone") or {}).get("raw_number","")
            if not (phone.startswith("+34") or phone.startswith("34")):
                continue
        name=org.get("name","") or d
        emp=org.get("num_employees",0) or 0
        industry=org.get("industry","") or ""
        # Descartar empresas claramente fuera del ICP por nombre
        if any(kw in name.lower() for kw in _BAD_NAME_KWORDS):
            print(f"    SKIP ICP: {name}")
            continue
        sector=detect_sector(industry+" "+name)
        lead=make_lead(name,d,sector,"erp","Empresa España (Apollo)","s-erp",f"https://{d}",f"Apollo.io · {emp} empleados · {org.get('industry','—')}",2,"apollo")
        lead["linkedin_org"]=org.get("linkedin_url","—") or "—"
        new.append(lead); seen.add(d)

    # 3. Google Maps
    print("  [3/4] Google Maps...")
    maps_count=0
    if GOOGLE_MAPS_KEY:
        print(f"    Usando Maps key: {GOOGLE_MAPS_KEY[:8]}...")
        for ms in MAPS_SEARCHES:
            for city in _MAPS_CITIES:
                for place in maps_places(ms["query"],city):
                    det=maps_details(place.get("place_id",""))
                    web=det.get("website","")
                    if not web: continue
                    d=domain_from_url(web)
                    if not d or d in seen or should_skip(d): continue
                    reviews=det.get("user_ratings_total",0) or 0
                    if reviews<5: continue
                    phone=det.get("formatted_phone_number","—")
                    lead=make_lead(place.get("name",d),d,ms["sector"],"erp","Google Maps","s-erp",web,f"Google Maps · {reviews} reseñas · {city.split(',')[0]}",2 if reviews>20 else 1,"google_maps")
                    lead["phone"]=phone
                    new.append(lead); seen.add(d); maps_count+=1; time.sleep(0.2)
        print(f"    → {maps_count} de Maps")
    else:
        print("    ⚠️ Sin GOOGLE_MAPS_API_KEY")

    # 4. Usuarios reales de ERPs competidores — casos de éxito de partners Dynamics/SAP
    print("  [4/5] ERP users: casos de éxito partners + Distrito K/Solmicro...")
    for co in scrape_cases_of_success():
        d = co.get("domain", "")
        if not d or d in seen or should_skip(d):
            continue
        lead = make_lead(
            co["name"], d, co.get("sector", "General"),
            "migrate", f"Usuario {co['erp_current']}", "s-mig",
            f"https://{d}",
            f"Empresa usando {co['erp_current']} — potencial migración a Etendo",
            3, "erp_user"
        )
        lead["current_erp"] = co["erp_current"]
        new.append(lead); seen.add(d)

    for eu in scrape_erp_users():
        d = eu.get("domain","")
        if not d or d in seen or should_skip(d): continue
        lead = make_lead(
            eu["name"], d, eu.get("sector","General"),
            "migrate", f"Usuario {eu['erp_current']}", "s-mig",
            eu["web"],
            f"Empresa usando {eu['erp_current']} — potencial migración a Etendo",
            3, "erp_user"
        )
        lead["current_erp"] = eu["erp_current"]
        new.append(lead); seen.add(d)
    erp_user_count = len([l for l in new if l.get("source_type")=="erp_user"])
    print(f"    → {erp_user_count} empresas usuarias de ERP competidor")

    # 5. Partners competidores — con Spider de crawling profundo
    print("  [5/5] Partners con Spider...")
    raw_partners=scrape_partners()
    partner_urls_by_comp = {}
    for p in raw_partners:
        d=p.get("domain","")
        if not d:
            res=brave_search(f'"{p["name"]}" España ERP consultoría',num=1)
            if res: d=domain_from_url(res[0]["url"])
        if not d or d in seen or should_skip(d): continue

        # Lead básico
        lead=make_lead(p["name"],d,"Consultoría ERP","partner",f"Partner {p['competitor']}","s-par",
                       p.get("url",f"https://{d}"),f"Partner certificado de {p['competitor']} en España",2,"partner_scraping")
        new.append(lead); seen.add(d)

        # Acumular URLs de perfil de partner para crawling profundo
        if p.get("url") and "http" in p.get("url",""):
            comp = p.get("competitor","")
            if comp not in partner_urls_by_comp:
                partner_urls_by_comp[comp] = []
            partner_urls_by_comp[comp].append(p["url"])

    # Crawling profundo con Spider — entra a cada perfil de partner
    if SCRAPLING_OK and partner_urls_by_comp:
        print(f"  Spider crawling profundo — {sum(len(v) for v in partner_urls_by_comp.values())} perfiles...")
        for comp_name, urls in partner_urls_by_comp.items():
            spider = PartnerSpider(urls, comp_name)
            profiles = spider.crawl()
            for profile in profiles:
                d = profile.get("domain","")
                if not d or d in seen or should_skip(d): continue
                # Lead enriquecido con datos del Spider
                lead = make_lead(
                    profile["name"], d, profile["sector"],
                    "partner", f"Partner {comp_name} (deep)", "s-par",
                    profile["web"], f"Partner {comp_name} — clientes: {', '.join(profile['clients_mentioned']) or 'N/A'}",
                    2, "partner_spider"  # Score 2 — son partners de competidores, no leads directos
                )
                lead["phone"] = profile.get("phone","—")
                new.append(lead); seen.add(d)
        print(f"    → {len([l for l in new if l.get('source_type')=='partner_spider'])} perfiles completos extraídos")

    # Empresite — directorio con email+teléfono ya incluidos (no necesitan Hunter)
    print("  Empresite B2B directory...")
    empresite_count = 0
    for co in scrape_empresite():
        d = co.get("domain", "")
        if not d or d in seen or should_skip(d):
            continue
        lead = make_lead(
            co["name"], d, co["sector"],
            "erp", f"Empresite {co['sector']}", "s-erp",
            f"https://{d}",
            f"Empresite · {co.get('city','España')} · {co.get('province','')}",
            2, "empresite"
        )
        # Pre-enriched: email y teléfono directamente del directorio
        lead["phone"] = co.get("phone", "—")
        lead["email"] = co.get("email", "—")
        lead["contact_name"] = "—"
        lead["current_erp"] = "—"
        new.append(lead)
        seen.add(d)
        empresite_count += 1
    print(f"    → {empresite_count} empresas de Empresite (email+tel incluidos)")

    # eInforma — CNAE directory (dominio confirmado, sin email/tel — enriquecimiento via web-scrape)
    print("  eInforma CNAE directory...")
    einforma_count = 0
    for co in scrape_einforma():
        d = co.get("domain", "")
        if not d or d in seen or should_skip(d):
            continue
        lead = make_lead(
            co["name"], d, co["sector"],
            "erp", f"eInforma {co['sector']}", "s-erp",
            f"https://www.{d}",
            f"eInforma · {co.get('city','España')} · {co.get('province','')}",
            2, "einforma"
        )
        lead["current_erp"] = "—"
        new.append(lead); seen.add(d); einforma_count += 1
    print(f"    → {einforma_count} empresas de eInforma (CNAE España)")

    # Filtrar por señal ICP ANTES de enriquecer — evitar gastar Apollo/Hunter en leads que caerán igual
    VALID_SIGNALS_ENRICH = {
        "Busca ERP (Apollo)", "Busca ERP", "Selección ERP", "Migración ERP",
        "Empresa logística", "Empresa construcción", "Empresa textil", "Empresa exportadora",
        "Empresa ICP", "Empresa España (Apollo)", "Empresa alimentación", "Fabricante España",
        "Empresa química", "Empresa automoción", "Distribuidor", "Licitación",
        "Partner Odoo", "Partner SAP", "Partner Sage", "Partner Holded",
        "Partner SAP (deep)", "Busca partner",
        "Google Maps",           # Maps siempre tiene señal real
        "Usuario TeamSystem/DistritoK", "Usuario Solmicro/Zucchetti",  # ERP users = migración
    }
    to_enrich = [l for l in new if l.get("signal_label","") in VALID_SIGNALS_ENRICH]
    skip_enrich = len(new) - len(to_enrich)
    if skip_enrich:
        print(f"  Saltando enriquecimiento de {skip_enrich} leads sin señal ICP válida")

    print(f"  Enriqueciendo {len(to_enrich)} leads (de {len(new)} totales)...")
    for i,lead in enumerate(to_enrich):
        c=enrich(lead["domain"])
        lead.update({
            "email":        c.get("email","—"),
            "contact_name": c.get("name","—"),
            "contact_pos":  c.get("position","—"),
            "phone":        c.get("phone", lead.get("phone","—")),
            "linkedin_org": c.get("linkedin", lead.get("linkedin_org","—")),
            "current_erp":  c.get("current_erp","—"),
        })
        if i%5==0: time.sleep(0.3)

    # Acumular preservando histórico — límite 500 para tener más datos
    data["leads_history"]=(new+data["leads_history"])[:500]
    print(f"  Total acumulado en historial: {len(data['leads_history'])}")
    return new

# ── Google Sheets ───────────────────────────────────────────────────────────
def sheets_token():
    if not GOOGLE_SA_JSON: return None
    try:
        import time as t,base64
        sa=json.loads(GOOGLE_SA_JSON)
        hdr=base64.urlsafe_b64encode(json.dumps({"alg":"RS256","typ":"JWT"}).encode()).rstrip(b"=").decode()
        now=int(t.time())
        pay=base64.urlsafe_b64encode(json.dumps({"iss":sa["client_email"],"scope":"https://www.googleapis.com/auth/spreadsheets","aud":"https://oauth2.googleapis.com/token","iat":now,"exp":now+3600}).encode()).rstrip(b"=").decode()
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        key=serialization.load_pem_private_key(sa["private_key"].encode(),password=None)
        sig=base64.urlsafe_b64encode(key.sign(f"{hdr}.{pay}".encode(),padding.PKCS1v15(),hashes.SHA256())).rstrip(b"=").decode()
        r=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"urn:ietf:params:oauth:grant-type:jwt-bearer","assertion":f"{hdr}.{pay}.{sig}"},timeout=15)
        return r.json().get("access_token","")
    except Exception as e:
        print(f"  ⚠️ Sheets token: {e}"); return None

def sheets_append(sheet_id,rng,vals,tok):
    try:
        r=requests.post(f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{rng}:append",params={"valueInputOption":"USER_ENTERED","insertDataOption":"INSERT_ROWS"},headers={"Authorization":f"Bearer {tok}","Content-Type":"application/json"},json={"values":vals},timeout=15)
        r.raise_for_status(); return True
    except Exception as e:
        print(f"  ⚠️ Sheets: {e}"); return False

def sheets_domains(sheet_id,tok):
    try:
        r=requests.get(f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/Leads!B:B",headers={"Authorization":f"Bearer {tok}"},timeout=15)
        r.raise_for_status()
        return {row[0] for row in r.json().get("values",[])[1:] if row}
    except: return set()

def send_digest_email(today_leads):
    if not GMAIL_USER or not GMAIL_PASS:
        print("  ⚠️ Email: faltan GMAIL_USER o GMAIL_PASSWORD_ROCIO"); return
    top = sorted([l for l in today_leads if l["score"]==3], key=lambda x: x.get("email","—")!="—", reverse=True)[:8] or today_leads[:8]
    if not top: print("  ℹ️ Email: sin leads hoy"); return
    rows_html = ""
    for l in top:
        em = l.get("email","—")
        em_cell = f'<a href="mailto:{em}">{em}</a>' if em != "—" else "—"
        sc_color = "#2E7D32" if l["score"]==3 else "#E65100" if l["score"]==2 else "#616161"
        sc_txt = "⭐ Alto" if l["score"]==3 else "▲ Medio" if l["score"]==2 else "▼ Bajo"
        rows_html += f'<tr><td style="padding:8px 12px;border-bottom:1px solid #eee"><b>{l["company"]}</b><br><span style="font-size:11px;color:#888">{l["domain"]}</span></td><td style="padding:8px 12px;border-bottom:1px solid #eee;font-size:12px">{l["signal_label"]}</td><td style="padding:8px 12px;border-bottom:1px solid #eee;font-size:12px;color:{sc_color}">{sc_txt}</td><td style="padding:8px 12px;border-bottom:1px solid #eee;font-size:12px">{em_cell}</td><td style="padding:8px 12px;border-bottom:1px solid #eee;font-size:12px">{l.get("contact_name","—")}</td></tr>'
    html_body = f"""<div style="font-family:Inter,sans-serif;max-width:700px;margin:0 auto">
<div style="background:#EDA100;padding:16px 24px;border-radius:8px 8px 0 0">
  <b style="color:#fff;font-size:18px">Etendo Intelligence — {TODAY}</b>
  <span style="color:#fff;opacity:.8;font-size:13px;margin-left:12px">{len(top)} leads de hoy</span>
</div>
<table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #eee;border-top:none">
  <thead><tr style="background:#f9f9f9">
    <th style="padding:8px 12px;text-align:left;font-size:12px;color:#555">Empresa</th>
    <th style="padding:8px 12px;text-align:left;font-size:12px;color:#555">Señal</th>
    <th style="padding:8px 12px;text-align:left;font-size:12px;color:#555">Score</th>
    <th style="padding:8px 12px;text-align:left;font-size:12px;color:#555">Email</th>
    <th style="padding:8px 12px;text-align:left;font-size:12px;color:#555">Contacto</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<div style="padding:12px 24px;background:#f9f9f9;border:1px solid #eee;border-top:none;border-radius:0 0 8px 8px;font-size:12px;color:#888">
  <a href="https://intel-dashboard.onrender.com" style="color:#EDA100;font-weight:600">Ver dashboard completo →</a>
</div></div>"""
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🟡 Etendo Intel {TODAY} — {len(top)} leads nuevos"
        msg["From"] = GMAIL_USER
        msg["To"] = GMAIL_USER
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASS)
            smtp.send_message(msg)
        print(f"  ✅ Email enviado: {len(top)} leads → {GMAIL_USER}")
    except Exception as e:
        print(f"  ⚠️ Email: {e}")

def save_to_sheets(new_leads,new_changes,sheet_id,tok):
    if not sheet_id or not tok: print("  ℹ️ Sheets no config"); return
    existing=sheets_domains(sheet_id,tok)
    rows=[]; 
    for l in new_leads:
        if l["domain"] in existing: continue
        sc="⭐ Alto" if l["score"]==3 else ("▲ Medio" if l["score"]==2 else "▼ Bajo")
        rows.append([TODAY,l["domain"],l["company"],l["sector"],l["signal_label"],sc,f"https://{l['domain']}",l.get("linkedin_org","—"),l.get("contact_name","—"),l.get("contact_pos","—"),l.get("email","—"),l.get("linkedin_org","—"),l.get("phone","—"),l["source_url"],l["snippet"][:200],l.get("source_type","search"),"Nuevo",""])
    if rows:
        ok=sheets_append(sheet_id,"Leads!A:R",rows,tok)
        print(f"  {'✅' if ok else '⚠️'} Sheets: {len(rows)} leads")
    if new_changes:
        sheets_append(sheet_id,"Competidores!A:E",[[c["date"],c["competitor"],c["section"],c["type"],c["detail"]] for c in new_changes],tok)
    sheets_append(sheet_id,"Resumen!A:F",[[TODAY,len(new_leads),len([l for l in new_leads if l["score"]==3]),len([l for l in new_leads if l.get("email","—")!="—"]),len(new_changes),len([])]],tok)

# ── HTML ────────────────────────────────────────────────────────────────────
def inject(html,mid,val):
    new,n=re.subn(f"<!-- WS:{mid} -->.*?<!-- /WS:{mid} -->",f"<!-- WS:{mid} -->{val}<!-- /WS:{mid} -->",html,flags=re.DOTALL)
    if n==0: print(f"  ⚠️ Marcador: {mid}")
    return new

def src_badge(st):
    return {"brave_search":'<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#E6F1FB;color:#0C447C">Brave</span>',"apollo":'<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#EEEDFE;color:#3C3489">Apollo</span>',"google_maps":'<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#EAF3DE;color:#27500A">Maps</span>',"partner_scraping":'<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#FAEEDA;color:#633806">Partner</span>',"selection":'<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#EEEDFE;color:#3C3489">Selección</span>'}.get(st,"")

def render_comp_cards(results,data):
    html=""
    for cr in results:
        c=cr["comp"]; p=data["prices"].get(c["id"],{}); f=data["features"].get(c["id"],[])
        ch=cr["change"]
        badge={"changed":"⚠️ Cambio","new":"Primera visita","none":"Sin cambios","error":"Sin acceso"}
        bcls={"changed":"warn","new":"ok","none":"ok","error":"no"}
        feats="".join(f'<div class="crow">• {x}</div>' for x in f) or '<div class="crow" style="color:var(--text-muted)">Sin novedades detectadas</div>'
        tiers = p.get("tiers",[])
        if tiers:
            tiers_html = "".join(f'<span style="display:inline-block;margin:2px 4px 2px 0;padding:2px 7px;border-radius:4px;background:var(--gb);font-size:11px"><b>{t["label"]}</b> {t["price"]}</span>' for t in tiers)
            ps_html = f'<div class="crow">Precios públicos:<br>{tiers_html}</div>'
        else:
            ps_html = '<div class="crow" style="color:var(--text-muted)">Precio a consulta — sin tarifa pública</div>'
        bdr=' style="border-color:#EDA100"' if ch=="changed" else ""
        hbg=' style="background:#FAEEDA22"' if ch=="changed" else ""
        html+=f'<div class="ccard"{bdr}><div class="chead"{hbg}><div><p class="cname">{c["name"]}</p><p class="curl">{c["url"].replace("https://","")}</p></div><span class="pill {bcls[ch]}">{badge[ch]}</span></div><div class="cbody">{ps_html}{feats}<div class="crow" style="color:var(--text-muted)">Revisado: {TODAY}</div></div></div>'
    return html

def render_changes(data):
    if not data["changes_history"]: return '<tr><td colspan="5" style="padding:12px;color:var(--text-muted);text-align:center">Sin cambios</td></tr>'
    return "".join(f'<tr><td style="color:var(--text-muted)">{c["date"]}</td><td><b>{c["competitor"]}</b></td><td>{c["section"]}</td><td><span class="pill warn">{c["type"]}</span></td><td style="color:var(--text-secondary)">{c["detail"]}</td></tr>' for c in data["changes_history"][:20])

def render_prices(data):
    rows=""
    for i,c in enumerate(COMPETITORS):
        p=data["prices"].get(c["id"],{}); pub='<span class="pill ok">✓ Sí</span>' if p.get("publishes") else '<span class="pill no">No publica</span>'
        bg=' style="background:var(--surface-1)"' if i%2 else ""
        rows+=f'<tr{bg}><td><b>{c["name"]}</b></td><td><span class="ptag">{p.get("basic","—")}</span></td><td><span class="ptag">{p.get("mid","—")}</span></td><td><span class="ptag">{p.get("advanced","—")}</span></td><td>{pub}</td><td style="color:var(--text-muted)">{TODAY}</td></tr>'
    return rows

def render_features(data):
    html=""
    for c in COMPETITORS:
        fs=data["features"].get(c["id"],[])
        if fs: html+=f'<div class="acard b" style="margin-bottom:6px"><b>{c["name"]}</b> — {" · ".join(fs)}</div>'
    return html or '<div style="color:var(--text-muted);font-size:12px;padding:8px">Sin novedades hoy</div>'

def _age_badge(lead_date_str):
    try:
        d = datetime.datetime.strptime(lead_date_str, "%d/%m/%Y")
        days = (datetime.datetime.today() - d).days
        if days == 0:
            return '<span style="font-size:9px;padding:1px 6px;border-radius:3px;background:#E8F5E9;color:#2E7D32;font-weight:700">Nuevo</span>'
        return f'<span style="font-size:9px;color:var(--text-muted)">{days}d</span>'
    except Exception:
        return ""

PARTNER_SOURCES = {"partner_scraping","partner_spider"}

def render_leads(leads):
    direct = [l for l in leads if l.get("source_type","") not in PARTNER_SOURCES]
    if not direct: return '<tr><td colspan="10" style="padding:16px;text-align:center;color:var(--text-muted)">Sin leads directos</td></tr>'
    rows=""
    for l in direct[:100]:
        sc=l["score"]; sc_cls="sc-h" if sc==3 else("sc-m" if sc==2 else"sc-l"); sc_txt="⭐ Alto" if sc==3 else("▲ Medio" if sc==2 else"▼ Bajo")
        em=l.get("email","—"); em_h=f'<a href="mailto:{em}" class="lnk">{em[:22]}{"…" if len(em)>22 else ""}</a>' if em!="—" else '<span style="color:var(--text-muted)">—</span>'
        age=_age_badge(l.get("date",""))
        rows+=f'<tr data-s="{l["signal"]}" data-q="{"h" if sc==3 else("m" if sc==2 else"l")}" data-src="{l.get("source_type","")}">'
        rows+=f'<td><b>{l["company"]}</b>{age}<div style="font-size:10px;color:var(--text-muted)">{l["domain"]}</div></td>'
        rows+=f'<td style="color:var(--text-secondary);font-size:11px">{l["sector"]}</td>'
        rows+=f'<td><span class="sig {l["signal_class"]}">{l["signal_label"]}</span>{src_badge(l.get("source_type",""))}</td>'
        rows+=f'<td><span class="{sc_cls}">{sc_txt}</span></td>'
        rows+=f'<td style="font-size:11px">{l.get("contact_name","—")}<div style="font-size:10px;color:var(--text-muted)">{l.get("contact_pos","—")}</div></td>'
        rows+=f'<td style="font-size:11px">{em_h}</td>'
        rows+=f'<td style="font-size:11px">{l.get("phone","—")}</td>'
        rows+=f'<td><a href="https://{l["domain"]}" target="_blank" class="lnk">Web</a></td>'
        rows+=f'<td><a href="{l["source_url"]}" target="_blank" class="lnk">Fuente</a></td>'
        rows+=f'<td style="font-size:10px;color:var(--text-muted)">{l["date"]}</td></tr>'
    return rows

def render_partner_leads(leads):
    partners = [l for l in leads if l.get("source_type","") in PARTNER_SOURCES]
    if not partners: return '<tr><td colspan="7" style="padding:16px;text-align:center;color:var(--text-muted)">Sin partners detectados</td></tr>'
    rows=""
    for l in partners[:60]:
        sc=l["score"]; sc_cls="sc-h" if sc==3 else("sc-m" if sc==2 else"sc-l")
        age=_age_badge(l.get("date",""))
        comp=l.get("snippet","").split("—")[0].replace("Partner ","").strip()[:30] or "—"
        em=l.get("email","—"); em_h=f'<a href="mailto:{em}" class="lnk">{em[:22]}{"…" if len(em)>22 else ""}</a>' if em!="—" else '<span style="color:var(--text-muted)">—</span>'
        rows+=f'<tr>'
        rows+=f'<td><b>{l["company"]}</b>{age}<div style="font-size:10px;color:var(--text-muted)">{l["domain"]}</div></td>'
        rows+=f'<td style="font-size:11px;color:var(--text-secondary)">{comp}</td>'
        rows+=f'<td style="font-size:11px">{l.get("sector","—")}</td>'
        rows+=f'<td><span class="{sc_cls}" style="font-size:10px">{"⭐" if sc==3 else "▲" if sc==2 else "▼"}</span></td>'
        rows+=f'<td style="font-size:11px">{em_h}</td>'
        rows+=f'<td><a href="https://{l["domain"]}" target="_blank" class="lnk">Web</a></td>'
        rows+=f'<td style="font-size:10px;color:var(--text-muted)">{l["date"]}</td></tr>'
    return rows

def render_preview(leads):
    p=[l for l in leads if l["score"]==3][:5] or leads[:5]
    if not p: return '<tr><td colspan="6" style="padding:12px;color:var(--text-muted);text-align:center">Sin leads nuevos</td></tr>'
    rows=""
    for l in p:
        sc_cls="sc-h" if l["score"]==3 else("sc-m" if l["score"]==2 else"sc-l"); sc_txt="⭐ Alto" if l["score"]==3 else("▲ Medio" if l["score"]==2 else"▼ Bajo")
        em=l.get("email","—"); em_h=f'<a href="mailto:{em}" class="lnk">{em[:20]}</a>' if em!="—" else "—"
        rows+=f'<tr><td><b>{l["company"]}</b></td><td><span class="sig {l["signal_class"]}">{l["signal_label"]}</span></td><td><span class="{sc_cls}">{sc_txt}</span></td><td style="font-size:11px">{em_h}</td><td><a href="https://{l["domain"]}" target="_blank" class="lnk">Web</a></td><td style="font-size:10px;color:var(--text-muted)">{l["date"]}</td></tr>'
    return rows

def render_engagement(data):
    posts = data.get("engagement_history", [])
    if not posts:
        return '<tr><td colspan="5" style="padding:16px;text-align:center;color:var(--text-muted)">Sin posts detectados aún — ejecuta el script para buscar oportunidades</td></tr>'
    rows = ""
    for p in posts[:50]:
        draft   = p.get("comment_draft", "")
        score   = p.get("relevance_score", "—")
        url     = p.get("url","").replace('"','&quot;')
        title   = p.get("title","")
        draft_escaped = draft.replace("`","\\`").replace("${","$\\{")
        draft_html = f'<details style="margin-top:4px"><summary style="cursor:pointer;font-size:10px;color:var(--text-muted)">Ver borrador</summary><div id="draft-{hash(url)}" style="margin-top:6px;padding:8px;background:var(--gb);border-radius:4px;font-size:11px;line-height:1.5;white-space:pre-wrap">{draft}</div><button onclick="engCopy(`{draft_escaped}`)" style="margin-top:4px;font-size:10px;padding:2px 8px;border:1px solid var(--gb);border-radius:4px;cursor:pointer;background:var(--gb)">📋 Copiar borrador</button></details>' if draft else '<span style="font-size:10px;color:var(--text-muted)">—</span>'
        label_badge = f'<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#E6F1FB;color:#0C447C">{p.get("label","")}</span>'
        rows += f'''<tr data-eng-url="{url}">
          <td style="padding:8px 10px;max-width:220px">
            <a href="{url}" target="_blank" class="lnk" style="font-size:12px;font-weight:600">{title[:60]}{"…" if len(title)>60 else ""}</a>
            <div style="font-size:10px;color:var(--text-muted)">{p.get("domain","")}</div>
          </td>
          <td style="padding:8px 10px">{label_badge}</td>
          <td style="padding:8px 10px;font-size:11px">{score}/10</td>
          <td style="padding:8px 10px;min-width:120px">
            <div class="eng-status"></div>
            <div class="eng-actions" style="margin-top:4px"></div>
          </td>
          <td style="padding:8px 10px;max-width:300px">{draft_html}</td>
        </tr>'''
    return rows

def render_alerts(changes,new_leads):
    html=""
    for c in changes: html+=f'<div class="acard warn"><b>⚠️ {c["competitor"]}</b> — {c["detail"]}</div>'
    high=[l for l in new_leads if l["score"]==3]
    if high: html+=f'<div class="acard g"><b>⭐ {len(high)} leads de alta calidad</b> — {", ".join(l["company"] for l in high[:3])}</div>'
    em=[l for l in new_leads if l.get("email","—")!="—"]
    if em: html+=f'<div class="acard b"><b>📧 {len(em)} leads con email verificado</b></div>'
    mp=[l for l in new_leads if l.get("source_type")=="google_maps"]
    if mp: html+=f'<div class="acard b"><b>📍 {len(mp)} empresas via Google Maps</b></div>'
    pt=[l for l in new_leads if l.get("source_type")=="partner_scraping"]
    if pt: html+=f'<div class="acard" style="border-left-color:#EDA100"><b>🤝 {len(pt)} partners de competidores</b></div>'
    return html or '<div style="padding:10px;color:var(--text-muted);font-size:12px">Sin alertas nuevas</div>'

# ── Supabase sync ───────────────────────────────────────────────────────────
def save_to_supabase(new_leads):
    """
    Escribe los leads nuevos en Supabase (contacts + deals) para que aparezcan
    en el tab Outreach del dashboard. Solo leads con email verificado o score >= 2.
    Usa upsert por email (contacts) para evitar duplicados.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("  ⚠️  SUPABASE_URL / SUPABASE_SERVICE_KEY no configurados — skip")
        return

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation,resolution=merge-duplicates",
    }
    base = SUPABASE_URL.rstrip("/") + "/rest/v1"

    def _domain_ok(domain):
        d = domain.replace("www.", "").lower()
        return not any(skip in d for skip in SKIP_DOMAINS)

    # Solo leads con email O teléfono, dominio limpio y señal ICP/partner válida
    # Google Maps da teléfono directo del negocio — igual de contactable que email
    VALID_SIGNALS = {
        "Busca ERP (Apollo)", "Busca ERP", "Selección ERP", "Migración ERP",
        "Empresa logística", "Empresa construcción", "Empresa textil", "Empresa exportadora",
        "Empresa ICP", "Empresa España (Apollo)", "Empresa alimentación", "Fabricante España",
        "Empresa química", "Empresa automoción", "Distribuidor", "Licitación",
        "Partner Odoo", "Partner SAP", "Partner Sage", "Partner Holded",
        "Partner SAP (deep)", "Busca partner",
        "Google Maps",
        # Fuentes nuevas con email/tel pre-enriquecido
        "Empresite Industrial", "Empresite Logística", "Empresite Construcción",
        "Empresite Alimentación", "Empresite Química", "Empresite Textil",
        "Empresite Automoción", "Empresite Metalurgia", "Empresite Distribución",
        "Empresite Farmacéutico", "Empresite Madera/Mueble",
        # ERP users (Distrito K, Solmicro, Davisa, etc.) — prospectos de migración score 3
        "Usuario TeamSystem/DistritoK", "Usuario Solmicro/Zucchetti",
        "Usuario MS Dynamics", "Usuario SAP Business One",
    }
    candidates = [
        l for l in new_leads
        if (l.get("email", "—") != "—" or l.get("phone", "—") != "—")
        and _domain_ok(l.get("domain", ""))
        and (l.get("signal_label", "") in VALID_SIGNALS
             or l.get("signal_label", "").startswith("Empresite "))
    ]
    if not candidates:
        print(f"  → Supabase: 0 leads con contacto — nada que sincronizar")
        return

    _BAD_COMPANY = re.compile(
        r"(odoo|erp|sap|sage|partner|consultor|logístic|inicio|home|"
        r"implementar|software|gestión|programa|guía|consejos|qué es|cuál es|"
        r"silver|gold|platinum|📈|〖|página)", re.IGNORECASE
    )

    def _clean_company(name, domain):
        if not name or name in ("—", "Inicio", "Home") or _BAD_COMPANY.search(name) or len(name) > 50:
            base = domain.replace("www.", "").split(".")[0]
            base = re.sub(r"([a-z])([A-Z])", r"\1 \2", base).replace("-", " ").replace("_", " ")
            return base.title()
        return name

    NON_DECISOR = {
        "conductor", "operario", "técnico", "técnica", "analista", "desarrollador",
        "programador", "administrativo", "becario", "intern", "junior", "assistant",
        "asistente", "auxiliar", "agente", "recepcionista", "contable", "controller",
        "talent acquisition", "recruiter", "reclutador", "soporte", "support",
        "helpdesk", "customer success", "account executive", "ejecutivo de cuentas",
    }

    def _is_decisor(pos):
        if not pos or pos in ("—", ""):
            return True  # sin dato → no filtrar, puede ser decisor
        pos_low = pos.lower()
        return not any(k in pos_low for k in NON_DECISOR)

    created = 0
    skipped = 0
    for lead in candidates:
        email   = lead.get("email", "").strip().lower()
        company = _clean_company(lead.get("company", ""), lead.get("domain", ""))
        domain  = lead.get("domain", "")
        sector  = lead.get("sector", "") or lead.get("signal_label", "")
        score   = lead.get("score", 1)
        pos     = lead.get("contact_pos", "")

        if not _is_decisor(pos):
            skipped += 1
            continue

        contact_payload = {
            "nombre":          lead.get("contact_name") or company,
            "email":           email,
            "empresa":         company,
            "fuente":          OUTREACH_SOURCE,
            "notas_internas":  (
                f"Detectado por Intel Dashboard · {lead.get('date','')} · "
                f"{lead.get('signal_label','')} · Fuente: {lead.get('source_url','')}"
            ),
            "custom_fields": {
                "sector":       sector,
                "score":        score,
                "domain":       domain,
                "signal_label": lead.get("signal_label", ""),
                "source_type":  lead.get("source_type", ""),
                "linkedin":     lead.get("linkedin", "—"),
                "phone":        lead.get("phone", "—"),
                "position":     lead.get("contact_pos", "—"),
                "current_erp":  lead.get("current_erp", "—"),
                "detected_at":  datetime.datetime.now().isoformat(),
            },
        }

        try:
            # Verificar si ya existe un contacto con ese email
            existing = requests.get(
                f"{base}/contacts",
                headers={**headers, "Prefer": ""},
                params={"email": f"eq.{email}", "fuente": f"eq.{OUTREACH_SOURCE}", "select": "id"},
                timeout=10,
            )
            if existing.status_code == 200 and existing.json():
                # Ya existe — actualizar signal_label en custom_fields y saltar
                contact_id = existing.json()[0]["id"]
                requests.patch(
                    f"{base}/contacts?id=eq.{contact_id}",
                    headers={**headers, "Prefer": "return=minimal"},
                    json={"custom_fields": contact_payload["custom_fields"]},
                    timeout=10,
                )
                skipped += 1
                # Igual verificar si tiene deal
            else:
                # Nuevo contacto
                r = requests.post(
                    f"{base}/contacts",
                    headers=headers,
                    json=contact_payload,
                    timeout=10,
                )
                if r.status_code not in (200, 201):
                    print(f"    ⚠️  contact insert {email}: {r.status_code} {r.text[:120]}")
                    skipped += 1
                    continue
                contact_id = r.json()[0]["id"] if r.json() else None
                if not contact_id:
                    skipped += 1
                    continue

            # Crear deal en stage "Nuevo Lead" solo si no existe uno ya
            check = requests.get(
                f"{base}/deals?contact_id=eq.{contact_id}&select=id",
                headers={**headers, "Prefer": ""},
                timeout=10,
            )
            if check.status_code == 200 and check.json():
                skipped += 1
                continue

            deal_payload = {
                "nombre":      f"{company} · Intel Dashboard",
                "contact_id":  contact_id,
                "empresa":     company,
                "pipeline_id": PIPELINE_ID,
                "stage_id":    STAGE_NUEVO_LEAD,
                "prioridad":   "alta" if score == 3 else ("media" if score == 2 else "baja"),
                "valor":       0,
                "moneda":      "EUR",
                "prob_cierre": 0,
                "estado":      "open",
            }
            dr = requests.post(
                f"{base}/deals",
                headers={**headers, "Prefer": "return=representation"},
                json=deal_payload,
                timeout=10,
            )
            if dr.status_code in (200, 201):
                created += 1
            else:
                print(f"    ⚠️  deal {email}: {dr.status_code} {dr.text[:120]}")
                skipped += 1

        except Exception as e:
            print(f"    ⚠️  Supabase error {email}: {e}")
            skipped += 1

    print(f"  → Supabase: {created} leads nuevos · {skipped} ignorados/errores")


def count_supabase_outreach():
    """Devuelve el total de contactos en Supabase con fuente=intel_dashboard."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return 0
    try:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Prefer": "count=exact",
            "Range": "0-0",
        }
        r = requests.get(
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/contacts?fuente=eq.intel_dashboard&select=id",
            headers=headers, timeout=10,
        )
        cr = r.headers.get("Content-Range", "")  # "0-0/26"
        total = int(cr.split("/")[-1]) if "/" in cr else 0
        return total
    except Exception as e:
        print(f"  ⚠️ count_supabase_outreach: {e}")
        return 0


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*55}\nEtendo Intelligence — {NOW}\n{'='*55}")
    data=load_data()
    comp_results,new_changes=scrape_competitors(data)
    new_leads=search_all_leads(data)
    search_engagement_posts(data)
    save_data(data)
    print("→ Exportando engagement JSON...")
    export_engagement_json(data["engagement_history"])
    if GOOGLE_SHEET_ID:
        print("→ Google Sheets...")
        tok=sheets_token()
        if tok: save_to_sheets(new_leads,new_changes,GOOGLE_SHEET_ID,tok)
        else: print("  ⚠️ Configura GOOGLE_SERVICE_ACCOUNT_JSON")
    print("→ Supabase Outreach...")
    save_to_supabase(new_leads)
    outreach_total = count_supabase_outreach()
    print(f"  → Total en Outreach Supabase: {outreach_total}")
    print("→ HTML...")
    html=open(HTML_FILE,encoding="utf-8").read()
    by_src={}
    for l in new_leads: by_src[l.get("source_type","?")] = by_src.get(l.get("source_type","?"),0)+1
    html=inject(html,"generated_at",NOW); html=inject(html,"footer_date",TODAY)
    html=inject(html,"leads_total",str(len(data["leads_history"])))
    html=inject(html,"leads_new_today",str(len(new_leads)))
    html=inject(html,"leads_high",str(len([l for l in data["leads_history"] if l["score"]==3])))
    html=inject(html,"leads_email",str(len([l for l in data["leads_history"] if l.get("email","—")!="—"])))
    html=inject(html,"outreach_total",str(outreach_total))
    html=inject(html,"changes_total",str(len(new_changes)))
    html=inject(html,"ALERTS",render_alerts(new_changes,new_leads))
    today_leads=[l for l in data["leads_history"] if l.get("date","")==TODAY]
    html=inject(html,"LEADS_PREVIEW",render_preview(new_leads or today_leads))
    html=inject(html,"COMP_CARDS",render_comp_cards(comp_results,data))
    html=inject(html,"CHANGES_HISTORY",render_changes(data))
    html=inject(html,"PRICES_ROWS",render_prices(data))
    html=inject(html,"FEATURES",render_features(data))
    html=inject(html,"LEADS_ROWS",render_leads(data["leads_history"]))
    html=inject(html,"PARTNER_ROWS",render_partner_leads(data["leads_history"]))
    engagement_posts = data.get("engagement_history",[])
    html=inject(html,"ENGAGEMENT_ROWS",render_engagement(data))
    html=inject(html,"engagement_total",str(len(engagement_posts)))
    html=inject(html,"engagement_ready",str(len([p for p in engagement_posts if p.get("comment_ready")])))
    print("→ Email digest...")
    send_digest_email(today_leads)
    open(HTML_FILE,"w",encoding="utf-8").write(html)
    print(f"\n✅ {NOW}")
    print(f"   Leads nuevos: {len(new_leads)} {by_src}")
    print(f"   Con email: {len([l for l in new_leads if l.get('email','—')!='—'])}")
    print(f"   Alta calidad: {len([l for l in new_leads if l['score']==3])}")
    print(f"   Cambios: {len(new_changes)} · Histórico: {len(data['leads_history'])}")

if __name__=="__main__":
    main()
