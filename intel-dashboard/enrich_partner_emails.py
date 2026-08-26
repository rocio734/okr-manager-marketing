#!/usr/bin/env python3
"""
enrich_partner_emails.py — Enriquece emails de partners scrapeando sus propios sitios.

Estrategia:
1. Visita /contacto, /contact, /sobre-nosotros, /about, / de cada dominio
2. Extrae emails de mailto: links y patrones regex
3. Filtra emails personales (gmail, hotmail, etc.)
4. Prioriza: info@ > hola@ > comercial@ > contacto@ > otros
5. Guarda en intel_data.json y regenera index.html

Uso:
  python3 enrich_partner_emails.py [--limit N] [--dry-run]
"""
import sys, json, re, time, os, requests
from pathlib import Path
from bs4 import BeautifulSoup
import urllib3, tempfile, shutil
urllib3.disable_warnings()

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

DRY_RUN      = "--dry-run" in sys.argv
LIMIT        = int(next((sys.argv[sys.argv.index("--limit")+1] for i,a in enumerate(sys.argv) if a=="--limit"), 9999))
# Tiempo máximo en segundos. En GitHub Actions el job tiene 30 min; paramos a los 26 para guardar limpiamente.
MAX_SECONDS  = int(next((sys.argv[sys.argv.index("--max-seconds")+1] for i,a in enumerate(sys.argv) if a=="--max-seconds"), 26*60))
_START_TIME  = time.monotonic()


def _atomic_save(data: dict, target: Path) -> None:
    """Escribe JSON en un archivo temporal y lo mueve atómicamente al destino."""
    tmp = target.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(str(tmp), str(target))


def _time_left() -> float:
    return MAX_SECONDS - (time.monotonic() - _START_TIME)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

SKIP_DOMAINS_EMAIL = {
    "gmail.com","hotmail.com","yahoo.com","outlook.com","yahoo.es",
    "hotmail.es","live.com","icloud.com","protonmail.com",
}

PRIORITY_PREFIXES = [
    "info","hola","hello","contacto","contact","comercial",
    "ventas","sales","admin","administracion","administración","soporte",
]

EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

CONTACT_PATHS = [
    "/contacto", "/contact", "/contactanos", "/contáctanos",
    "/sobre-nosotros", "/about", "/quienes-somos", "/empresa",
    "/",
]


def extract_emails_from_html(html: str, domain: str) -> list[str]:
    """Extrae emails del HTML, filtrando personales y del propio dominio."""
    found = set(EMAIL_RE.findall(html))
    result = []
    for email in found:
        email = email.lower().strip(".,;)")
        parts = email.split("@")
        if len(parts) != 2:
            continue
        prefix, edomain = parts
        # Filtrar emails personales
        if edomain in SKIP_DOMAINS_EMAIL:
            continue
        # Filtrar emails de servicios / trackers
        if any(x in edomain for x in ["sentry","intercom","pixel","tracking","analytics","noreply","no-reply"]):
            continue
        # Filtrar emails que no son del propio dominio si son claramente de otra empresa
        # Aceptar: mismo dominio, o subdomain, o cualquier corporativo
        if len(prefix) < 2 or len(prefix) > 40:
            continue
        result.append(email)
    return result


def score_email(email: str, domain: str) -> int:
    """Puntúa un email: mayor = mejor. Prioriza info@, contacto@, etc."""
    prefix = email.split("@")[0].lower()
    edomain = email.split("@")[1].lower()
    score = 0
    # Mismo dominio que el partner = +10
    if domain in edomain or edomain.endswith("." + domain):
        score += 10
    # Prefijo de contacto corporativo = +5
    for i, pref in enumerate(PRIORITY_PREFIXES):
        if prefix == pref:
            score += 5 + (len(PRIORITY_PREFIXES) - i)
            break
        elif prefix.startswith(pref):
            score += 3
    return score


def fetch_emails_for_domain(domain: str) -> str:
    """Busca el mejor email corporativo para un dominio."""
    all_emails = []
    tried = set()

    for path in CONTACT_PATHS:
        for scheme in ["https", "http"]:
            url = f"{scheme}://{domain}{path}"
            if url in tried:
                continue
            tried.add(url)
            try:
                r = requests.get(url, headers=HEADERS, timeout=8,
                                  verify=False, allow_redirects=True)
                if r.status_code != 200:
                    continue
                # Evitar procesar páginas muy grandes (CDN, etc.)
                if len(r.content) > 500_000:
                    r_text = r.text[:200_000]
                else:
                    r_text = r.text

                emails = extract_emails_from_html(r_text, domain)
                all_emails.extend(emails)

                # Si encontramos algo en esta página, podemos parar si es la raíz
                if emails and path == "/":
                    break
                if emails:
                    break  # ya tenemos emails, no seguir con más paths
            except Exception:
                pass
            break  # si https falla con 200, no probar http para el mismo path

        if all_emails:
            break  # tenemos emails, salir del loop de paths

    if not all_emails:
        return "—"

    # Rankear y devolver el mejor
    scored = sorted(all_emails, key=lambda e: score_email(e, domain), reverse=True)
    return scored[0]


def main():
    print(f"\n{'='*60}")
    print(f"  PARTNER EMAIL ENRICHMENT")
    print(f"  dry_run: {DRY_RUN}  |  limit: {LIMIT}")
    print(f"{'='*60}\n")

    data = fi.load_data()
    leads = data["leads_history"]

    # Partners sin email
    partners_sin_email = [
        l for l in leads
        if l.get("source_type", "") in fi.PARTNER_SOURCES
        and l.get("email", "—") in ("—", "", None, "None")
    ]
    print(f"Partners sin email: {len(partners_sin_email)}")
    print(f"Procesando:         {min(LIMIT, len(partners_sin_email))}")
    print(f"Tiempo máximo:      {MAX_SECONDS//60} min\n")

    enriched = 0
    processed = 0
    stopped_early = False

    for i, lead in enumerate(partners_sin_email[:LIMIT], 1):
        # Parar limpiamente si queda menos de 90 segundos
        if _time_left() < 90:
            print(f"\n⏱️  Tiempo casi agotado ({_time_left():.0f}s restantes) — parando limpiamente en [{i-1}/{min(LIMIT, len(partners_sin_email))}]")
            stopped_early = True
            break

        domain  = lead.get("domain", "")
        company = lead.get("company", domain)
        if not domain:
            continue

        email = fetch_emails_for_domain(domain)
        processed += 1

        status = "✓" if email != "—" else "·"
        print(f"  [{i:3d}] {status} {company[:38]:38s} {domain:30s} {email}")

        if email != "—":
            enriched += 1
            if not DRY_RUN:
                lead["email"] = email

        if i % 50 == 0:
            print(f"\n  [{i}/{min(LIMIT, len(partners_sin_email))}] — {enriched} emails encontrados hasta ahora\n")

        # Guardar cada 5 con atomic write para no corromper el archivo
        if not DRY_RUN and i % 5 == 0 and enriched > 0:
            _atomic_save(data, fi.DATA_FILE)

        time.sleep(0.3)

    print(f"\n{'─'*60}")
    print(f"  Procesados: {processed}")
    print(f"  Con email:  {enriched}")
    print(f"  Pendientes: {len(partners_sin_email) - processed} (continuarán la próxima semana)")
    if stopped_early:
        print(f"  ⚠️  Parado antes del timeout — datos guardados correctamente")
    print(f"{'─'*60}\n")

    if not DRY_RUN and enriched > 0:
        _atomic_save(data, fi.DATA_FILE)
        print(f"intel_data.json guardado (atomic)")

        # Regenerar tabla de partners en index.html
        html = fi.HTML_FILE.read_text(encoding="utf-8")
        partner_leads = [l for l in data["leads_history"]
                         if l.get("source_type", "") in fi.PARTNER_SOURCES]
        html = fi.inject(html, "PARTNER_ROWS", fi.render_partner_leads(partner_leads))
        fi.HTML_FILE.write_text(html, encoding="utf-8")
        print(f"index.html actualizado")
    elif DRY_RUN:
        print("[DRY RUN] — sin guardar")


if __name__ == "__main__":
    main()
