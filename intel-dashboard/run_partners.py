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
    """Playwright: directorio de partners de Holded."""
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
            await page.goto("https://www.holded.com/es/directorio-solution-partners",
                            timeout=30000, wait_until="networkidle")
            # Esperar a que aparezcan tarjetas de partners
            await page.wait_for_timeout(4000)

            # Holded renderiza cada partner como una card con un link externo
            # Buscar todos los links que salen de holded.com
            links = await page.query_selector_all("a[href]")
            for link in links:
                href = await link.get_attribute("href") or ""
                if not href.startswith("http") or "holded.com" in href:
                    continue
                d = fi.domain_from_url(href)
                if not d or d in seen or fi.should_skip(d):
                    continue
                # Intentar obtener nombre: texto del link, o el h3/h4 más cercano
                name = (await link.inner_text()).strip()
                if not name or len(name) < 3 or name.lower() in ("web", "website", "visitar"):
                    # Subir un nivel a buscar el nombre de la empresa
                    parent = await link.evaluate_handle("el => el.closest('[class*=card], [class*=partner], article, li, div')")
                    if parent:
                        heading = await parent.query_selector("h2,h3,h4,strong,[class*=name],[class*=title]")
                        if heading:
                            name = (await heading.inner_text()).strip()[:60]
                if not name or len(name) < 3:
                    name = d.split(".")[0].capitalize()
                partners.append({"name": name[:60], "competitor": "Holded",
                                  "url": href, "domain": d})
                seen.add(d)
        except Exception as e:
            print(f"    ⚠️  Holded Playwright: {e}")
        finally:
            await browser.close()
    return partners


async def _pw_sage():
    """Playwright: formulario de búsqueda de partners Sage España."""
    partners = []
    seen = set()
    # Buscar partners en varias ciudades españolas para cobertura amplia
    locations = ["Madrid", "Barcelona", "Valencia", "Sevilla", "Bilbao",
                 "Zaragoza", "Málaga", "Murcia", "Valladolid", "Alicante"]
    types = ["Business Partner", "Tech Partner"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="es-ES",
        )
        for loc in locations:
            for ptype in types:
                page = await ctx.new_page()
                try:
                    await page.goto("https://www.sage.com/es-es/encuentra-un-partner/",
                                    timeout=25000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)

                    # Rellenar localización
                    loc_input = await page.query_selector(
                        "input[placeholder*='postal'], input[placeholder*='ciudad'], "
                        "input[name*='location'], input[id*='location'], "
                        ".sage-partner-search input[type='text']"
                    )
                    if loc_input:
                        await loc_input.fill(loc)
                        await page.wait_for_timeout(500)

                    # Seleccionar tipo de partner (select o radio)
                    type_sel = await page.query_selector("select[name*='type'], select[id*='type']")
                    if type_sel:
                        await type_sel.select_option(label=ptype)
                    else:
                        # Intentar como dropdown o radio
                        radio = await page.query_selector(f"input[value='{ptype}']")
                        if radio:
                            await radio.click()

                    # Enviar formulario
                    submit = await page.query_selector(
                        "button[type='submit'], input[type='submit'], "
                        ".sage-partner-search button, [class*='search-btn']"
                    )
                    if submit:
                        await submit.click()
                        await page.wait_for_timeout(4000)

                    # Extraer resultados
                    result_cards = await page.query_selector_all(
                        ".sage-partner-search-results .partner, "
                        "[class*='partner-result'], [class*='result-card'], "
                        ".sage-partner-search-results li, "
                        ".sage-partner-search-results article"
                    )
                    for card in result_cards:
                        name_el = await card.query_selector("h2,h3,h4,[class*='name'],[class*='title']")
                        web_el  = await card.query_selector("a[href*='http']:not([href*='sage.com'])")
                        name = (await name_el.inner_text()).strip()[:60] if name_el else ""
                        href = (await web_el.get_attribute("href") or "") if web_el else ""
                        d = fi.domain_from_url(href)
                        if d and d not in seen and not fi.should_skip(d) and name:
                            partners.append({"name": name, "competitor": "Sage",
                                             "url": href, "domain": d})
                            seen.add(d)

                    if result_cards:
                        print(f"    Sage {loc}/{ptype}: {len(result_cards)} resultados")
                except Exception as e:
                    print(f"    ⚠️  Sage {loc}/{ptype}: {e}")
                finally:
                    await page.close()
                time.sleep(0.5)

        await browser.close()
    return partners


async def _pw_sap():
    """Playwright: directorio de partners SAP Latinoamérica/España."""
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
            await page.goto("https://www.sap.com/latinamerica/partners/find.html",
                            timeout=30000, wait_until="networkidle")
            await page.wait_for_timeout(5000)

            # Buscar el selector de país para filtrar España
            country_sel = await page.query_selector(
                "select[name*='country'], select[id*='country'], "
                "[placeholder*='country'], [placeholder*='país']"
            )
            if country_sel:
                await country_sel.select_option(label="Spain")
                await page.wait_for_timeout(1000)

            # Buscar tipo "Sell" o "Consulting" (los más relevantes)
            for ptype in ["Sell", "Consulting", "Managed Services"]:
                type_el = await page.query_selector(f"[value='{ptype}'], label:has-text('{ptype}')")
                if type_el:
                    await type_el.click()
                    await page.wait_for_timeout(3000)

                    # Extraer resultados
                    cards = await page.query_selector_all(
                        "[class*='partner-card'], [class*='partner-result'], "
                        "[class*='result-item'], .partner, article"
                    )
                    for card in cards:
                        name_el = await card.query_selector("h2,h3,h4,[class*='name']")
                        web_el  = await card.query_selector("a[href*='http']:not([href*='sap.com'])")
                        name = (await name_el.inner_text()).strip()[:60] if name_el else ""
                        href = (await web_el.get_attribute("href") or "") if web_el else ""
                        d = fi.domain_from_url(href)
                        if d and d not in seen and not fi.should_skip(d) and name:
                            partners.append({"name": name, "competitor": "SAP",
                                             "url": href, "domain": d})
                            seen.add(d)
                    if cards:
                        print(f"    SAP {ptype}: {len(cards)} resultados")

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
