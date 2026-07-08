#!/usr/bin/env python3
"""
MCP server — CRM Etendo para Claude Desktop de Vico.
Deploy en Render como web service independiente.

Tools expuestos:
  listar_leads      — pipeline activo ordenado por score
  ver_lead          — detalle completo de un lead por empresa
  agregar_nota      — guarda nota en ETCRM_Lead_Note
  pipeline_resumen  — agrupado por estado
"""
import os, json, threading
import urllib.request, urllib.parse, http.cookiejar
from datetime import datetime, timezone

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp import types
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route
import uvicorn
import anyio

# ── Env ────────────────────────────────────────────────────────────────────
ETENDO_BASE     = os.getenv("ETENDO_BASE_URL", "https://futit-staff.etendo.cloud")
ETENDO_USER     = os.getenv("ETENDO_USERNAME", "")
ETENDO_PASS     = os.getenv("ETENDO_PASSWORD", "")
WRITE_URL       = os.getenv("ETENDO_WRITE_URL", "https://staff-ui.etendo.cloud/etendo")
MCP_AUTH_TOKEN  = os.getenv("MCP_AUTH_TOKEN", "")
SUPABASE_URL    = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY    = os.getenv("SUPABASE_SERVICE_KEY", "")
_COMERCIAL_ROLE = "8351131DFF384725AB08E06773FE6144"
_DEAD_STATUSES  = {"dead", "won", "disqualified", "lost"}

# ── Etendo helpers ─────────────────────────────────────────────────────────
def _login():
    body = json.dumps({
        "username": ETENDO_USER,
        "password": ETENDO_PASS,
        "role":     _COMERCIAL_ROLE,
    }).encode()
    req = urllib.request.Request(f"{ETENDO_BASE}/api/auth/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["token"]


def _fetch(jwt, entity):
    out, start = [], 0
    while True:
        body = urllib.parse.urlencode({
            "_operationType": "fetch",
            "_startRow": str(start),
            "_endRow":   str(start + 499),
            "_noActiveFilter": "true",
        }).encode()
        req = urllib.request.Request(
            f"{ETENDO_BASE}/api/datasource/{entity}", data=body, method="POST"
        )
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


def _sid_login():
    jar    = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    body   = urllib.parse.urlencode({
        "user": ETENDO_USER, "password": ETENDO_PASS, "Command": "Login"
    }).encode()
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


def _days_since(date_str):
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


# ── CRM snapshot ───────────────────────────────────────────────────────────
def _snapshot():
    jwt   = _login()
    leads = _fetch(jwt, "ETCRM_Lead")
    notes = _fetch(jwt, "ETCRM_Lead_Note")

    # Nota más reciente por lead
    notes_map = {}
    for n in notes:
        lid  = n.get("lead")
        text = (n.get("note") or "").strip()
        if not lid or not text:
            continue
        date = max(n.get("creationDate") or "", n.get("updatedDate") or "")
        if lid not in notes_map or date > notes_map[lid]["date"]:
            notes_map[lid] = {"text": text, "date": date}

    result = []
    for l in leads:
        lid    = l.get("id", "")
        status = (l.get("statusField") or l.get("status") or "").lower()
        if status in _DEAD_STATUSES:
            continue
        note_entry = notes_map.get(lid, {})
        note_text  = note_entry.get("text", "")
        note_date  = note_entry.get("date", "")
        updated    = l.get("updated", "")
        best_date  = (
            max(note_date, updated) if note_date and updated
            else (note_date or updated)
        )
        result.append({
            "id":       lid,
            "empresa":  (l.get("businessPartner$_identifier") or l.get("_identifier") or "").strip(),
            "contacto": f"{l.get('firstName','') or ''} {l.get('lastName','') or ''}".strip(),
            "email":    l.get("email", ""),
            "telefono": l.get("phone", ""),
            "estado":   l.get("statusField") or l.get("status") or "",
            "score":    l.get("scorePurchaseIntention") or l.get("leadScore") or 0,
            "fit":      l.get("strategicFit", ""),
            "owner":    l.get("salesRepresentative$_identifier", ""),
            "nota":     note_text,
            "dias":     _days_since(best_date),
        })

    result.sort(key=lambda x: -(x.get("score") or 0))
    return result


# ── Tool handlers ──────────────────────────────────────────────────────────
def _tool_listar_leads(_args):
    leads = _snapshot()
    if not leads:
        return "No hay leads activos en el CRM."
    lines = [f"Pipeline activo — {len(leads)} leads\n"]
    for l in leads:
        dias = f"{l['dias']}d sin actividad" if l["dias"] is not None else ""
        nota = f'\n   → "{l["nota"][:200]}"' if l["nota"] else ""
        lines.append(
            f"• {l['empresa']}  |  {l['estado']}  |  Score {l['score']}  |  {dias}"
            f"\n  Contacto: {l['contacto']}  |  Owner: {l['owner']}{nota}\n"
        )
    return "\n".join(lines)


def _tool_ver_lead(args):
    empresa = (args.get("empresa") or "").lower().strip()
    leads   = _snapshot()
    matches = [l for l in leads if empresa in l["empresa"].lower()]
    if not matches:
        return f"No encontré ningún lead con '{args.get('empresa')}'."
    l    = matches[0]
    dias = f"{l['dias']} días sin actividad" if l["dias"] is not None else "sin fecha de actividad"
    return (
        f"{l['empresa']}\n"
        f"Contacto: {l['contacto'] or '—'}\n"
        f"Email:    {l['email'] or '—'}\n"
        f"Teléfono: {l['telefono'] or '—'}\n"
        f"Estado:   {l['estado']}\n"
        f"Score:    {l['score']}\n"
        f"Fit:      {l['fit']}\n"
        f"Owner:    {l['owner']}\n"
        f"Actividad: {dias}\n\n"
        f"Última nota:\n{l['nota'] or '(sin notas registradas)'}"
    )


def _tool_agregar_nota(args):
    empresa  = (args.get("empresa") or "").strip()
    nota_txt = (args.get("nota") or "").strip()
    if not empresa or not nota_txt:
        return "Falta empresa o nota."

    leads   = _snapshot()
    matches = [l for l in leads if empresa.lower() in l["empresa"].lower()]
    if not matches:
        return f"No encontré ningún lead con '{empresa}'."
    lead = matches[0]

    sid = _sid_login()
    if not sid:
        return "Error de autenticación al conectar con el CRM."

    payload = json.dumps({
        "lead": lead["id"],
        "note": nota_txt,
    }).encode()
    req = urllib.request.Request(
        f"{WRITE_URL}/org.openbravo.service.datasource/ETCRM_Lead_Note",
        data=payload, method="POST",
    )
    req.add_header("Cookie",       f"JSESSIONID={sid}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept",       "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        status = resp.get("response", {}).get("status")
        if status == 0:
            return f"Nota guardada en {lead['empresa']}:\n\"{nota_txt}\""
        else:
            return f"Error del CRM al guardar: {resp.get('response',{}).get('error','respuesta inesperada')}"
    except Exception as e:
        return f"Error al guardar la nota: {e}"


def _tool_pipeline_resumen(_args):
    leads  = _snapshot()
    grupos = {}
    for l in leads:
        st = l["estado"] or "sin estado"
        grupos.setdefault(st, []).append(l)

    lines = [f"Pipeline — {len(leads)} leads activos\n"]
    for estado, grupo in sorted(grupos.items(), key=lambda x: -len(x[1])):
        top = sorted(grupo, key=lambda x: -(x.get("score") or 0))[:3]
        top_str = ", ".join(f"{l['empresa']} ({l['score']})" for l in top)
        lines.append(f"{estado} — {len(grupo)} lead{'s' if len(grupo)>1 else ''}\n  Top: {top_str}\n")
    return "\n".join(lines)


_HANDLERS = {
    "listar_leads":     _tool_listar_leads,
    "ver_lead":         _tool_ver_lead,
    "agregar_nota":     _tool_agregar_nota,
    "pipeline_resumen": _tool_pipeline_resumen,
}

# ── MCP server ─────────────────────────────────────────────────────────────
server = Server("etendo-crm-vico")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="listar_leads",
            description=(
                "Lista todos los leads activos del CRM ordenados por score. "
                "Muestra empresa, contacto, estado, score, última nota y días sin actividad."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="ver_lead",
            description=(
                "Detalle completo de un lead: contacto, email, teléfono, estado, score y última nota. "
                "Buscar por nombre de empresa."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "empresa": {"type": "string", "description": "Nombre de la empresa o lead"}
                },
                "required": ["empresa"],
            },
        ),
        types.Tool(
            name="agregar_nota",
            description=(
                "Agrega una nota al lead en el CRM. "
                "Usarlo después de una llamada, reunión o email para registrar qué pasó y el próximo paso."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "empresa": {"type": "string"},
                    "nota":    {"type": "string", "description": "Texto de la nota a guardar"},
                },
                "required": ["empresa", "nota"],
            },
        ),
        types.Tool(
            name="pipeline_resumen",
            description="Resumen del pipeline agrupado por etapa: cuántos leads hay en cada estado y cuáles son los más calientes.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    handler = _HANDLERS.get(name)
    if not handler:
        return [types.TextContent(type="text", text=f"Tool desconocida: {name}")]
    try:
        result = await anyio.to_thread.run_sync(lambda: handler(arguments))
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {e}")]


# ── SSE transport ──────────────────────────────────────────────────────────
sse = SseServerTransport("/messages/")


def _check_token(request: Request) -> bool:
    if not MCP_AUTH_TOKEN:
        return True
    if request.query_params.get("token", "") == MCP_AUTH_TOKEN:
        return True
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() == MCP_AUTH_TOKEN
    return False


async def handle_sse(request: Request):
    if not _check_token(request):
        return Response("Unauthorized", status_code=401)
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def handle_messages(request: Request):
    if not _check_token(request):
        return Response("Unauthorized", status_code=401)
    await sse.handle_post_message(request.scope, request.receive, request._send)


async def handle_root(_r: Request):
    return Response(
        '{"service":"etendo-crm-mcp","status":"running","endpoints":["/sse","/health","/brief","/brief-response","/brief-responses"]}',
        media_type="application/json",
    )


async def handle_brief(_r: Request):
    brief_path = os.path.join(os.path.dirname(__file__), "brief_marca_etendo_jul2026.html")
    try:
        with open(brief_path, "r", encoding="utf-8") as f:
            content = f.read()
        return Response(content, media_type="text/html; charset=utf-8")
    except FileNotFoundError:
        return Response("Brief no encontrado", status_code=404)


# ── OAuth endpoints (required by mcp-remote) ───────────────────────────────
_OAUTH_BASE = "https://crm-pulse.onrender.com"

async def handle_oauth_metadata(_r: Request):
    return Response(json.dumps({
        "issuer":                             _OAUTH_BASE,
        "authorization_endpoint":             f"{_OAUTH_BASE}/oauth/authorize",
        "token_endpoint":                     f"{_OAUTH_BASE}/oauth/token",
        "registration_endpoint":              f"{_OAUTH_BASE}/register",
        "response_types_supported":           ["code"],
        "grant_types_supported":              ["authorization_code"],
        "code_challenge_methods_supported":   ["S256"],
        "scopes_supported":                   ["mcp"],
    }), media_type="application/json")


async def handle_register(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    redirect_uris = body.get("redirect_uris", [])
    return Response(json.dumps({
        "client_id":                  "crm-etendo-mcp",
        "client_secret_expires_at":   0,
        "grant_types":                ["authorization_code"],
        "response_types":             ["code"],
        "redirect_uris":              redirect_uris,
        "token_endpoint_auth_method": "none",
    }), media_type="application/json", status_code=201)


async def handle_authorize(request: Request):
    params   = dict(request.query_params)
    redirect = params.get("redirect_uri", "")
    state    = params.get("state", "")
    sep      = "&" if "?" in redirect else "?"
    location = f"{redirect}{sep}code=crm-approved&state={state}"
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Autorizar CRM Etendo</title>
<style>
  body {{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#F7F9FC;}}
  .card {{background:#fff;border:1px solid #E5E9F0;border-radius:10px;padding:40px 48px;text-align:center;max-width:400px;}}
  .logo {{font-size:13px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#1863DC;margin-bottom:16px;}}
  h1 {{font-size:20px;font-weight:700;color:#111827;margin:0 0 8px;}}
  p {{color:#6B7280;font-size:14px;margin:0 0 28px;}}
  a.btn {{display:inline-block;background:#1863DC;color:#fff;text-decoration:none;padding:12px 32px;border-radius:7px;font-size:15px;font-weight:600;}}
  a.btn:hover {{background:#0D47A8;}}
</style>
</head>
<body>
<div class="card">
  <div class="logo">Etendo CRM</div>
  <h1>Autorizar conexión</h1>
  <p>Claude Desktop quiere conectarse al CRM.<br>Hacé click para aprobar.</p>
  <a href="{location}" class="btn">Autorizar</a>
</div>
<script>
  // Attempt auto-redirect after short delay; user click is the reliable fallback
  setTimeout(function(){{ window.location.href = "{location}"; }}, 800);
</script>
</body>
</html>"""
    return Response(html, media_type="text/html")


async def handle_token(request: Request):
    token = MCP_AUTH_TOKEN or "vico-crm-etendo-2026"
    return Response(json.dumps({
        "access_token": token,
        "token_type":   "bearer",
        "expires_in":   31536000,
        "scope":        "mcp",
    }), media_type="application/json")


# ── Brief response endpoints ────────────────────────────────────────────────
_CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

async def handle_brief_response(request: Request):
    if request.method == "OPTIONS":
        return Response("", headers=_CORS_HEADERS)
    try:
        body = await request.json()
    except Exception:
        return Response('{"error":"invalid json"}', status_code=400,
                        media_type="application/json", headers=_CORS_HEADERS)

    question_key  = str(body.get("question_key", "")).strip()
    respondent    = str(body.get("respondent", "Anónimo")).strip() or "Anónimo"
    response_text = str(body.get("response_text", "")).strip()
    brief_id      = str(body.get("brief_id", "brief_marca_jul2026")).strip()

    if not question_key or not response_text:
        return Response('{"error":"question_key and response_text are required"}',
                        status_code=400, media_type="application/json", headers=_CORS_HEADERS)

    if not SUPABASE_URL or not SUPABASE_KEY:
        return Response('{"error":"supabase not configured"}', status_code=503,
                        media_type="application/json", headers=_CORS_HEADERS)

    payload = json.dumps([{
        "brief_id":      brief_id,
        "question_key":  question_key,
        "respondent":    respondent,
        "response_text": response_text,
    }]).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/brief_responses",
        data=payload, method="POST",
    )
    req.add_header("apikey",        SUPABASE_KEY)
    req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
    req.add_header("Content-Type",  "application/json")
    req.add_header("Prefer",        "return=minimal")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
        return Response('{"ok":true}', media_type="application/json", headers=_CORS_HEADERS)
    except Exception as e:
        return Response(json.dumps({"error": str(e)}), status_code=500,
                        media_type="application/json", headers=_CORS_HEADERS)


async def handle_brief_responses(request: Request):
    if not _check_token(request):
        return Response("Unauthorized", status_code=401)
    if not SUPABASE_URL or not SUPABASE_KEY:
        return Response('{"error":"supabase not configured"}', status_code=503,
                        media_type="application/json")
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/brief_responses?select=*&order=created_at.asc",
    )
    req.add_header("apikey",        SUPABASE_KEY)
    req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return Response(
            _render_responses_html(data),
            media_type="text/html",
        )
    except Exception as e:
        return Response(json.dumps({"error": str(e)}), status_code=500,
                        media_type="application/json")


async def handle_brief_responses_json(request: Request):
    """Public JSON endpoint — returns responses grouped by question_key for the brief page."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return Response('[]', media_type="application/json", headers=_CORS_HEADERS)
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/brief_responses?select=question_key,respondent,response_text,created_at&order=created_at.asc",
    )
    req.add_header("apikey",        SUPABASE_KEY)
    req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return Response(json.dumps(data), media_type="application/json", headers=_CORS_HEADERS)
    except Exception as e:
        return Response('[]', media_type="application/json", headers=_CORS_HEADERS)


def _render_responses_html(rows):
    LABELS = {
        "resp-brand-arch":  "Brand architecture (secc. 01)",
        "resp-1":           "Brand architecture (secc. 06)",
        "resp-2":           "Colores Etendo Go",
        "resp-3":           "Publicar Odoo vs Etendo",
        "resp-4":           "Fix GTM conversion labels",
    }
    by_q = {}
    for r in rows:
        k = r.get("question_key", "?")
        by_q.setdefault(k, []).append(r)

    parts = ["<!doctype html><html lang='es'><head><meta charset='UTF-8'>",
             "<title>Respuestas — Brief Etendo</title>",
             "<style>body{font-family:system-ui,sans-serif;max-width:700px;margin:40px auto;padding:0 20px;color:#111;}",
             "h1{font-size:20px;margin-bottom:4px;}p.sub{color:#666;font-size:13px;margin-bottom:32px;}",
             "h2{font-size:14px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#1863DC;margin:24px 0 8px;}",
             ".card{background:#f7f9fc;border:1px solid #dde3ed;border-radius:6px;padding:14px 16px;margin-bottom:10px;}",
             ".who{font-size:11px;font-weight:700;color:#9ca3af;margin-bottom:4px;}",
             ".text{font-size:14px;line-height:1.5;color:#111;}",
             ".empty{color:#aaa;font-style:italic;font-size:13px;}",
             "</style></head><body>",
             "<h1>Respuestas — Brief de Posicionamiento Etendo</h1>",
             "<p class='sub'>Actualizado en tiempo real desde Supabase.</p>"]

    for key, label in LABELS.items():
        parts.append(f"<h2>{label}</h2>")
        entries = by_q.get(key, [])
        if not entries:
            parts.append("<p class='empty'>Sin respuestas aún.</p>")
        else:
            for e in entries:
                ts = (e.get("created_at") or "")[:16].replace("T", " ")
                parts.append(f"<div class='card'><div class='who'>{e.get('respondent','—')} · {ts}</div>")
                parts.append(f"<div class='text'>{e.get('response_text','')}</div></div>")

    parts.append("</body></html>")
    return "".join(parts)


app = Starlette(routes=[
    Route("/",                      endpoint=handle_root),
    Route("/sse",                   endpoint=handle_sse),
    Route("/messages/",             endpoint=handle_messages,           methods=["POST"]),
    Route("/health",                endpoint=lambda _r: Response("ok")),
    Route("/.well-known/oauth-authorization-server", endpoint=handle_oauth_metadata, methods=["GET"]),
    Route("/register",              endpoint=handle_register,           methods=["POST", "OPTIONS"]),
    Route("/oauth/authorize",       endpoint=handle_authorize,          methods=["GET"]),
    Route("/oauth/token",           endpoint=handle_token,              methods=["POST", "OPTIONS"]),
    Route("/brief",                 endpoint=handle_brief,              methods=["GET"]),
    Route("/brief-response",        endpoint=handle_brief_response,     methods=["POST", "OPTIONS"]),
    Route("/brief-responses",       endpoint=handle_brief_responses,    methods=["GET"]),
    Route("/brief-responses-json",  endpoint=handle_brief_responses_json, methods=["GET", "OPTIONS"]),
])

def _keepalive():
    """Ping self every 10 min so Render free tier never sleeps."""
    import time
    time.sleep(60)
    while True:
        try:
            urllib.request.urlopen("https://crm-pulse.onrender.com/health", timeout=10)
        except Exception:
            pass
        time.sleep(10 * 60)

threading.Thread(target=_keepalive, daemon=True).start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
