"""
process_autoreplies.py
──────────────────────
Procesa auto-replies (bounces y out-of-office) recibidos desde n8n
y actualiza outreach_tracking.json con la etapa/nota correspondiente.

n8n llama a este script indirectamente: deposita los datos en
outreach_autoreplies.json y luego este script los aplica al tracking.

También puede llamarse directamente:
  python3 process_autoreplies.py          # procesa pendientes
  python3 process_autoreplies.py --show   # muestra estado actual

Etapas asignadas:
  bounce     → etapa 99 (descartado) — email inválido
  vacaciones → etapa 2 + nota "vuelve: FECHA" — recontactar al volver
"""
import json, re
from datetime import datetime, timezone
from pathlib import Path
from outreach_tracking_helper import (
    load_tracking, save_tracking, update_stage, print_summary
)

ROOT         = Path(__file__).resolve().parent
AUTOREPLIES  = ROOT / "outreach_autoreplies.json"


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
    autoreplies = load_autoreplies()
    pendientes  = [a for a in autoreplies if not a.get("procesado")]

    if not pendientes:
        print("No hay auto-replies pendientes de procesar.")
        return

    tracking = load_tracking()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    bounces    = 0
    vacaciones = 0
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

        else:
            otros += 1

        reply["procesado"] = True
        reply["procesado_at"] = ts

    if not dry_run:
        save_tracking(tracking)
        save_autoreplies(autoreplies)
        print(f"\n✅ Procesados: {bounces} bounces → descartados | "
              f"{vacaciones} vacaciones → anotados | {otros} otros sin acción")
        print_summary(tracking)
    else:
        print(f"\n[DRY RUN] {len(pendientes)} auto-replies — "
              f"{bounces} bounces, {vacaciones} vacaciones, {otros} otros")


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
