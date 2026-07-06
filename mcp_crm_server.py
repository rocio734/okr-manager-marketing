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
import os, json
import urllib.request, urllib.parse, http.cookiejar
from datetime import datetime, timezone

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp import types
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
import uvicorn
import anyio

# ── Env ────────────────────────────────────────────────────────────────────
ETENDO_BASE     = os.getenv("ETENDO_BASE_URL", "https://futit-staff.etendo.cloud")
ETENDO_USER     = os.getenv("ETENDO_USERNAME", "")
ETENDO_PASS     = os.getenv("ETENDO_PASSWORD", "")
WRITE_URL       = os.getenv("ETENDO_WRITE_URL", "https://staff-ui.etendo.cloud/etendo")
MCP_AUTH_TOKEN  = os.getenv("MCP_AUTH_TOKEN", "")
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
    return request.query_params.get("token", "") == MCP_AUTH_TOKEN


async def handle_sse(request: Request):
    if not _check_token(request):
        return Response("Unauthorized", status_code=401)
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def handle_messages(request: Request):
    if not _check_token(request):
        return Response("Unauthorized", status_code=401)
    await sse.handle_post_message(request.scope, request.receive, request._send)


app = Starlette(routes=[
    Route("/sse",       endpoint=handle_sse),
    Route("/messages/", endpoint=handle_messages, methods=["POST"]),
    Route("/health",    endpoint=lambda _r: Response("ok")),
])

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
