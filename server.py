#!/usr/bin/env python3
"""
ClawApp AutoTrader — Local dashboard server.

Serves the HTML dashboard and provides a REST API that bridges the
browser UI with the JSON state files and trader.py.

Usage:
  python3 server.py          # starts on http://localhost:8080
  python3 server.py --port 9090

API endpoints:
  GET  /                       → serve index.html
  GET  /api/state              → signals + trades + policy + daily_spend merged
  POST /api/policy             → update policy.json
  POST /api/check              → run trader.py (generate a signal)
  POST /api/approve/<id>       → approve a pending signal
  POST /api/dismiss/<id>       → dismiss a pending signal
  POST /api/reset              → clear signals, trades, daily_spend to seed state
"""

import json
import subprocess
import sys
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT     = Path(__file__).parent
DATA_DIR = ROOT / "data"
# NOTE: the core trader lives under demo/skills/clawapp-autotrader/
TRADER   = ROOT / "skills" / "clawapp-autotrader" / "trader.py"

def data_paths(exec_mode):
    m = exec_mode if exec_mode in ("paper", "real") else "paper"
    return {
        "signals":     DATA_DIR / f"signals_{m}.json",
        "trades":      DATA_DIR / f"trades_{m}.json",
        "daily_spend": DATA_DIR / f"daily_spend_{m}.json",
    }

# ── File helpers ──────────────────────────────────────────────────────────────

def load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default if default is not None else {}

def load_cron_meta():
    """Load data/cron_meta.json if present; return None if missing or invalid."""
    try:
        p = DATA_DIR / "cron_meta.json"
        if not p.exists():
            return None
        return json.loads(p.read_text())
    except Exception:
        return None

def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, default=str))

def today_iso():
    return datetime.now(timezone.utc).date().isoformat()

# ── Seed data for reset ───────────────────────────────────────────────────────

SEED_TRADES = [
    {"id":"t1","asset":"BTC","direction":"BUY","size":15.0,"fillPrice":83420.10,
     "reasoning":"BTC dropped 1.4% in 5 minutes, triggering the move threshold. Moderate confidence.",
     "timestamp":"2026-03-10T19:48:00Z","mode":"paper","approvalMode":"manual","orderId":None},
    {"id":"t2","asset":"ETH","direction":"BUY","size":18.0,"fillPrice":2184.55,
     "reasoning":"ETH fell 1.1% following BTC decline. Dip buying within daily cap. Low-medium confidence.",
     "timestamp":"2026-03-10T21:12:00Z","mode":"paper","approvalMode":"manual","orderId":None},
    {"id":"t3","asset":"BTC","direction":"SELL","size":12.0,"fillPrice":84810.75,
     "reasoning":"BTC surged 1.6% — signalling short-term overextension. Taking partial profit. Medium confidence.",
     "timestamp":"2026-03-10T22:54:00Z","mode":"paper","approvalMode":"policy","orderId":None},
    {"id":"t4","asset":"BTC","direction":"BUY","size":20.0,"fillPrice":84205.30,
     "reasoning":"BTC retraced 1.2% from recent high. Re-entering within policy cap. Medium confidence.",
     "timestamp":"2026-03-10T23:38:00Z","mode":"paper","approvalMode":"manual","orderId":None},
]

# ── Request handler ───────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Tidy log output
        print(f"  {self.command} {self.path} → {args[1]}")

    # ── Routing ────────────────────────────────────────────────────────────────

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_file(ROOT / "index.html", "text/html")
        elif path == "/api/state":
            self._json(self._get_state())
        elif path == "/api/accounts":
            self._handle_accounts()
        elif path == "/api/setup":
            self._handle_setup()
        else:
            self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        path    = urlparse(self.path).path
        length  = int(self.headers.get("Content-Length", 0))
        body    = json.loads(self.rfile.read(length)) if length > 0 else {}

        if path == "/api/policy":
            self._handle_policy(body)
        elif path == "/api/check":
            self._handle_check()
        elif path.startswith("/api/approve/"):
            self._handle_approve(path.split("/")[-1])
        elif path.startswith("/api/dismiss/"):
            self._handle_dismiss(path.split("/")[-1])
        elif path == "/api/reset":
            self._handle_reset()
        else:
            self._send(404, "text/plain", b"Not found")

    def do_OPTIONS(self):
        self._send(204, "text/plain", b"")

    # ── Handlers ───────────────────────────────────────────────────────────────

    def _get_state(self):
        policy    = load(DATA_DIR / "policy.json", {"assets":["BTC","ETH"],"perTradeCap":20,"dailyCap":100,"execMode":"paper","approvalMode":"manual"})
        paths     = data_paths(policy.get("execMode", "paper"))
        signals   = load(paths["signals"],     [])
        trades    = load(paths["trades"],      [])
        daily     = load(paths["daily_spend"], {"spent":0,"date":today_iso()})

        # Reset daily spend if new day
        if daily.get("date") != today_iso():
            daily = {"spent": 0, "date": today_iso()}
            save(paths["daily_spend"], daily)

        out = {
            "policy":  {**policy, "dailySpent": daily.get("spent", 0)},
            "signals": signals,
            "trades":  trades,
        }
        cron = load_cron_meta()
        if cron is not None:
            out["cron"] = cron
        reset_meta = load(DATA_DIR / "reset_meta.json")
        if reset_meta and reset_meta.get("lastResetAt"):
            out["lastResetAt"] = reset_meta["lastResetAt"]
        return out

    def _handle_policy(self, body):
        current = load(DATA_DIR / "policy.json", {})
        # dailySpent lives in daily_spend.json, not policy.json — strip it
        body.pop("dailySpent", None)
        current.update(body)
        save(DATA_DIR / "policy.json", current)
        self._json({"status": "ok"})

    def _handle_check(self):
        """Run trader.py and return the generated signal."""
        try:
            result = subprocess.run(
                [sys.executable, str(TRADER)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=20,
                cwd=str(ROOT),
            )
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip()
                self._json({"error": err}, status=500)
                return
            out = json.loads(result.stdout.strip())
            self._json(out)
        except subprocess.TimeoutExpired:
            self._json({"error": "trader.py timed out"}, status=500)
        except Exception as e:
            self._json({"error": str(e)}, status=500)

    def _handle_setup(self):
        """Return which API keys are configured in secrets.json."""
        secrets_path = ROOT / "secrets.json"
        secrets = load(secrets_path, {})
        self._json({
            "hasSecrets":  secrets_path.exists(),
            "hasCoinbase": bool(secrets.get("coinbase_api_key") and secrets.get("coinbase_api_secret")),
            "hasAI":       bool(secrets.get("anthropic_api_key") or secrets.get("openai_api_key")),
            "hasSorin":    bool(secrets.get("sorin_api_key")),
        })

    def _handle_accounts(self):
        """Fetch Coinbase account balances via trader.py --accounts."""
        policy = load(DATA_DIR / "policy.json", {})
        if policy.get("execMode") != "real":
            self._json({"accounts": []})
            return
        try:
            result = subprocess.run(
                [sys.executable, str(TRADER), "--accounts"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=20,
                cwd=str(ROOT),
            )
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip()
                self._json({"error": err}, status=500)
                return
            out = json.loads(result.stdout.strip())
            self._json(out)
        except subprocess.TimeoutExpired:
            self._json({"error": "trader.py timed out"}, status=500)
        except Exception as e:
            self._json({"error": str(e)}, status=500)

    def _handle_approve(self, signal_id):
        """Approve a pending signal: execute trade, update files."""
        policy  = load(DATA_DIR / "policy.json", {})
        paths   = data_paths(policy.get("execMode", "paper"))
        signals = load(paths["signals"], [])
        sig = next((s for s in signals if s["id"] == signal_id), None)
        if not sig:
            self._json({"error": "signal not found"}, status=404)
            return

        try:
            result = subprocess.run(
                [sys.executable, str(TRADER), "--approve", signal_id],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=20,
                cwd=str(ROOT),
            )
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip()
                self._json({"error": err}, status=500)
                return
            out = json.loads(result.stdout.strip())
            self._json(out)
        except Exception as e:
            self._json({"error": str(e)}, status=500)

    def _handle_dismiss(self, signal_id):
        policy  = load(DATA_DIR / "policy.json", {})
        paths   = data_paths(policy.get("execMode", "paper"))
        signals = load(paths["signals"], [])
        updated = False
        for s in signals:
            if s["id"] == signal_id:
                s["status"] = "dismissed"
                updated = True
                break
        if not updated:
            self._json({"error": "signal not found"}, status=404)
            return
        save(paths["signals"], signals)
        self._json({"status": "ok"})

    def _handle_reset(self):
        paths = data_paths("paper")
        save(paths["signals"],     [])
        save(paths["trades"],      SEED_TRADES)
        save(paths["daily_spend"], {"spent": 65.0, "date": today_iso()})
        save(DATA_DIR / "reset_meta.json", {"lastResetAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        self._json({"status": "reset"})

    # ── Response helpers ───────────────────────────────────────────────────────

    def _serve_file(self, path, content_type):
        try:
            content = Path(path).read_bytes()
            self._send(200, content_type, content)
        except FileNotFoundError:
            self._send(404, "text/plain", b"Not found")

    def _json(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self._send(status, "application/json", body)

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)


# ── Entry point ───────────────────────────────────────────────────────────────

def _init_data_files():
    """Create mode-specific data files on first run."""
    for mode in ("paper", "real"):
        paths = data_paths(mode)
        if not paths["signals"].exists():
            save(paths["signals"], [])
        if not paths["daily_spend"].exists():
            save(paths["daily_spend"], {"spent": 0, "date": today_iso()})
    paper_trades = data_paths("paper")["trades"]
    if not paper_trades.exists():
        save(paper_trades, SEED_TRADES)
    real_trades = data_paths("real")["trades"]
    if not real_trades.exists():
        save(real_trades, [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    _init_data_files()
    server = HTTPServer(("localhost", args.port), Handler)
    print(f"\n  🦞 ClawApp AutoTrader server running")
    print(f"  → Dashboard: http://localhost:{args.port}")
    print(f"  → Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
