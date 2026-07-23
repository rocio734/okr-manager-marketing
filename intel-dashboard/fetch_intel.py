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

REPO_DIR  = Path(__file__).parent
HTML_FILE = REPO_DIR / "index.html"
DATA_FILE = REPO_DIR / "intel_data.json"

BRAVE_API_KEY   = os.environ.get("BRAVE_API_KEY","")
GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY","")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID","")
GOOGLE_SA_JSON  = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON","")
HUNTER_KEY      = os.environ.get("HUNTER_API_KEY","")
APOLLO_KEY      = os.environ.get("APOLLO_API_KEY","")

TODAY = datetime.date.today().strftime("%d/%m/%Y")
NOW   = datetime.datetime.now().strftime("%d/%m/%Y %H:%M UTC")
HEADERS = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36","Accept-Language":"es-ES,es;q=0.9"}

COMPETITORS = [
    {"name":"Odoo","id":"odoo","url":"https://www.odoo.com/es","pricing_url":"https://www.odoo.com/es/pricing","blog_url":"https://www.odoo.com/es/blog","partners_url":"https://www.odoo.com/es/partners"},
    {"name":"SAP Business One","id":"sap","url":"https://www.sap.com/spain/products/erp/business-one.html","pricing_url":"https://www.sap.com/spain/products/erp/business-one.html","blog_url":"https://www.sap.com/spain/products/erp/business-one.html","partners_url":"https://www.sap.com/spain/partner-finder.html"},
    {"name":"Holded","id":"holded","url":"https://www.holded.com","pricing_url":"https://www.holded.com/es/precios","blog_url":"https://www.holded.com/es/blog","partners_url":"https://www.holded.com/es/partners"},
    {"name":"Sage","id":"sage","url":"https://www.sage.com/es-es/erp/","pricing_url":"https://www.sage.com/es-es/erp/","blog_url":"https://www.sage.com/es-es/blog/","partners_url":"https://www.sage.com/es-es/partners/find-a-partner/"},
]

LEAD_SEARCHES = [
    {"query":'"implementar ERP" OR "implantar ERP" España pyme 2025 OR 2026',"signal":"erp","signal_label":"Busca ERP","signal_class":"s-erp"},
    {"query":'"migrar de Odoo" OR "migrar de Sage" OR "migrar de SAP" España empresa',"signal":"migrate","signal_label":"Migración ERP","signal_class":"s-mig"},
    {"query":'"partner ERP" OR "consultor ERP" España selección proveedor pyme',"signal":"partner","signal_label":"Busca partner","signal_class":"s-par"},
    {"query":'"selección ERP" OR "evaluar ERP" OR "comparar ERP" España empresa 2025 OR 2026',"signal":"selection","signal_label":"Selección ERP","signal_class":"s-sel"},
]

MAPS_SEARCHES = [
    {"query":"empresa industrial manufacturing Spain","sector":"Industrial"},
    {"query":"empresa logistica distribucion Spain","sector":"Logística"},
    {"query":"empresa servicios profesionales Spain","sector":"Servicios"},
    {"query":"empresa construccion ingenieria Spain","sector":"Construcción"},
    {"query":"empresa alimentacion retail Spain","sector":"Retail"},
]

SPAIN_CITIES = ["Madrid,Spain","Barcelona,Spain","Valencia,Spain","Bilbao,Spain","Zaragoza,Spain","Sevilla,Spain"]

SKIP_DOMAINS = {"odoo","sap","sage","holded","linkedin","infojobs","wikipedia","youtube","google","bing","microsoft","facebook","twitter"}

def fetch(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"    ⚠️ {url[:60]}: {e}")
        return ""

def text_hash(t): return hashlib.md5(t.encode()).hexdigest()[:12]

def clean(html):
    soup = BeautifulSoup(html,"html.parser")
    for t in soup(["script","style","nav","footer","header"]): t.decompose()
    return " ".join(soup.get_text().split())[:5000]

def extract_prices(html):
    p = {"basic":"—","mid":"—","advanced":"—","publishes":False}
    found = re.findall(r'(\d+[\.,]?\d*)\s*€\s*(?:/\s*(?:mes|month|usuario|user))?', BeautifulSoup(html,"html.parser").get_text(), re.I)
    if found:
        p["publishes"] = True
        u = sorted(set(float(x.replace(",",".")) for x in found if float(x.replace(",","."))>0))
        if u: p["basic"] = f"€{u[0]:.0f}/mes"
        if len(u)>1: p["mid"] = f"€{u[len(u)//2]:.0f}/mes"
        if len(u)>2: p["advanced"] = f"€{u[-1]:.0f}/mes"
    return p

def extract_features(html):
    soup = BeautifulSoup(html,"html.parser")
    out=[]
    for tag in soup.find_all(["h2","h3","h4"],limit=10):
        t=tag.get_text(strip=True)
        if len(t)>20 and any(k in t.lower() for k in ["ia","inteligencia","nuevo","nueva","lanza","mejora","feature","agent","copilot","update","automatiz"]):
            out.append(t[:120])
    return out[:4]

def detect_sector(text):
    t=text.lower()
    if any(w in t for w in ["industr","manufactur","fabricac","taller","metalurg"]): return "Industrial"
    if any(w in t for w in ["logístic","distribuc","almacén","transport","cadena"]): return "Logística"
    if any(w in t for w in ["servicios","consultor","asesor","despacho","gestor"]): return "Servicios"
    if any(w in t for w in ["telecom","tecnolog","software","digital","informátic"]): return "Tech/Telecom"
    if any(w in t for w in ["retail","comercio","tienda","alimentac","bebida"]): return "Retail"
    if any(w in t for w in ["construcc","ingeniería","obra","arquitect","inmobil"]): return "Construcción"
    return "General"

def score_lead(text, signal):
    s=1
    if any(w in text for w in ["busca","necesita","implantación","migrar","proyecto erp","selección","licitación"]): s+=1
    if any(w in text for w in ["empresa","pyme","s.l.","s.a.","grupo","industrial","factory","ltd"]): s+=1
    if any(w in text for w in ["blog","artículo","guía","cómo elegir","qué es","comparativa"]): s-=1
    if signal in ["migrate","partner"]: s+=1
    return max(1,min(3,s))

def domain_from_url(url):
    return re.sub(r'^https?://(www\.)?','',url).split('/')[0].split('?')[0].strip().lower()

def make_lead(company,domain,sector,signal,label,cls,src_url,snippet,score,source_type="search"):
    return {"company":company,"domain":domain,"sector":sector,"signal":signal,"signal_label":label,"signal_class":cls,"source_url":src_url,"snippet":snippet[:150],"score":score,"date":TODAY,"source_type":source_type,"email":"—","contact_name":"—","contact_pos":"—","phone":"—","linkedin_org":"—"}

def should_skip(domain):
    return not domain or any(s in domain for s in SKIP_DOMAINS)

# ── APIs ────────────────────────────────────────────────────────────────────
def brave_search(query, num=5):
    if not BRAVE_API_KEY: return []
    try:
        r=requests.get("https://api.search.brave.com/res/v1/web/search",
            headers={"Accept":"application/json","Accept-Encoding":"gzip","X-Subscription-Token":BRAVE_API_KEY},
            params={"q":query,"count":min(num,20),"country":"es","search_lang":"es","text_decorations":0},timeout=15)
        r.raise_for_status()
        return [{"title":i.get("title",""),"url":i.get("url",""),"snippet":i.get("description","")} for i in r.json().get("web",{}).get("results",[])]
    except Exception as e:
        print(f"    ⚠️ Brave Search: {e}")
        return []

def apollo_companies():
    if not APOLLO_KEY: return []
    try:
        r=requests.post("https://api.apollo.io/v1/mixed_companies/search",
            headers={"Content-Type":"application/json","X-Api-Key":APOLLO_KEY},
            json={"page":1,"per_page":15,"organization_locations":["Spain"],
                  "organization_num_employees_ranges":["11,200"],
                  "industries":["Manufacturing","Logistics and Supply Chain","Wholesale","Construction","Food & Beverages","Textiles"]},timeout=20)
        r.raise_for_status()
        return r.json().get("organizations",[])
    except Exception as e:
        print(f"    ⚠️ Apollo companies: {e}")
        return []

def apollo_contact(domain):
    if not APOLLO_KEY: return {}
    try:
        r=requests.post("https://api.apollo.io/v1/mixed_people/search",
            headers={"Content-Type":"application/json","X-Api-Key":APOLLO_KEY},
            json={"page":1,"per_page":1,"organization_domains":[domain],
                  "person_titles":["CEO","Director General","Director Operaciones","CTO","Gerente","Owner","Founder"]},timeout=20)
        r.raise_for_status()
        pp=r.json().get("people",[])
        if not pp: return {}
        p=pp[0]
        return {"email":p.get("email","—") or "—","name":f"{p.get('first_name','')} {p.get('last_name','')}".strip(),"position":p.get("title","—"),"linkedin":p.get("linkedin_url","—") or "—","phone":(p.get("phone_numbers") or [{}])[0].get("raw_number","—")}
    except Exception as e:
        print(f"    ⚠️ Apollo contact {domain}: {e}")
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

def enrich(domain):
    c={"email":"—","name":"—","position":"—","linkedin":"—","phone":"—"}
    if APOLLO_KEY:
        a=apollo_contact(domain)
        if a.get("email","—")!="—": c.update(a); return c
    if HUNTER_KEY:
        h=hunter_search(domain)
        if h.get("email","—")!="—": c.update(h); return c
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

def scrape_partners():
    all_p=[]
    # Odoo partners España — extraer nombre + URL externa del partner
    html=fetch("https://www.odoo.com/es/partners")
    if html:
        soup=BeautifulSoup(html,"html.parser")
        # Buscar cards de partners con su web externa
        for card in soup.select("[class*='partner']"):
            name_el = card.find(["h3","h4","strong","[class*='name']"])
            web_el  = card.find("a",href=re.compile(r"^https?://(?!.*odoo.com)"))
            name = name_el.get_text(strip=True) if name_el else ""
            web  = web_el.get("href","") if web_el else ""
            if name and 5<len(name)<70:
                d = domain_from_url(web) if web else ""
                all_p.append({"name":name,"competitor":"Odoo","url":web or "https://www.odoo.com/es/partners","domain":d})
        # Fallback: links de páginas de country o partner (no genéricos)
        if not all_p:
            for a in soup.find_all("a",href=re.compile(r"^https?://(?!.*odoo.com)")):
                t=a.get_text(strip=True)
                h=a.get("href","")
                d=domain_from_url(h)
                if 5<len(t)<70 and d and not should_skip(d) and any(k in h for k in ["country","partner","reseller"]):
                    all_p.append({"name":t,"competitor":"Odoo","url":h,"domain":d})
    print(f"    Odoo partners: {len(all_p)}")

    # Holded partners
    html=fetch("https://www.holded.com/es/partners")
    holded_count=0
    if html:
        soup=BeautifulSoup(html,"html.parser")
        # Buscar links externos (webs de partners)
        for a in soup.find_all("a",href=re.compile(r"^https?://(?!.*holded.com)")):
            t=a.get_text(strip=True)
            h=a.get("href","")
            d=domain_from_url(h)
            if 5<len(t)<70 and d and not should_skip(d):
                all_p.append({"name":t,"competitor":"Holded","url":h,"domain":d})
                holded_count+=1
        # Fallback: elementos con clase partner
        if holded_count==0:
            for el in soup.select("h2,h3,[class*='partner'],[class*='agency']")[:20]:
                t=el.get_text(strip=True)
                if 5<len(t)<70:
                    all_p.append({"name":t,"competitor":"Holded","url":"https://www.holded.com/es/partners","domain":""})
    print(f"    Holded partners: {holded_count}")

    # SAP partners via Google
    sap_results=brave_search('"partner SAP Business One" España consultor implementador', num=5)
    for r in sap_results:
        d=domain_from_url(r["url"])
        if d and not should_skip(d) and "sap.com" not in d:
            all_p.append({"name":r["title"].split("|")[0].strip(),"competitor":"SAP","url":r["url"],"domain":d})

    # Sage partners via Google
    sage_results=brave_search('"partner Sage" España consultor ERP autorizado', num=5)
    for r in sage_results:
        d=domain_from_url(r["url"])
        if d and not should_skip(d) and "sage.com" not in d:
            all_p.append({"name":r["title"].split("|")[0].strip(),"competitor":"Sage","url":r["url"],"domain":d})

    print(f"    Total partners antes de filtrar: {len(all_p)}")
    return all_p[:40]

# ── Datos históricos ────────────────────────────────────────────────────────
def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE) as f: return json.load(f)
    return {"comp_hashes":{},"changes_history":[],"leads_history":[],"prices":{},"features":{}}

def save_data(data):
    with open(DATA_FILE,"w") as f: json.dump(data,f,ensure_ascii=False,indent=2)

# ── Scraping competidores ───────────────────────────────────────────────────
def scrape_competitors(data):
    print("→ Competidores...")
    results,changes=[],[]
    for comp in COMPETITORS:
        print(f"  {comp['name']}...")
        html=fetch(comp["url"])
        if not html:
            results.append({"comp":comp,"change":"error","prices":{},"features":[]}); continue
        h=text_hash(clean(html)); ph=data["comp_hashes"].get(comp["id"],"")
        changed=h!=ph and ph!=""; is_new=ph==""
        data["comp_hashes"][comp["id"]]=h
        ph2=fetch(comp["pricing_url"]) if comp["pricing_url"]!=comp["url"] else html
        prices=extract_prices(ph2 or html); data["prices"][comp["id"]]=prices
        bh=fetch(comp["blog_url"]) if comp["blog_url"]!=comp["url"] else html
        features=extract_features(bh or html); data["features"][comp["id"]]=features
        if changed:
            e={"date":TODAY,"competitor":comp["name"],"section":"Web principal","type":"Contenido modificado","detail":f"Cambios en {comp['url']}"}
            data["changes_history"].insert(0,e); changes.append(e)
        results.append({"comp":comp,"change":"new" if is_new else ("changed" if changed else "none"),"prices":prices,"features":features})
    return results,changes

# ── Búsqueda de leads ───────────────────────────────────────────────────────
def search_all_leads(data):
    print("→ Buscando leads...")
    new=[]; seen={l.get("domain","") for l in data["leads_history"]}

    # 1. Google Custom Search
    print("  [1/4] Google Search...")
    for s in LEAD_SEARCHES:
        for r in brave_search(s["query"],num=5):
            d=domain_from_url(r["url"])
            if d in seen or should_skip(d): continue
            co=r["title"].split("|")[0].split("-")[0].strip()
            if len(co)>60: co=d
            txt=r["title"]+r["snippet"]
            lead=make_lead(co,d,detect_sector(txt),s["signal"],s["signal_label"],s["signal_class"],r["url"],r["snippet"],score_lead(txt.lower(),s["signal"]),"brave_search")
            new.append(lead); seen.add(d)

    # 2. Apollo empresas
    print("  [2/4] Apollo.io...")
    for org in apollo_companies():
        d=domain_from_url(org.get("primary_domain","") or org.get("website_url","") or "")
        if not d or d in seen or should_skip(d): continue
        name=org.get("name","") or d
        emp=org.get("num_employees",0) or 0
        sector=detect_sector((org.get("industry","") or "")+" "+name)
        lead=make_lead(name,d,sector,"erp","Busca ERP (Apollo)","s-erp",f"https://{d}",f"Apollo.io · {emp} empleados · {org.get('industry','—')}",2 if 15<=emp<=150 else 1,"apollo")
        lead["linkedin_org"]=org.get("linkedin_url","—") or "—"
        new.append(lead); seen.add(d)

    # 3. Google Maps
    print("  [3/4] Google Maps...")
    maps_count=0
    if GOOGLE_MAPS_KEY:
        print(f"    Usando Maps key: {GOOGLE_MAPS_KEY[:8]}...")
        for ms in MAPS_SEARCHES[:3]:
            for city in SPAIN_CITIES[:3]:
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

    # 4. Partners competidores
    print("  [4/4] Partners...")
    raw_partners=scrape_partners()
    for p in raw_partners:
        d=p.get("domain","")
        if not d:
            res=brave_search(f'"{p["name"]}" España ERP consultoría',num=1)
            if res: d=domain_from_url(res[0]["url"])
        if not d or d in seen or should_skip(d): continue
        lead=make_lead(p["name"],d,"Consultoría ERP","partner",f"Partner {p['competitor']}","s-par",p.get("url",f"https://{d}"),f"Partner certificado de {p['competitor']} en España",2,"partner_scraping")
        new.append(lead); seen.add(d)

    # Enriquecimiento
    print(f"  Enriqueciendo {len(new)} leads...")
    for i,lead in enumerate(new):
        c=enrich(lead["domain"])
        lead.update({"email":c.get("email","—"),"contact_name":c.get("name","—"),"contact_pos":c.get("position","—"),"phone":c.get("phone",lead.get("phone","—")),"linkedin_org":c.get("linkedin",lead.get("linkedin_org","—"))})
        if i%5==0: time.sleep(0.3)

    data["leads_history"]=(new+data["leads_history"])[:300]
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
        feats="".join(f'<div class="crow">• {x}</div>' for x in f) or '<div class="crow" style="color:var(--text-muted)">Sin novedades</div>'
        ps=f"{p.get('basic','—')} · {p.get('mid','—')} · {p.get('advanced','—')}" if p.get("publishes") else "No publica precios"
        bdr=' style="border-color:#EDA100"' if ch=="changed" else ""
        hbg=' style="background:#FAEEDA22"' if ch=="changed" else ""
        html+=f'<div class="ccard"{bdr}><div class="chead"{hbg}><div><p class="cname">{c["name"]}</p><p class="curl">{c["url"].replace("https://","")}</p></div><span class="pill {bcls[ch]}">{badge[ch]}</span></div><div class="cbody"><div class="crow">Precios: <b>{ps}</b></div>{feats}<div class="crow" style="color:var(--text-muted)">Revisado: {TODAY}</div></div></div>'
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

def render_leads(leads):
    if not leads: return '<tr><td colspan="10" style="padding:16px;text-align:center;color:var(--text-muted)">Sin leads — ejecutar el script</td></tr>'
    rows=""
    for l in leads[:100]:
        sc=l["score"]; sc_cls="sc-h" if sc==3 else("sc-m" if sc==2 else"sc-l"); sc_txt="⭐ Alto" if sc==3 else("▲ Medio" if sc==2 else"▼ Bajo")
        em=l.get("email","—"); em_h=f'<a href="mailto:{em}" class="lnk">{em[:22]}{"…" if len(em)>22 else ""}</a>' if em!="—" else '<span style="color:var(--text-muted)">—</span>'
        rows+=f'<tr data-s="{l["signal"]}" data-q="{"h" if sc==3 else("m" if sc==2 else"l")}" data-src="{l.get("source_type","")}">'
        rows+=f'<td><b>{l["company"]}</b><div style="font-size:10px;color:var(--text-muted)">{l["domain"]}</div></td>'
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

def render_preview(leads):
    p=[l for l in leads if l["score"]==3][:5] or leads[:5]
    if not p: return '<tr><td colspan="6" style="padding:12px;color:var(--text-muted);text-align:center">Sin leads nuevos</td></tr>'
    rows=""
    for l in p:
        sc_cls="sc-h" if l["score"]==3 else("sc-m" if l["score"]==2 else"sc-l"); sc_txt="⭐ Alto" if l["score"]==3 else("▲ Medio" if l["score"]==2 else"▼ Bajo")
        em=l.get("email","—"); em_h=f'<a href="mailto:{em}" class="lnk">{em[:20]}</a>' if em!="—" else "—"
        rows+=f'<tr><td><b>{l["company"]}</b></td><td><span class="sig {l["signal_class"]}">{l["signal_label"]}</span></td><td><span class="{sc_cls}">{sc_txt}</span></td><td style="font-size:11px">{em_h}</td><td><a href="https://{l["domain"]}" target="_blank" class="lnk">Web</a></td><td style="font-size:10px;color:var(--text-muted)">{l["date"]}</td></tr>'
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

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*55}\nEtendo Intelligence — {NOW}\n{'='*55}")
    data=load_data()
    comp_results,new_changes=scrape_competitors(data)
    new_leads=search_all_leads(data)
    save_data(data)
    if GOOGLE_SHEET_ID:
        print("→ Google Sheets...")
        tok=sheets_token()
        if tok: save_to_sheets(new_leads,new_changes,GOOGLE_SHEET_ID,tok)
        else: print("  ⚠️ Configura GOOGLE_SERVICE_ACCOUNT_JSON")
    print("→ HTML...")
    html=open(HTML_FILE,encoding="utf-8").read()
    by_src={}
    for l in new_leads: by_src[l.get("source_type","?")] = by_src.get(l.get("source_type","?"),0)+1
    html=inject(html,"generated_at",NOW); html=inject(html,"footer_date",TODAY)
    html=inject(html,"leads_total",str(len(new_leads)))
    html=inject(html,"leads_high",str(len([l for l in new_leads if l["score"]==3])))
    html=inject(html,"leads_email",str(len([l for l in new_leads if l.get("email","—")!="—"])))
    html=inject(html,"changes_total",str(len(new_changes)))
    html=inject(html,"ALERTS",render_alerts(new_changes,new_leads))
    html=inject(html,"LEADS_PREVIEW",render_preview(new_leads))
    html=inject(html,"COMP_CARDS",render_comp_cards(comp_results,data))
    html=inject(html,"CHANGES_HISTORY",render_changes(data))
    html=inject(html,"PRICES_ROWS",render_prices(data))
    html=inject(html,"FEATURES",render_features(data))
    html=inject(html,"LEADS_ROWS",render_leads(data["leads_history"]))
    open(HTML_FILE,"w",encoding="utf-8").write(html)
    print(f"\n✅ {NOW}")
    print(f"   Leads nuevos: {len(new_leads)} {by_src}")
    print(f"   Con email: {len([l for l in new_leads if l.get('email','—')!='—'])}")
    print(f"   Alta calidad: {len([l for l in new_leads if l['score']==3])}")
    print(f"   Cambios: {len(new_changes)} · Histórico: {len(data['leads_history'])}")

if __name__=="__main__":
    main()
