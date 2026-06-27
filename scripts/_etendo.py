"""Helpers compartidos para los jobs de OKR Manager."""
import os, json, http.cookiejar, urllib.request, urllib.parse, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV  = ROOT.parent / ".env"
# Buscar configs: primero en config/ (dentro del repo), luego en reports/okr_coach_configs/ (local)
CONFIGS_DIR = ROOT / "config" if (ROOT / "config").exists() else ROOT.parent / "reports" / "okr_coach_configs"

# Cargar .env
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

ETENDO_USER = os.environ.get("ETENDO_USERNAME")
ETENDO_PASS = os.environ.get("ETENDO_PASSWORD")
ETENDO_BASE = os.environ.get("ETENDO_BASE_URL") or os.environ.get("ETENDO_BASE", "https://futit-staff.etendo.cloud")
WRITE_URL   = os.environ.get("ETENDO_WRITE_URL", "https://staff-ui.etendo.cloud/etendo")

SUPABASE_URL  = os.environ.get("SUPABASE_URL")
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY")
OPENAI_KEY    = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")
RESEND_KEY    = os.environ.get("RESEND_API_KEY")

APPROVER_EMAIL = os.environ.get("OKR_APPROVER_EMAIL", "rocio.altamirano@smfconsulting.es")
SITE_URL       = os.environ.get("OKR_SITE_URL", "http://localhost:8080")


def load_team_config(team_slug):
    """Lee reports/okr_coach_configs/<slug>.json (creado por okr_coach_setup.py)."""
    path = CONFIGS_DIR / f"{team_slug}.json"
    if not path.exists():
        raise FileNotFoundError(f"No encontré config en {path}. Corré primero: python3 scripts/okr_coach_setup.py")
    return json.loads(path.read_text())


def all_team_configs():
    """Devuelve lista de configs de todos los teams configurados."""
    if not CONFIGS_DIR.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(CONFIGS_DIR.glob("*.json"))]


def etendo_login(role_id):
    body = json.dumps({"username": ETENDO_USER, "password": ETENDO_PASS, "role": role_id}).encode()
    req  = urllib.request.Request(f"{ETENDO_BASE}/api/auth/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["token"]


def etendo_fetch(jwt, entity):
    """Fetch paginado de una entidad. Devuelve lista de records."""
    out = []
    start = 0
    while True:
        body = urllib.parse.urlencode({
            "_operationType": "fetch",
            "_startRow": str(start),
            "_endRow":   str(start + 500),
        }).encode()
        req = urllib.request.Request(f"{ETENDO_BASE}/api/datasource/{entity}", data=body, method="POST")
        req.add_header("Authorization", f"Bearer {jwt}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=30) as r:
            page = json.loads(r.read()).get("response", {}).get("data", [])
        if not page:
            break
        out.extend(page)
        if len(page) < 500:
            break
        start += 500
    return out


def etendo_sid_login():
    """Login via JSESSIONID en staff-ui (requerido para ETCRM_Lead)."""
    jar    = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    body   = urllib.parse.urlencode(
        {"user": ETENDO_USER, "password": ETENDO_PASS, "Command": "Login"}
    ).encode()
    req = urllib.request.Request(
        f"{WRITE_URL}/secureApp/LoginHandler.html", data=body, method="POST"
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with opener.open(req):
            pass
    except Exception:
        pass
    for cookie in jar:
        if cookie.name == "JSESSIONID":
            return cookie.value
    return ""


def etendo_fetch_crm(sid, entity="ETCRM_Lead"):
    """Fetch paginado de ETCRM_Lead via JSESSIONID (staff-ui datasource)."""
    out = []
    start = 0
    while True:
        params = urllib.parse.urlencode({
            "_startRow": str(start),
            "_endRow":   str(start + 500),
        })
        url = f"{WRITE_URL}/org.openbravo.service.datasource/{entity}?{params}"
        req = urllib.request.Request(url)
        req.add_header("Cookie", f"JSESSIONID={sid}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=30) as r:
            page = json.loads(r.read()).get("response", {}).get("data", [])
        if not page:
            break
        out.extend(page)
        if len(page) < 500:
            break
        start += 500
    return out


def fetch_team_krs(jwt, period_name, team_id):
    """Devuelve KRs del team filtrando por OKR padre.team."""
    objs = etendo_fetch(jwt, "SMFOKR_Okr_Obj")
    team_obj_ids = {
        o.get("id") for o in objs
        if (o.get("period$_identifier") or "").strip() == period_name
        and o.get("team") == team_id
    }
    if not team_obj_ids:
        return []
    krs = etendo_fetch(jwt, "SMFOKR_Okr_Kr")
    out = []
    for kr in krs:
        if kr.get("okr") in team_obj_ids:
            out.append({
                "id":        kr.get("id"),
                "name":      kr.get("title") or kr.get("_identifier"),
                "current":   kr.get("currentValue"),
                "target":    kr.get("targetValue"),
                "baseline":  kr.get("startValue"),
                "objective": kr.get("okr$_identifier"),
            })
    return out


def sb_request(method, path, body=None):
    """Llamada REST a Supabase con service key (bypassea RLS)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY en .env")
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode()
        return json.loads(text) if text else None


def llm_call(prompt, max_tokens=2000, retries=3):
    """Llamada a OpenAI API con reintentos ante errores 5xx."""
    import time
    body = {
        "model": "gpt-4.1",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {OPENAI_KEY}",
                "Content-Type":  "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                resp = json.loads(r.read())
            text = resp["choices"][0]["message"]["content"].strip()
            if text.startswith("```"):
                text = text.split("```", 2)[1]
                if text.startswith("json"):
                    text = text[4:].strip()
                text = text.rstrip("`").strip()
            return text
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            last_err = RuntimeError(f"OpenAI API {e.code}: {err_body}")
            if e.code < 500:
                raise last_err  # 4xx no reintentamos
            print(f"  [llm_call] intento {attempt+1}/{retries} falló con {e.code}, reintentando...")
            time.sleep(5 * (attempt + 1))
    raise last_err


def send_email(to_email, subject, html):
    if not RESEND_KEY:
        print(f"  [skip email — RESEND_API_KEY no configurado]")
        return
    body = {
        "from": "OKR Manager <okr@smfconsulting.es>",
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())
