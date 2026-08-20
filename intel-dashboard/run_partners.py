#!/usr/bin/env python3
"""
run_partners.py — Spider de partners standalone.

Scrapea directorios de partners de competidores ERP en España:
  • Ahora ERP (Teamsystem SQL) — HTTP directo
  • Odoo España              — HTTP directo (paginado)
  • Holded                   — Playwright (JS-rendered)
  • Sage                     — Playwright (form + resultados JS)
  • SAP                      — Playwright (app React)

Cada fuente añade partners a intel_data.json sin duplicar dominios.
Hunter enriquece hasta 20 partners nuevos con email.

Uso:
  python3 run_partners.py [--dry-run]
"""
import sys, json, time, os, re, asyncio, requests
from pathlib import Path
from bs4 import BeautifulSoup
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INTEL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(INTEL_DIR))

# Cargar .env
ENV_FILE = INTEL_DIR.parent.parent / ".env"
if ENV_FILE.exists():
    for _line in ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if "=" in _line and not _line.startswith("#"):
            k, v = _line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import fetch_intel as fi

DRY_RUN    = "--dry-run" in sys.argv
HUNTER_KEY = os.environ.get("HUNTER_API_KEY", "")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
}

# ── Playwright disponible? ────────────────────────────────────────────────────
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False
    print("⚠️  playwright no instalado — Holded/Sage/SAP usarán HTTP fallback")


# ═══════════════════════════════════════════════════════════════════════════════
#  SCRAPERS HTTP (sin JS)
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_ahora_partners():
    """Integrantes de Ahora ERP (TeamSystem SQL) con nombres reales."""
    partners = []
    try:
        r = requests.get("https://www.ahora.es/integrantes/", headers=HEADERS, timeout=12, verify=False)
        if r.status_code != 200:
            print(f"    ⚠️  Ahora ERP: status {r.status_code}")
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", string="Sitio web"):
            href = a.get("href", "")
            if not href or "ahora" in href or href == "http://":
                continue
            d = fi.domain_from_url(href)
            if not d or fi.should_skip(d):
                continue
            name = ""
            container = a
            for _ in range(6):
                container = container.parent
                if container is None:
                    break
                texts = [t.strip() for t in container.stripped_strings]
                candidates = [t for t in texts
                              if len(t) > 3 and "Sitio web" not in t
                              and not any(x in t for x in
                                         ["Calle", "Avda", "Plaza", "Pol.", "C/", "Paseo", "Av."])]
                if candidates:
                    name = candidates[0][:60]
                    break
            if not name:
                name = d.split(".")[0].capitalize()
            partners.append({"name": name, "competitor": "Ahora ERP", "url": href, "domain": d})
    except Exception as e:
        print(f"    ⚠️  Ahora ERP: {e}")
    return partners


def scrape_odoo_partners():
    """Directorio público de Odoo España — 24 páginas × ~21 perfiles."""
    partners = []
    seen = set()
    base = "https://www.odoo.com/es/partners/country/spain-66"

    print("    Recolectando perfiles Odoo España...")
    profile_urls = []
    for page in range(1, 26):
        url = f"{base}?page={page}" if page > 1 else base
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            links = soup.select("a.o_website_partner_search_partner_url, "
                                ".o_partner_no_full_page a[href*='/partners/'], "
                                "a[href*='/es/partners/']")
            if not links:
                links = soup.select("a[href*='/partners/']")
            page_urls = []
            for a in links:
                href = a.get("href", "")
                if "/partners/country/" in href or "/partners/page/" in href:
                    continue
                if re.search(r"/partners/[^/]+-\d+", href):
                    full = "https://www.odoo.com" + href if href.startswith("/") else href
                    if full not in seen:
                        page_urls.append(full)
                        seen.add(full)
            if not page_urls:
                break
            profile_urls.extend(page_urls)
            time.sleep(0.3)
        except Exception as e:
            print(f"    ⚠️  Odoo pág {page}: {e}")
            break

    print(f"    → {len(profile_urls)} perfiles encontrados")
    seen_domains = set()
    for i, pu in enumerate(profile_urls, 1):
        try:
            rp = requests.get(pu, headers=HEADERS, timeout=12)
            if rp.status_code != 200:
                continue
            sp = BeautifulSoup(rp.text, "html.parser")
            name_el = sp.select_one("h1, .o_partner_name, [itemprop='name']")
            name = name_el.get_text(strip=True)[:60] if name_el else ""
            web_el = sp.select_one("a[href*='http']:not([href*='odoo.com'])")
            if not web_el:
                continue
            href = web_el.get("href", "")
            d = fi.domain_from_url(href)
            if d and d not in seen_domains and not fi.should_skip(d):
                partners.append({"name": name or d, "competitor": "Odoo", "url": href, "domain": d})
                seen_domains.add(d)
            if i % 50 == 0:
                print(f"    [{i}/{len(profile_urls)}] encontrados: {len(partners)}")
            time.sleep(0.2)
        except Exception:
            pass

    return partners


def scrape_distrk_partners():
    partners = []
    for url in ["https://www.distritok.com/canal-de-partners/",
                "https://www.distritok.com/partners/"]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=re.compile(r"^https?://(?!.*distritok)")):
                t = a.get_text(strip=True)
                d = fi.domain_from_url(a.get("href", ""))
                if 3 < len(t) < 80 and d and not fi.should_skip(d):
                    partners.append({"name": t, "competitor": "DistritoK",
                                     "url": a.get("href", ""), "domain": d})
            if partners:
                break
        except Exception as e:
            print(f"    ⚠️  DistritoK {url}: {e}")
    return partners


# ═══════════════════════════════════════════════════════════════════════════════
#  SCRAPERS PLAYWRIGHT (JS-rendered)
# ═══════════════════════════════════════════════════════════════════════════════

async def _pw_holded():
    """Playwright: directorio de partners de Holded.

    Estrategia:
    1. Cargar /directorio-solution-partners → extraer 45 URLs de perfil
    2. Visitar cada perfil → sacar h1 (nombre) + 'Visitar web' (URL externa)
    """
    partners = []
    seen = set()
    base = "https://www.holded.com"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="es-ES",
        )
        # ── Paso 1: extraer slugs del directorio ──────────────────────────────
        page = await ctx.new_page()
        try:
            await page.goto(f"{base}/es/directorio-solution-partners",
                            timeout=30000, wait_until="networkidle")
            await page.wait_for_timeout(4000)
            profile_links = await page.query_selector_all(
                "a[href*='/directorio-solution-partners/']"
            )
            profile_urls = []
            for a in profile_links:
                href = await a.get_attribute("href") or ""
                # Filtrar la propia página del directorio
                if href and href != "/es/directorio-solution-partners":
                    full = base + href if href.startswith("/") else href
                    if full not in profile_urls:
                        profile_urls.append(full)
            print(f"    Holded: {len(profile_urls)} perfiles encontrados")
        except Exception as e:
            print(f"    ⚠️  Holded directorio: {e}")
            profile_urls = []
        finally:
            await page.close()

        # ── Paso 2: visitar cada perfil ───────────────────────────────────────
        for i, url in enumerate(profile_urls, 1):
            prof = await ctx.new_page()
            try:
                await prof.goto(url, timeout=20000, wait_until="domcontentloaded")
                await prof.wait_for_timeout(2000)

                # Nombre: h1 de la página
                name_el = await prof.query_selector("h1")
                name = (await name_el.inner_text()).strip()[:60] if name_el else ""

                # Website externo: link con texto "Visitar web" o primero que salga de holded.com
                web_el = await prof.query_selector(
                    "a:has-text('Visitar web'), a:has-text('Visitar página'), "
                    "a[href^='http']:not([href*='holded.com']):not([href*='facebook']):not([href*='instagram'])"
                    ":not([href*='linkedin']):not([href*='twitter']):not([href*='apple']):not([href*='google'])"
                )
                href = (await web_el.get_attribute("href") or "").strip() if web_el else ""
                d = fi.domain_from_url(href)

                if d and d not in seen and not fi.should_skip(d):
                    if not name:
                        name = d.split(".")[0].capitalize()
                    partners.append({"name": name, "competitor": "Holded",
                                     "url": href, "domain": d})
                    seen.add(d)
                    if i % 10 == 0:
                        print(f"    [{i}/{len(profile_urls)}] partners: {len(partners)}")
            except Exception as e:
                print(f"    ⚠️  Holded perfil {i}: {e}")
            finally:
                await prof.close()
            await asyncio.sleep(0.3)

        await browser.close()
    print(f"    Holded total: {len(partners)} partners con web")
    return partners


async def _pw_sage():
    """Playwright: formulario de búsqueda de partners Sage España.

    Estructura de resultados:
    - <a class='website-opt' href='...'>  → URL externa del partner
    - data-tracking='NOMBRE'              → nombre del partner (en botón hermano)
    Requiere ciudad para devolver resultados; iteramos varias ciudades + tipos.
    Extraemos con regex del HTML para acceder a elementos ocultos.
    """
    partners = []
    seen = set()
    types = ["Business Partner", "Tech Partner"]
    # Sin ciudad el formulario no devuelve nada; con ciudades grandes cubrimos España
    locations = ["Madrid", "Barcelona", "Valencia", "Sevilla", "Bilbao",
                 "Zaragoza", "Málaga", "Murcia", "Valladolid", "Alicante"]
    URL = "https://www.sage.com/es-es/encuentra-un-partner/"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="es-ES",
        )
        for ptype in types:
            for loc in locations:
                page = await ctx.new_page()
                try:
                    await page.goto(URL, timeout=25000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)

                    type_sel = await page.query_selector("#partner-search-partner-type")
                    if type_sel:
                        await type_sel.select_option(value=ptype)

                    country_sel = await page.query_selector("#partner-search-country")
                    if country_sel:
                        await country_sel.select_option(value="Spain")

                    loc_input = await page.query_selector("#partner-search-location")
                    if loc_input:
                        await loc_input.fill(loc)

                    submit = await page.query_selector("#partner-search-submit")
                    if submit:
                        await submit.click()
                        await page.wait_for_timeout(6000)

                    # JS evaluate para extraer pares nombre+URL de elementos ocultos
                    JS = """
                        () => {
                            const out = [];
                            document.querySelectorAll(".more-partner-details").forEach(detail => {
                                const webLink = detail.querySelector("a.website-opt");
                                if (!webLink) return;
                                const href = webLink.href;
                                const sibling = detail.previousElementSibling;
                                let name = "";
                                if (sibling) {
                                    const btn = sibling.querySelector("[data-tracking]");
                                    if (btn) name = btn.getAttribute("data-tracking") || "";
                                }
                                if (!name) {
                                    const parent = detail.closest("li, .partner, article");
                                    if (parent) {
                                        const h = parent.querySelector("h2,h3,h4,.partner-name");
                                        if (h) name = h.textContent.trim();
                                    }
                                }
                                out.push({name: name, href: href});
                            });
                            return out;
                        }
                    """
                    rows = await page.evaluate(JS)
                    found = 0
                    for row in rows:
                        href = row.get("href", "")
                        d = fi.domain_from_url(href)
                        if d and d not in seen and not fi.should_skip(d):
                            name = (row.get("name") or d.split(".")[0]).strip().title()[:60]
                            partners.append({"name": name, "competitor": "Sage",
                                             "url": href, "domain": d})
                            seen.add(d)
                            found += 1

                    if found:
                        print(f"    Sage {ptype}/{loc}: {found} nuevos")

                except Exception as e:
                    print(f"    ⚠️  Sage {ptype}/{loc}: {e}")
                finally:
                    await page.close()
                await asyncio.sleep(0.5)

        await browser.close()
    print(f"    Sage total: {len(partners)} partners con web")
    return partners


async def _pw_sap():
    """Playwright: directorio de partners SAP España.

    sap.com/spain/partners/find.html ya tiene los partners precargados.
    Extraemos con JS evaluate todos los links externos que son webs de partners.
    """
    partners = []
    seen = set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="es-ES",
        )
        page = await ctx.new_page()
        try:
            await page.goto("https://www.sap.com/spain/partners/find.html",
                            timeout=25000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # Aceptar cookies si aparece el banner
            btn = await page.query_selector("#truste-consent-button")
            if btn:
                await btn.click()
                await page.wait_for_timeout(2000)

            await page.wait_for_timeout(3000)

            JS = """
                () => {
                    const skip = ["sap.com","facebook","twitter","linkedin","instagram",
                                  "youtube","google","apple","microsoft","mailto","tel:","#"];
                    const links = document.querySelectorAll("a[href]");
                    const out = [];
                    links.forEach(a => {
                        const href = a.href;
                        if (!href.startsWith("http")) return;
                        if (skip.some(s => href.includes(s))) return;
                        const card = a.closest("[class*=card],[class*=partner],li,article");
                        const name = card?.querySelector("h2,h3,h4,[class*=name],[class*=title]")?.textContent?.trim()
                                  || a.textContent.trim()
                                  || "";
                        out.push({href: href, name: name.substring(0, 60)});
                    });
                    return out;
                }
            """
            rows = await page.evaluate(JS)
            for row in rows:
                d = fi.domain_from_url(row.get("href", ""))
                if d and d not in seen and not fi.should_skip(d):
                    name = (row.get("name") or d.split(".")[0]).strip()[:60] or d.split(".")[0].capitalize()
                    partners.append({"name": name, "competitor": "SAP",
                                     "url": row.get("href", ""), "domain": d})
                    seen.add(d)
            print(f"    SAP España: {len(partners)} partners encontrados")

        except Exception as e:
            print(f"    ⚠️  SAP Playwright: {e}")
        finally:
            await browser.close()
    return partners


def scrape_holded_partners():
    if PLAYWRIGHT_OK:
        try:
            return asyncio.run(_pw_holded())
        except Exception as e:
            print(f"    ⚠️  Holded PW error: {e}")
    # Fallback HTTP
    partners = []
    try:
        r = requests.get("https://www.holded.com/es/directorio-solution-partners",
                         headers=HEADERS, timeout=12)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            seen = set()
            for a in soup.find_all("a", href=re.compile(r"^https?://(?!.*holded\.com)")):
                t = a.get_text(strip=True)
                d = fi.domain_from_url(a.get("href", ""))
                if 3 < len(t) < 80 and d and d not in seen and not fi.should_skip(d):
                    if t.lower() not in ("sitio web", "web", "website", "ver más"):
                        partners.append({"name": t, "competitor": "Holded",
                                         "url": a.get("href", ""), "domain": d})
                        seen.add(d)
    except Exception as e:
        print(f"    ⚠️  Holded HTTP: {e}")
    return partners


def scrape_sage_partners():
    if PLAYWRIGHT_OK:
        try:
            return asyncio.run(_pw_sage())
        except Exception as e:
            print(f"    ⚠️  Sage PW error: {e}")
    return []  # Sage no tiene listado estático


def scrape_sap_partners():
    if PLAYWRIGHT_OK:
        try:
            return asyncio.run(_pw_sap())
        except Exception as e:
            print(f"    ⚠️  SAP PW error: {e}")
    return []  # SAP no tiene listado estático


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{'='*60}")
    print("  PARTNER SPIDER")
    print("  Ahora ERP · Odoo · Holded · Sage · SAP")
    print(f"{'='*60}\n")
    print(f"  Playwright: {'✅' if PLAYWRIGHT_OK else '⚠️  no disponible'}")
    print(f"  dry_run:    {DRY_RUN}\n")

    data = fi.load_data()
    initial_count = sum(1 for l in data["leads_history"]
                        if l.get("source_type", "") in fi.PARTNER_SOURCES)
    seen = {l.get("domain", "") for l in data["leads_history"] if l.get("domain")}
    print(f"Partners existentes: {initial_count}")
    print(f"Dominios en histórico: {len(seen)}\n")

    # Scraping por fuente
    all_raw = []
    steps = [
        ("Ahora ERP (directo)",   scrape_ahora_partners),
        ("Holded (Playwright)",   scrape_holded_partners),
        ("Sage (Playwright)",     scrape_sage_partners),
        ("SAP (Playwright)",      scrape_sap_partners),
        ("DistritoK (directo)",   scrape_distrk_partners),
        ("Odoo (directorio)",     scrape_odoo_partners),
    ]
    for label, fn in steps:
        print(f"[→] {label}...")
        try:
            results = fn()
            print(f"    {len(results)} entradas")
            all_raw.extend(results)
        except Exception as e:
            print(f"    ⚠️  Error: {e}")
        time.sleep(0.5)

    print(f"\nTotal raw (con duplicados): {len(all_raw)}")

    # Dedup y crear leads
    new_leads = []
    for p in all_raw:
        d = p.get("domain", "")
        if not d or d in seen or fi.should_skip(d):
            continue
        name = p.get("name", "").strip() or d.split(".")[0].capitalize()
        if not name or len(name) < 2:
            continue
        comp = p.get("competitor", "ERP")
        lead = fi.make_lead(
            name, d, "Consultoría ERP", "partner",
            f"Partner {comp}", "s-par",
            p.get("url", f"https://{d}"),
            f"Partner certificado de {comp} en España",
            2, "partner_scraping",
        )
        new_leads.append(lead)
        seen.add(d)

    print(f"Partners nuevos (sin duplicados): {len(new_leads)}\n")

    if not new_leads:
        print("⚠️  Ningún partner nuevo. El histórico ya los tiene todos.")
        return

    # Enriquecer con emails via Hunter (máx 20)
    if HUNTER_KEY:
        print(f"Enriqueciendo emails con Hunter (max 20)...")
        enriched = 0
        for lead in new_leads[:20]:
            try:
                email = fi.hunter_find_email(lead["domain"])
                if email and email != "—":
                    lead["email"] = email
                    enriched += 1
                    time.sleep(0.5)
            except Exception:
                pass
        print(f"  → {enriched} emails encontrados\n")

    # Guardar
    if not DRY_RUN:
        data["leads_history"].extend(new_leads)
        fi.save_data(data)
        print(f"intel_data.json: {len(data['leads_history'])} leads totales")

        # Regenerar HTML
        html = fi.HTML_FILE.read_text(encoding="utf-8")
        partner_leads = [l for l in data["leads_history"]
                         if l.get("source_type", "") in fi.PARTNER_SOURCES]
        html = fi.inject(html, "PARTNER_ROWS", fi.render_partner_leads(partner_leads))
        html = fi.inject(html, "leads_total", str(len(data["leads_history"])))
        fi.HTML_FILE.write_text(html, encoding="utf-8")
        print(f"index.html actualizado: {len(partner_leads)} partners en tabla")
    else:
        print("[DRY RUN] — sin guardar")

    print(f"\n{'─'*60}")
    print(f"  Antes:  {initial_count} partners")
    print(f"  Nuevos: {len(new_leads)}")
    total = initial_count + (len(new_leads) if not DRY_RUN else 0)
    print(f"  Total:  {total} partners")
    print(f"{'─'*60}\n")

    for lead in new_leads[:30]:
        comp_tag = lead.get("snippet", "").replace("Partner certificado de ", "").replace(" en España", "")
        em = lead.get("email", "—")
        email_str = f" 📧 {em[:25]}" if em != "—" else ""
        print(f"  ✓ {lead['company'][:38]:38s} [{comp_tag:15s}] {lead['domain']}{email_str}")
    if len(new_leads) > 30:
        print(f"  ... y {len(new_leads)-30} más")


if __name__ == "__main__":
    main()
