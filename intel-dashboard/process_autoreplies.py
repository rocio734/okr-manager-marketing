"""
process_autoreplies.py
──────────────────────
Procesa auto-replies (bounces y out-of-office) recibidos desde n8n
y actualiza outreach_tracking.json con la etapa/nota correspondiente.

Fuentes de datos:
  1. outreach_autoreplies.json   — cola local (manual o fallback)
  2. Supabase tabla outreach_autoreplies — registros enviados por n8n vía Render

También puede llamarse directamente:
  python3 process_autoreplies.py          # procesa pendientes (local + Supabase)
  python3 process_autoreplies.py --show   # muestra estado actual

Etapas asignadas:
  bounce     → etapa 99 (descartado) — email inválido
  vacaciones → nota "vuelve: FECHA" — recontactar al volver
"""
import json, re, os
from datetime import datetime, timezone
from pathlib import Path
from outreach_tracking_helper import (
    load_tracking, save_tracking, update_stage, print_summary
)

# cargar .env si existe
_ENV = Path(__file__).resolve().parent.parent.parent / ".env"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

ROOT         = Path(__file__).resolve().parent
AUTOREPLIES  = ROOT / "outreach_autoreplies.json"


# ── Supabase helpers ─────────────────────────────────────────────────────────

def _sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def fetch_supabase_autoreplies() -> list:
    """
    Lee registros no procesados de outreach_autoreplies en Supabase
    y los convierte al mismo formato que load_autoreplies().
    Devuelve lista vacía si Supabase no está configurado o la tabla no existe.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        import urllib.request, urllib.parse
        url = f"{SUPABASE_URL}/rest/v1/outreach_autoreplies?procesado=eq.false&select=*&order=id.asc"
        req = urllib.request.Request(url, headers=_sb_headers())
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        # normalizar al formato de la cola local
        result = []
        for row in rows:
            result.append({
                "email":        row.get("email", ""),
                "tipo":         row.get("tipo", "bounce"),
                "raw_subject":  row.get("raw_subject", ""),
                "raw_body":     row.get("raw_body", ""),
                "raw_from":     row.get("raw_from", ""),
                "fecha_vuelta": row.get("fecha_vuelta", ""),
                "detected_at":  row.get("detected_at", ""),
                "procesado":    False,
                "_sb_id":       row.get("id"),  # para marcar como procesado
            })
        return result
    except Exception as e:
        print(f"⚠️  Supabase fetch error: {e}")
        return []


def mark_supabase_processed(sb_ids: list) -> None:
    """Marca como procesados los registros de Supabase por ID."""
    if not SUPABASE_URL or not SUPABASE_KEY or not sb_ids:
        return
    try:
        import urllib.request
        ids_filter = ",".join(str(i) for i in sb_ids)
        url = f"{SUPABASE_URL}/rest/v1/outreach_autoreplies?id=in.({ids_filter})"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = json.dumps({"procesado": True, "procesado_at": ts}).encode()
        req = urllib.request.Request(url, data=body, headers=_sb_headers(), method="PATCH")
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"✅ Marcados como procesados en Supabase: {sb_ids}")
    except Exception as e:
        print(f"⚠️  Supabase mark error: {e}")


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_autoreplies() -> list:
    if not AUTOREPLIES.exists():
        return []
    data = json.loads(AUTOREPLIES.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("autoreplies", [])


def save_autoreplies(autoreplies: list) -> None:
    AUTOREPLIES.write_text(
        json.dumps(autoreplies, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def clasificar_autoreply(subject: str, body: str = "") -> str:
    """
    Clasifica el tipo de auto-reply.
    Devuelve: 'bounce' | 'vacaciones' | 'otro'
    """
    text = (subject + " " + body).lower()

    bounce_signals = [
        "mail delivery", "undelivered", "undeliverable",
        "delivery failed", "delivery status", "mailer-daemon",
        "no se pudo entregar", "fallo de entrega",
        "address not found", "user unknown", "no such user",
        "550", "554", "rejected", "does not exist",
    ]
    vacation_signals = [
        "out of office", "fuera de", "vacaciones", "vacation",
        "ausente", "absence", "away from", "de vacaciones",
        "no estaré", "estaré ausente", "respuesta automática",
        "automatic reply", "autoreply", "auto-reply",
        "fuera de la oficina", "de baja",
    ]

    for s in bounce_signals:
        if s in text:
            return "bounce"
    for s in vacation_signals:
        if s in text:
            return "vacaciones"
    return "otro"


def extraer_fecha_vuelta(subject: str, body: str = "") -> str:
    """
    Intenta extraer la fecha de vuelta de un mensaje de out-of-office.
    Devuelve string ISO o "" si no encuentra.
    """
    text = subject + " " + body
    # Patrones: "back on August 25", "vuelvo el 25 de agosto", "retorno el 25/08"
    patterns = [
        r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b',        # 25/08/2026
        r'\b(\d{1,2})\s+de\s+(\w+)\b',                         # 25 de agosto
        r'\bback\s+(?:on\s+)?(\w+\s+\d{1,2})\b',               # back on August 25
        r'\breturn(?:ing)?\s+(?:on\s+)?(\w+\s+\d{1,2})\b',     # returning August 25
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return ""


# ── Main processor ────────────────────────────────────────────────────────────

def process(dry_run: bool = False) -> None:
    local_autoreplies = load_autoreplies()
    sb_autoreplies    = fetch_supabase_autoreplies()

    if sb_autoreplies:
        print(f"  → {len(sb_autoreplies)} auto-replies desde Supabase")

    # Combinar: locales pendientes + Supabase (dedup por email+tipo)
    local_pendientes = [a for a in local_autoreplies if not a.get("procesado")]
    seen_keys = {(a["email"], a.get("raw_subject","")[:30]) for a in local_pendientes}
    for sb in sb_autoreplies:
        key = (sb["email"], sb.get("raw_subject","")[:30])
        if key not in seen_keys:
            local_pendientes.append(sb)
            seen_keys.add(key)

    pendientes = local_pendientes
    sb_ids_procesados = []

    if not pendientes:
        print("No hay auto-replies pendientes de procesar.")
        return

    tracking = load_tracking()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    bounces    = 0
    vacaciones = 0
    positivos  = 0
    negativos  = 0
    otros      = 0

    for reply in pendientes:
        email   = reply.get("email", "").lower().strip()
        subject = reply.get("raw_subject", "")
        body    = reply.get("raw_body", "")
        tipo    = reply.get("tipo") or clasificar_autoreply(subject, body)

        if not email:
            reply["procesado"] = True
            continue

        print(f"\n  [{tipo:10s}] {email}")
        print(f"             Subject: {subject[:70]}")

        if dry_run:
            reply["procesado"] = False  # no marcar en dry run
            continue

        if tipo == "bounce":
            update_stage(
                tracking, email, nueva_etapa=99,
                canal="autoreply",
                subject=subject,
                notas="Email inválido — bounce detectado automáticamente",
            )
            bounces += 1

        elif tipo == "vacaciones":
            fecha_vuelta = reply.get("fecha_vuelta") or extraer_fecha_vuelta(subject, body)
            nota = f"Out of office — vuelve: {fecha_vuelta}" if fecha_vuelta else "Out of office — fecha de vuelta desconocida"
            # No avanzamos etapa, solo agregamos nota al historial de contactos
            if email in tracking:
                tracking[email].setdefault("contactos", []).append({
                    "etapa":       tracking[email].get("etapa", 0),
                    "etapa_label": "autoreply_vacaciones",
                    "canal":       "autoreply",
                    "subject":     subject,
                    "fecha":       ts,
                    "notas":       nota,
                })
                tracking[email]["notas"] = nota
            else:
                update_stage(
                    tracking, email, nueva_etapa=2,
                    canal="autoreply",
                    subject=subject,
                    notas=nota,
                )
            vacaciones += 1

        elif tipo == "positivo":
            # Respondió con interés → etapa 4
            update_stage(
                tracking, email, nueva_etapa=4,
                canal="reply",
                subject=subject,
                notas="✅ Respuesta positiva detectada automáticamente — en seguimiento",
            )
            positivos += 1

        elif tipo == "negativo":
            # No interesado → etapa 99 (descartado)
            update_stage(
                tracking, email, nueva_etapa=99,
                canal="reply",
                subject=subject,
                notas="❌ Respuesta negativa — no interesado",
            )
            negativos += 1

        else:
            otros += 1

        reply["procesado"] = True
        reply["procesado_at"] = ts
        if reply.get("_sb_id"):
            sb_ids_procesados.append(reply["_sb_id"])

    if not dry_run:
        save_tracking(tracking)
        save_autoreplies(local_autoreplies)
        if sb_ids_procesados:
            mark_supabase_processed(sb_ids_procesados)
        print(f"\n✅ Procesados: {bounces} bounces → descartados | "
              f"{vacaciones} vacaciones → anotados | "
              f"{positivos} positivos → etapa 4 | "
              f"{negativos} negativos → descartados | "
              f"{otros} otros sin acción")
        print_summary(tracking)
    else:
        print(f"\n[DRY RUN] {len(pendientes)} auto-replies — "
              f"{bounces} bounces, {vacaciones} vacaciones, "
              f"{positivos} positivos, {negativos} negativos, {otros} otros")


def add_autoreply(email: str, tipo: str, subject: str = "",
                  body: str = "", fecha_vuelta: str = "",
                  raw_from: str = "") -> None:
    """
    Añade un auto-reply a la cola (llamado desde n8n via webhook o manualmente).
    """
    autoreplies = load_autoreplies()
    autoreplies.append({
        "email":        email.lower().strip(),
        "tipo":         tipo,
        "raw_subject":  subject,
        "raw_body":     body[:500],  # truncar body largo
        "raw_from":     raw_from,
        "fecha_vuelta": fecha_vuelta,
        "detected_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "procesado":    False,
    })
    save_autoreplies(autoreplies)
    print(f"✅ Auto-reply registrado: {email} → {tipo}")


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if "--show" in args:
        tracking = load_tracking()
        print_summary(tracking)
        bounced  = [v for v in tracking.values() if v.get("etapa") == 99]
        vacas    = [v for v in tracking.values()
                    if "vacaciones" in v.get("notas", "").lower()]
        print(f"Bounces (descartados): {len(bounced)}")
        for l in bounced:
            print(f"  {l.get('email')} — {l.get('empresa','?')}")
        print(f"\nVacaciones: {len(vacas)}")
        for l in vacas:
            print(f"  {l.get('email')} — {l.get('notas','')}")
    elif "--add" in args:
        # Uso: python3 process_autoreplies.py --add email tipo [subject]
        idx = args.index("--add")
        em  = args[idx+1] if len(args) > idx+1 else ""
        tp  = args[idx+2] if len(args) > idx+2 else "bounce"
        sub = args[idx+3] if len(args) > idx+3 else ""
        add_autoreply(em, tp, sub)
    else:
        dry = "--dry-run" in args
        if dry:
            print("=== DRY RUN ===\n")
        process(dry_run=dry)
