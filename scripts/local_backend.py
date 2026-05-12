#!/usr/bin/env python3
"""
Mini backend local — solo para desarrollo/demo.
Puerto 8081. Expone endpoints que disparan los jobs en background.
El frontend hace polling a Supabase para detectar cuando terminan.

Uso: python3 local_backend.py
"""
import subprocess, sys, json, urllib.parse, os, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

LOCK_DIR = Path("/tmp/okr_backend_locks")
LOCK_DIR.mkdir(exist_ok=True)


def _acquire_lock(job_key):
    """Devuelve True si se adquirió el lock, False si ya hay un job corriendo."""
    lock_file = LOCK_DIR / f"{job_key}.lock"
    # Si existe el lock, verificar si el proceso sigue vivo
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
            os.kill(pid, 0)   # no mata, solo verifica existencia
            return False       # proceso vivo → rechazar
        except (ProcessLookupError, ValueError):
            lock_file.unlink(missing_ok=True)   # proceso muerto → limpiar
    return True


def _release_lock_on_exit(lock_file, proc):
    """Hilo que espera a que el proceso termine y libera el lock."""
    import threading
    def _wait():
        proc.wait()
        lock_file.unlink(missing_ok=True)
    threading.Thread(target=_wait, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[backend] {fmt % args}")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        team   = (params.get("team", ["marketing"])[0])

        if parsed.path == "/krs":
            try:
                from _etendo import all_team_configs, etendo_login, fetch_team_krs
                configs = [c for c in all_team_configs()
                           if c["team"]["name"].lower().startswith(team.lower())]
                if not configs:
                    raise ValueError(f"Team '{team}' no encontrado")
                cfg    = configs[0]
                jwt    = etendo_login(cfg["etendo"]["role_id"])
                krs    = fetch_team_krs(jwt, cfg["period"]["name"], cfg["team"]["id"])
                self.send_response(200)
                self._send_cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(krs).encode())
            except Exception as e:
                self.send_response(500)
                self._send_cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        team   = (params.get("team", ["marketing"])[0])

        if parsed.path == "/generate-initiatives":
            next_week = params.get("next_week", ["0"])[0] == "1"
            args = ["job_monday.py", "--team", team]
            if next_week:
                args.append("--next-week")
            self._run(args)
        elif parsed.path == "/generate-proposals":
            self._run(["job_friday.py", "--team", team, "--any-status"])
        elif parsed.path == "/writeback":
            self._run(["job_writeback.py"])
        elif parsed.path == "/market-intel":
            self._run(["job_market_intel.py", "--team", team])
        elif parsed.path == "/generate-backlog":
            self._run(["job_generate_backlog.py"])
        else:
            self.send_response(404)
            self.end_headers()

    def _run(self, script_args):
        job_key = script_args[0].replace(".py", "")

        if not _acquire_lock(job_key):
            self.send_response(409)
            self._send_cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"started": False, "reason": "already_running"}).encode())
            return

        self.send_response(200)
        self._send_cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPTS_DIR / script_args[0])] + script_args[1:],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        lock_file = LOCK_DIR / f"{job_key}.lock"
        lock_file.write_text(str(proc.pid))
        _release_lock_on_exit(lock_file, proc)
        self.wfile.write(json.dumps({"started": True}).encode())

    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin",  "http://localhost:8080")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


if __name__ == "__main__":
    server = HTTPServer(("localhost", 8081), Handler)
    print("Backend local corriendo en http://localhost:8081")
    server.serve_forever()
