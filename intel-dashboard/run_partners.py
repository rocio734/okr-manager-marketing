#!/usr/bin/env python3
"""
run_partners.py — Spider de partners standalone.

Scrapea directorios de Odoo, SAP, Sage, Holded y Ahora ERP
para encontrar consultoras IT en España → candidatas a partners
o integradores de Etendo.

No necesita Brave ni SerpAPI — usa scraping directo de las páginas
de partners + Hunter para enriquecer emails.

Uso:
  python3 run_partners.py [--dry-run]
"""
import sys, json, time, os, re, requests
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
HUNTER_KEY = os.environ.get("HUNTER_API_KEY","")
HEADERS = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36","Accept-Language":"es-ES,es;q=0.9"}

# ── Hunter domain search (reutiliza la función de fetch_intel) ────────────────
def get_email(domain):
    try:
        r = fi.hunter_find_email(domain)
        return r
    except Exception:
        return "—"

# ── Ahora ERP — scraping directo con nombres correctos ───────────────────────
def scrape_ahora_partners():
    """Extrae integrantes de www.ahora.es/integrantes/ con nombres reales."""
    partners = []
    try:
        r = requests.get("https://www.ahora.es/integrantes/", headers=HEADERS, timeout=12, verify=False)
        if r.status_code != 200:
            print(f"    ⚠️  Ahora ERP: status {r.status_code}")
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        # Cada partner tiene un div con: NombreEmpresa, dirección, teléfono y link "Sitio web"
        for a in soup.find_all("a", string="Sitio web"):
            href = a.get("href","")
            if not href or "ahora" in href or href == "http://":
                continue
            d = fi.domain_from_url(href)
            if not d or fi.should_skip(d):
                continue
            # Subir por el DOM para encontrar el nombre de la empresa
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
                                         ["Calle","Avda","Plaza","Pol.","C/","Paseo","Av."])]
                if candidates:
                    name = candidates[0][:60]
                    break
            if not name:
                # Fallback: capitalizar el dominio
                name = d.split(".")[0].capitalize()
            partners.append({"name": name, "competitor": "Ahora ERP", "url": href, "domain": d})
    except Exception as e:
        print(f"    ⚠️  Ahora ERP: {e}")
    return partners

# ── Holded — scraping directo del directorio ──────────────────────────────────
def scrape_holded_partners():
    partners = []
    seen_domains = set()
    urls = [
        "https://www.holded.com/es/directorio-solution-partners",
        "https://www.holded.com/es/partners",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            # Links externos
            for a in soup.find_all("a", href=re.compile(r"^https?://(?!.*holded\.com)")):
                t = a.get_text(strip=True)
                d = fi.domain_from_url(a.get("href",""))
                href = a.get("href","")
                if 3 < len(t) < 80 and d and d not in seen_domains and not fi.should_skip(d):
                    if t.lower() not in ("sitio web","web","website","ver más","más info","ver perfil"):
                        partners.append({"name": t, "competitor": "Holded", "url": href, "domain": d})
                        seen_domains.add(d)
            # H2/H3 como nombres de empresa con el URL próximo
            if partners:
                break
        except Exception as e:
            print(f"    ⚠️  Holded {url}: {e}")
    return partners

# ── Sage — scraping del directorio de partners ────────────────────────────────
def scrape_sage_partners():
    partners = []
    urls = [
        "https://www.sage.com/es-es/partners/find-a-partner/",
        "https://www.sage.com/es-es/partners/",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            # Buscar cards de partners
            for card in soup.select("[class*='partner'], [class*='Partner'], article"):
                links = card.find_all("a", href=re.compile(r"^https?://"))
                title_el = card.find(["h2","h3","h4"])
                name = title_el.get_text(strip=True)[:60] if title_el else ""
                for a in links:
                    href = a.get("href","")
                    d = fi.domain_from_url(href)
                    if d and "sage.com" not in d and not fi.should_skip(d) and len(name) > 2:
                        partners.append({"name": name, "competitor": "Sage", "url": href, "domain": d})
                        break
            if partners:
                break
        except Exception as e:
            print(f"    ⚠️  Sage {url}: {e}")
    return partners

# ── DistritoK resellers (nuestra señal principal) ─────────────────────────────
def scrape_distrk_partners():
    """Busca distribuidores/resellers de DistritoK en España."""
    partners = []
    urls = [
        "https://www.distritok.com/canal-de-partners/",
        "https://www.distritok.com/partners/",
        "https://www.distritok.com/distribuidores/",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=re.compile(r"^https?://(?!.*distritok)")):
                t = a.get_text(strip=True)
                d = fi.domain_from_url(a.get("href",""))
                if 3 < len(t) < 80 and d and not fi.should_skip(d):
                    partners.append({"name": t, "competitor": "DistritoK", "url": a.get("href",""), "domain": d})
            if partners:
                break
        except Exception as e:
            print(f"    ⚠️  DistritoK {url}: {e}")
    return partners

# ── TeamSystem resellers ───────────────────────────────────────────────────────
def scrape_teamsystem_partners():
    partners = []
    urls = [
        "https://www.teamsystem.com/es/canal-de-partners/",
        "https://www.teamsystem.es/partners/",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=re.compile(r"^https?://(?!.*teamsystem)")):
                t = a.get_text(strip=True)
                d = fi.domain_from_url(a.get("href",""))
                if 3 < len(t) < 80 and d and not fi.should_skip(d):
                    partners.append({"name": t, "competitor": "TeamSystem", "url": a.get("href",""), "domain": d})
            if partners:
                break
        except Exception as e:
            print(f"    ⚠️  TeamSystem {url}: {e}")
    return partners

# ── Odoo partner pages (HTML público) ─────────────────────────────────────────
def scrape_odoo_partners():
    """Odoo partner directory está en JS pero hay páginas de partner individuales."""
    partners = []
    # Intentar scraping del directorio principal
    urls = [
        "https://www.odoo.com/es/partners/country/spain-66",
        "https://www.odoo.com/es/partners",
    ]
    seen = set()
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            # Cards de partners en el directorio
            for card in soup.select(".o_partner_card, [itemtype*='Organization'], .partner-card, article"):
                title_el = card.find(["h2","h3","h4","strong"])
                link_el = card.find("a", href=re.compile(r"^https?://(?!.*odoo\.com)"))
                if not link_el:
                    link_el = card.find("a", href=True)
                href = link_el.get("href","") if link_el else ""
                name = title_el.get_text(strip=True)[:60] if title_el else ""
                d = fi.domain_from_url(href) if href else ""
                if name and d and d not in seen and not fi.should_skip(d) and "odoo.com" not in d:
                    partners.append({"name": name, "competitor": "Odoo", "url": href, "domain": d})
                    seen.add(d)
            if partners:
                break
        except Exception as e:
            print(f"    ⚠️  Odoo {url}: {e}")
    return partners

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print("  PARTNER SPIDER — Odoo · SAP · Sage · Holded · Ahora ERP")
    print(f"{'='*60}\n")
    print(f"  dry_run: {DRY_RUN}\n")

    # Cargar datos actuales
    data = fi.load_data()
    initial_count = sum(1 for l in data["leads_history"]
                        if l.get("source_type","") in fi.PARTNER_SOURCES)
    seen = {l.get("domain","") for l in data["leads_history"] if l.get("domain")}
    print(f"Partners existentes: {initial_count}")
    print(f"Dominios en histórico: {len(seen)}\n")

    # Scraping por fuente
    all_raw = []
    steps = [
        ("Ahora ERP (directo)",   scrape_ahora_partners),
        ("Holded (directo)",      scrape_holded_partners),
        ("Sage (directo)",        scrape_sage_partners),
        ("DistritoK (directo)",   scrape_distrk_partners),
        ("TeamSystem (directo)",  scrape_teamsystem_partners),
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
        d = p.get("domain","")
        if not d or d in seen or fi.should_skip(d):
            continue
        name = p.get("name","").strip() or d.split(".")[0].capitalize()
        if not name or len(name) < 2:
            continue

        lead = fi.make_lead(
            name, d, "Consultoría ERP", "partner",
            f"Partner {p['competitor']}", "s-par",
            p.get("url", f"https://{d}"),
            f"Partner certificado de {p['competitor']} en España",
            2, "partner_scraping",
        )
        new_leads.append(lead)
        seen.add(d)

    print(f"Partners nuevos (sin duplicados): {len(new_leads)}\n")

    if not new_leads:
        print("⚠️  Ningún partner nuevo. Puede que ya estén todos en el histórico.")
        return

    # Enriquecer con emails via Hunter (máx 20 para no agotar cuota)
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

        # Leer tracking
        tracking_file = INTEL_DIR / "outreach_tracking.json"
        sent_map = {}
        if tracking_file.exists():
            try:
                td = json.loads(tracking_file.read_text(encoding="utf-8"))
                tc = td.get("contactos", td)
                sent_map = {e: c for e, c in tc.items()
                            if c.get("etapa",0) >= 2 or c.get("etapa") == 99}
            except Exception:
                pass

        # Regenerar HTML
        html = fi.HTML_FILE.read_text(encoding="utf-8")
        partner_leads = [l for l in data["leads_history"]
                         if l.get("source_type","") in fi.PARTNER_SOURCES]
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
        comp_tag = lead.get("snippet","").replace("Partner certificado de ","").replace(" en España","")
        em = lead.get("email","—")
        email_str = f" 📧 {em[:25]}" if em != "—" else ""
        print(f"  ✓ {lead['company'][:38]:38s} [{comp_tag:15s}] {lead['domain']}{email_str}")
    if len(new_leads) > 30:
        print(f"  ... y {len(new_leads)-30} más")


if __name__ == "__main__":
    main()
