#!/usr/bin/env python3
"""
ClawApp AutoTrader — Core trading logic.

Called by the ClawApp skill on schedule, or by server.py when the
dashboard triggers a manual check. Reads policy.json, fetches a live
price, generates a signal, checks guardrails, and executes (paper or real).

Usage:
  python3 trader.py                         # normal run
  python3 trader.py --approve <signal_id>   # approve a held/pending signal
  python3 trader.py --accounts              # fetch Coinbase account balances

Signal methods (set via policy.json → signalMethod):
  random     — randomized signal, good for testing (default)
  heuristic  — RSI(14) + 25-min momentum on 5-min candles
  ai         — Claude/OpenAI analysis (requires anthropic_api_key or openai_api_key)
  sorin      — Sorin DeFi Tools token analysis + AI interpretation (requires sorin_api_key + AI key)
"""

import json
import sys
import time
import uuid
import random
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT       = Path(__file__).parent.parent.parent
DATA_DIR   = ROOT / "data"
SECRETS    = ROOT / "secrets.json"

def data_paths(exec_mode):
    """Return mode-specific file paths (paper or real)."""
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

def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, default=str))

def today_iso():
    return datetime.now(timezone.utc).date().isoformat()

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ── Price fetching ─────────────────────────────────────────────────────────────

def fetch_price(asset):
    """Fetch current price from Coinbase Exchange public API. No auth required."""
    url = f"https://api.exchange.coinbase.com/products/{asset}-USD/stats"
    req = urllib.request.Request(url, headers={"User-Agent": "ClawApp-AutoTrader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return float(data["last"])
    except Exception as e:
        raise RuntimeError(f"Price fetch failed for {asset}: {e}")

def fetch_candles(asset, granularity=300, count=25):
    """Fetch recent OHLCV candles. Returns list sorted oldest→newest.
    granularity: seconds per candle (60, 300, 900, 3600, 21600, 86400)
    """
    from datetime import timedelta
    end   = datetime.now(timezone.utc)
    start = end - timedelta(seconds=granularity * (count + 3))
    url = (
        f"https://api.exchange.coinbase.com/products/{asset}-USD/candles"
        f"?granularity={granularity}"
        f"&start={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"&end={end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "ClawApp-AutoTrader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            # Coinbase returns [[timestamp, low, high, open, close, volume], ...]
            # newest first — sort oldest first
            candles = json.loads(r.read())
            return sorted(candles, key=lambda x: x[0])
    except Exception as e:
        raise RuntimeError(f"Candle fetch failed for {asset}: {e}")

def compute_rsi(closes, period=14):
    """Compute RSI from a list of close prices (oldest first)."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(0.0, d) for d in deltas[-period:]]
    losses = [max(0.0, -d) for d in deltas[-period:]]
    avg_g  = sum(gains)  / period
    avg_l  = sum(losses) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))

# ── Signal params (from policy, with defaults) ─────────────────────────────────

def _signal_params(policy):
    """Read optional signal hyperparams from policy; return dict with defaults."""
    return {
        "candleGranularity":       int(policy.get("candleGranularity", 300)),   # seconds per candle (60,300,900,3600)
        "candleCount":             int(policy.get("candleCount", 25)),           # number of candles to fetch
        "rsiPeriod":               int(policy.get("rsiPeriod", 14)),           # RSI period
        "momentumLookbackCandles": int(policy.get("momentumLookbackCandles", 5)), # candles for momentum
        "aiCandlesInPrompt":       int(policy.get("aiCandlesInPrompt", 10)),   # last N candles in AI prompt
        "sorinAnalysisMaxChars":   int(policy.get("sorinAnalysisMaxChars", 3000)), # max Sorin text to send to AI
    }


# ── Signal generation ─────────────────────────────────────────────────────────

def _make_signal(asset, price, direction, confidence, reasoning, per_trade_cap, method="random"):
    cap = max(10, min(20, per_trade_cap))
    return {
        "id":              f"s-{uuid.uuid4().hex[:10]}",
        "asset":           asset,
        "direction":       direction,
        "size":            round(random.uniform(10, cap), 2),
        "price":           price,
        "reasoning":       reasoning,
        "confidence":      confidence,
        "signalMethod":    method,
        "timestamp":       now_iso(),
        "status":          "pending",
        "guardrailReason": None,
    }

# ── Random (testing) ──────────────────────────────────────────────────────────

_RANDOM_REASONINGS = [
    lambda a, d, p: f"{a} {'dropped' if d == 'BUY' else 'surged'} {p}% in the last 5 minutes, triggering the move threshold.",
    lambda a, d, p: f"{p}% {'decline' if d == 'BUY' else 'rally'} in {a} detected. Price action consistent with prior support {'bounce' if d == 'BUY' else 'rejection'}.",
    lambda a, d, p: f"{a} move of {p}% exceeds threshold. {'Dip buying' if d == 'BUY' else 'Profit taking'} signal.",
]

def generate_signal_random(asset, price, per_trade_cap):
    pct        = round(random.uniform(0.8, 2.2), 1)
    direction  = "BUY" if random.random() > 0.45 else "SELL"
    confidence = "High" if pct > 1.5 else "Medium" if pct > 1.0 else "Low"
    reasoning  = random.choice(_RANDOM_REASONINGS)(asset, direction, pct)
    return _make_signal(asset, price, direction, confidence, reasoning, per_trade_cap, method="random")

# ── Heuristic: RSI + momentum on configurable candles ──────────────────────────

def generate_signal_heuristic(asset, price, per_trade_cap, params):
    """RSI + short-term momentum; time window and RSI period from params."""
    g = params["candleGranularity"]
    n = params["candleCount"]
    rsi_per = params["rsiPeriod"]
    lb = params["momentumLookbackCandles"]

    candles = fetch_candles(asset, granularity=g, count=n)
    if len(candles) < max(lb, rsi_per):
        raise RuntimeError(f"Insufficient candle data for {asset} ({len(candles)} candles, need {max(lb, rsi_per)})")

    closes   = [c[4] for c in candles]
    rsi      = compute_rsi(closes, period=rsi_per)
    lookback = min(lb, len(closes) - 1)
    momentum = (closes[-1] - closes[-(lookback + 1)]) / closes[-(lookback + 1)] * 100

    window_min = (g * lookback) // 60

    # Direction + confidence
    if rsi < 35 and momentum < -0.5:
        direction, confidence = "BUY", "High"
    elif rsi < 45 and momentum < -0.3:
        direction, confidence = "BUY", "Medium"
    elif rsi > 65 and momentum > 0.5:
        direction, confidence = "SELL", "High"
    elif rsi > 55 and momentum > 0.3:
        direction, confidence = "SELL", "Medium"
    elif momentum < 0:
        direction, confidence = "BUY", "Low"
    else:
        direction, confidence = "SELL", "Low"

    if direction == "BUY":
        reasoning = (
            f"{asset} RSI({rsi_per}) at {rsi:.1f} with {abs(momentum):.2f}% decline over {window_min} min. "
            f"Oversold momentum suggests dip entry. {confidence} confidence."
        )
    else:
        reasoning = (
            f"{asset} RSI({rsi_per}) at {rsi:.1f} with {abs(momentum):.2f}% rally over {window_min} min. "
            f"Overbought momentum suggests partial exit. {confidence} confidence."
        )

    return _make_signal(asset, price, direction, confidence, reasoning, per_trade_cap, method="heuristic")

# ── AI: Claude or OpenAI analysis ─────────────────────────────────────────────

def _build_ai_prompt(asset, price, candles, params):
    """Build the shared prompt string for both AI providers; window sizes from params."""
    closes   = [c[4] for c in candles] if candles else [price]
    rsi_per  = params["rsiPeriod"]
    lb       = params["momentumLookbackCandles"]
    n_show   = params["aiCandlesInPrompt"]
    rsi      = compute_rsi(closes, period=rsi_per) if len(closes) > rsi_per else 50.0
    lookback = min(lb, len(closes) - 1)
    momentum = (closes[-1] - closes[-(lookback + 1)]) / closes[-(lookback + 1)] * 100 if len(closes) > 1 else 0.0
    candle_lines = "\n".join(
        f"  {i+1}. close={c[4]:.2f}, high={c[2]:.2f}, low={c[1]:.2f}, vol={c[5]:.4f}"
        for i, c in enumerate(candles[-n_show:])
    ) if candles else "  (no candle data)"
    g = params["candleGranularity"]
    window_min = (g * lookback) // 60
    return (
        f"You are a crypto trading signal generator. Analyze {asset}/USD and output a trade signal.\n\n"
        f"Current price: ${price:,.2f}\n"
        f"RSI({rsi_per}): {rsi:.1f}\n"
        f"Momentum over last {window_min} min: {momentum:+.2f}%\n"
        f"Recent candles (last {n_show}, oldest→newest):\n{candle_lines}\n\n"
        f'Respond with ONLY a valid JSON object — no markdown, no explanation:\n'
        f'{{"direction":"BUY" or "SELL","confidence":"Low" or "Medium" or "High",'
        f'"reasoning":"1-2 sentence rationale referencing the data above"}}'
    )

def _parse_ai_response(text):
    """Parse JSON from AI response, stripping markdown fences if present."""
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1].lstrip("json").strip()
    result = json.loads(text)
    return (
        result.get("direction",  "BUY"),
        result.get("confidence", "Medium"),
        result.get("reasoning",  "AI-generated signal."),
    )

def generate_signal_ai(asset, price, per_trade_cap, params):
    """Generate a signal using AI analysis. Uses Anthropic Claude if anthropic_api_key
    is set in secrets.json, or OpenAI if openai_api_key is set. Claude takes priority."""
    secrets = load(SECRETS, {})
    anthropic_key = secrets.get("anthropic_api_key")
    openai_key    = secrets.get("openai_api_key")

    if not anthropic_key and not openai_key:
        raise RuntimeError("No AI API key found in secrets.json (add anthropic_api_key or openai_api_key)")

    g, n = params["candleGranularity"], params["candleCount"]
    try:
        candles = fetch_candles(asset, granularity=g, count=n)
    except Exception:
        candles = []

    prompt = _build_ai_prompt(asset, price, candles, params)

    if anthropic_key:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("pip install anthropic")
        client  = anthropic.Anthropic(api_key=anthropic_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text
    else:
        try:
            import openai
        except ImportError:
            raise RuntimeError("pip install openai")
        client = openai.OpenAI(api_key=openai_key)
        resp   = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content

    direction, confidence, reasoning = _parse_ai_response(text)
    return _make_signal(asset, price, direction, confidence, reasoning, per_trade_cap, method="ai")

# ── Sorin: DeFi Tools token analysis + AI interpretation ──────────────────────

_SORIN_BASE = "https://defi-tools-proxy.saharaa.info"

def generate_signal_sorin(asset, price, per_trade_cap, params):
    """Fetch Sorin DeFi token analysis, then use an AI model to interpret it into a signal.
    Requires sorin_api_key in secrets.json for the Sorin API, plus either
    anthropic_api_key or openai_api_key for AI interpretation."""
    secrets = load(SECRETS, {})
    sorin_key     = secrets.get("sorin_api_key")
    anthropic_key = secrets.get("anthropic_api_key")
    openai_key    = secrets.get("openai_api_key")
    max_chars     = params["sorinAnalysisMaxChars"]

    if not sorin_key:
        raise RuntimeError("sorin_api_key not found in secrets.json")
    if not anthropic_key and not openai_key:
        raise RuntimeError("Sorin method requires an AI key to interpret analysis (add anthropic_api_key or openai_api_key)")

    # Fetch token analysis from Sorin DeFi Tools
    url = f"{_SORIN_BASE}/token/analysis?token_symbol={asset}&quote_currency=USDT"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {sorin_key}", "accept": "text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            analysis = r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Sorin API error {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        raise RuntimeError(f"Sorin API request failed: {e}")

    # Ask an AI model to interpret the analysis into a structured signal
    prompt = (
        f"You are a crypto trading signal generator. The following is a DeFi market analysis "
        f"for {asset}/USDT (current price: ${price:,.2f}). Based on this analysis, generate a trade signal.\n\n"
        f"Sorin DeFi Analysis:\n{analysis[:max_chars]}\n\n"
        f"Respond with ONLY a valid JSON object — no markdown, no explanation:\n"
        f'{{"direction":"BUY" or "SELL","confidence":"Low" or "Medium" or "High",'
        f'"reasoning":"1-2 sentence rationale citing specific data points from the analysis above"}}'
    )

    if anthropic_key:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("pip install anthropic")
        client  = anthropic.Anthropic(api_key=anthropic_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text
    else:
        try:
            import openai
        except ImportError:
            raise RuntimeError("pip install openai")
        client = openai.OpenAI(api_key=openai_key)
        resp   = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content

    direction, confidence, reasoning = _parse_ai_response(text)
    return _make_signal(asset, price, direction, confidence, reasoning, per_trade_cap, method="sorin")

# ── Guardrail check ───────────────────────────────────────────────────────────

_CONF_RANK = {"Low": 1, "Medium": 2, "High": 3}

def check_guardrails(signal, policy, daily_spent):
    if signal["asset"] not in policy.get("assets", []):
        return f"{signal['asset']} not in allowed assets"
    if signal["size"] > policy.get("perTradeCap", 20):
        return f"trade size ${signal['size']:.0f} exceeds per-trade cap ${policy['perTradeCap']}"
    if daily_spent + signal["size"] > policy.get("dailyCap", 100):
        return f"daily cap reached (${daily_spent:.0f}/${policy['dailyCap']} spent)"
    min_conf = (policy.get("minConfidence") or "Low").lower()
    # "off" is a legacy value — treat it the same as "low" (no effective filter)
    if min_conf not in ("off", "low"):
        sig_rank = _CONF_RANK.get(signal.get("confidence", "Low"), 1)
        min_rank = _CONF_RANK.get(min_conf.capitalize(), 1)
        if sig_rank < min_rank:
            return f"confidence '{signal['confidence']}' below minimum '{min_conf.capitalize()}'"
    return None

# ── Coinbase key loading ──────────────────────────────────────────────────────

def _load_coinbase_private_key(api_secret):
    """Load Ed25519 private key from PEM or raw base64. Handles malformed PEM."""
    try:
        import base64
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        raise RuntimeError("pip install pyjwt cryptography")

    s = api_secret.strip()

    if "-----BEGIN" in s:
        s = s.replace("\\n", "\n")
        begin_marker = "-----BEGIN PRIVATE KEY-----"
        end_marker = "-----END PRIVATE KEY-----"
        start = s.find(begin_marker)
        end = s.find(end_marker)
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError("Invalid PEM: need -----BEGIN PRIVATE KEY----- and -----END PRIVATE KEY-----")
        after_begin = start + len(begin_marker)
        while after_begin < len(s) and s[after_begin] in " \n\r\t":
            after_begin += 1
        b64 = s[after_begin:end].replace(" ", "").replace("\r", "").replace("\n", "").strip()
        wrapped = "\n".join(b64[i : i + 64] for i in range(0, len(b64), 64))
        pem = "-----BEGIN PRIVATE KEY-----\n" + wrapped + "\n-----END PRIVATE KEY-----"
        try:
            return load_pem_private_key(pem.encode("utf-8"), password=None)
        except Exception:
            pass
        # PEM failed — try as raw Ed25519 (Coinbase may use non-PKCS#8 PEM)
        try:
            raw = base64.b64decode(b64)
        except Exception:
            raw = base64.urlsafe_b64decode(b64)
        if len(raw) == 32:
            return Ed25519PrivateKey.from_private_bytes(raw)
        if len(raw) == 64:
            return Ed25519PrivateKey.from_private_bytes(raw[:32])
        raise RuntimeError(
            "Private key in PEM could not be loaded as PKCS#8 or as raw Ed25519 (32 or 64 bytes). "
            "Paste the key as raw base64 (no BEGIN/END) if Coinbase gave you that."
        )

    try:
        raw = base64.b64decode(s + "==" if not s.endswith("=") else s)
    except Exception:
        raw = base64.urlsafe_b64decode(s + "==" if not s.endswith("=") else s)
    if len(raw) == 32:
        return Ed25519PrivateKey.from_private_bytes(raw)
    if len(raw) == 64:
        # Coinbase provides seed (32 bytes) || public_key (32 bytes)
        return Ed25519PrivateKey.from_private_bytes(raw[:32])
    raise RuntimeError(
        "Ed25519 key must be 32 or 64 bytes. Use the raw base64 privateKey from the Coinbase downloaded JSON."
    )

# ── Coinbase order placement ──────────────────────────────────────────────────

def place_coinbase_order(signal, secrets):
    """
    Place a real market order via Coinbase Advanced Trade API.
    Coinbase now uses Ed25519 keys (EdDSA). coinbase-advanced-py uses ES256 (legacy ECDSA),
    so we always use the manual EdDSA JWT path.
    """
    api_key   = secrets["coinbase_api_key"]
    api_secret = secrets["coinbase_api_secret"]

    token = _build_jwt_manual(api_key, api_secret)

    if hasattr(token, "decode"):
        token = token.decode("utf-8")

    # Build order body
    product_id       = f"{signal['asset']}-USD"
    client_order_id  = f"clawapp-{uuid.uuid4().hex[:12]}"

    if signal["direction"] == "BUY":
        order_config = {"market_market_ioc": {"quote_size": f"{signal['size']:.2f}"}}
    else:
        precision    = 8 if signal["asset"] == "BTC" else 6
        base_size    = signal["size"] / signal["price"]
        order_config = {"market_market_ioc": {"base_size": f"{base_size:.{precision}f}"}}

    body = json.dumps({
        "client_order_id":    client_order_id,
        "product_id":         product_id,
        "side":               signal["direction"],
        "order_configuration": order_config,
    }).encode()

    req = urllib.request.Request(
        "https://api.coinbase.com/api/v3/brokerage/orders",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Coinbase API error {e.code}: {body}")

    order_id = (
        resp.get("order_id")
        or resp.get("success_response", {}).get("order_id")
        or resp.get("order", {}).get("order_id")
    )
    if not order_id:
        raise RuntimeError(f"Order placed but no order_id in response: {resp}")
    return order_id


def _build_jwt_manual(api_key, api_secret, uri="POST api.coinbase.com/api/v3/brokerage/orders"):
    """Build a Coinbase Ed25519/EdDSA JWT for the Advanced Trade API."""
    try:
        import jwt as pyjwt
    except ImportError:
        raise RuntimeError(
            "Install JWT support: pip install pyjwt cryptography"
        )

    private_key = _load_coinbase_private_key(api_secret)
    now = int(time.time())
    payload = {
        "sub": api_key,
        "iss": "cdp",
        "nbf": now,
        "exp": now + 120,
        "uri": uri,
    }
    return pyjwt.encode(
        payload,
        private_key,
        algorithm="EdDSA",
        headers={"kid": api_key, "nonce": uuid.uuid4().hex},
    )


# ── Account balance fetching ───────────────────────────────────────────────────

def fetch_accounts():
    """Fetch account balances from Coinbase Advanced Trade API and print as JSON."""
    secrets = load(SECRETS, {})
    if not secrets.get("coinbase_api_key"):
        print(json.dumps({"error": "No API credentials configured"}))
        sys.exit(1)

    api_key    = secrets["coinbase_api_key"]
    api_secret = secrets["coinbase_api_secret"]

    try:
        token = _build_jwt_manual(
            api_key, api_secret,
            uri="GET api.coinbase.com/api/v3/brokerage/accounts",
        )
        if hasattr(token, "decode"):
            token = token.decode("utf-8")

        req = urllib.request.Request(
            "https://api.coinbase.com/api/v3/brokerage/accounts?limit=250",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())

        accounts = []
        for acct in resp.get("accounts", []):
            bal   = acct.get("available_balance", {})
            value = float(bal.get("value", 0))
            if value > 0:
                accounts.append({
                    "currency": acct["currency"],
                    "balance":  value,
                    "name":     acct.get("name", acct["currency"]),
                })

        print(json.dumps({"accounts": accounts}))

    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(json.dumps({"error": f"Coinbase API error {e.code}: {body}"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

# ── Trade record ──────────────────────────────────────────────────────────────

def make_trade(signal, exec_mode, order_id=None, approval_mode="manual"):
    return {
        "id":           f"t-{uuid.uuid4().hex[:10]}",
        "asset":        signal["asset"],
        "direction":    signal["direction"],
        "size":         signal["size"],
        "fillPrice":    signal["price"],
        "reasoning":    signal["reasoning"],
        "timestamp":    now_iso(),
        "mode":         exec_mode,
        "approvalMode": approval_mode,
        "orderId":      order_id,
    }

# ── Approve a signal manually ─────────────────────────────────────────────────

def approve_signal(signal_id):
    policy    = load(DATA_DIR / "policy.json",  {"assets":["BTC","ETH"],"perTradeCap":20,"dailyCap":100,"execMode":"paper"})
    exec_mode = policy.get("execMode", "paper")
    paths     = data_paths(exec_mode)
    signals   = load(paths["signals"],     [])
    trades    = load(paths["trades"],      [])
    daily     = load(paths["daily_spend"], {"spent": 0, "date": today_iso()})

    sig = next((s for s in signals if s["id"] == signal_id), None)
    if not sig:
        print(json.dumps({"error": f"Signal {signal_id} not found"}))
        sys.exit(1)

    order_id = None
    if exec_mode == "real":
        try:
            secrets  = load(SECRETS, {})
            order_id = place_coinbase_order(sig, secrets)
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            sys.exit(1)

    sig["status"] = "approved"
    trade = make_trade(sig, exec_mode, order_id, approval_mode="manual")
    trades.append(trade)
    daily["spent"] = daily.get("spent", 0) + sig["size"]

    save(paths["signals"],     signals)
    save(paths["trades"],      trades)
    save(paths["daily_spend"], daily)

    print(json.dumps({"status": "ok", "trade": trade}))

# ── Main run ──────────────────────────────────────────────────────────────────

def run():
    policy        = load(DATA_DIR / "policy.json",  {"assets":["BTC","ETH"],"perTradeCap":20,"dailyCap":100,"execMode":"paper","approvalMode":"manual"})
    exec_mode     = policy.get("execMode",     "paper")
    approval_mode = policy.get("approvalMode", "manual")
    paths         = data_paths(exec_mode)
    signals       = load(paths["signals"],     [])
    trades        = load(paths["trades"],      [])
    daily         = load(paths["daily_spend"], {"spent": 0, "date": today_iso()})

    # Reset daily spend if it's a new day
    if daily.get("date") != today_iso():
        daily = {"spent": 0, "date": today_iso()}

    assets = policy.get("assets", ["BTC"])
    asset  = random.choice(assets)

    # Fetch price
    try:
        price = fetch_price(asset)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    # Generate signal
    signal_method = policy.get("signalMethod", "random")
    per_trade_cap = policy.get("perTradeCap", 20)
    params = _signal_params(policy)
    try:
        if signal_method == "heuristic":
            signal = generate_signal_heuristic(asset, price, per_trade_cap, params)
        elif signal_method == "ai":
            signal = generate_signal_ai(asset, price, per_trade_cap, params)
        elif signal_method == "sorin":
            signal = generate_signal_sorin(asset, price, per_trade_cap, params)
        else:
            signal = generate_signal_random(asset, price, per_trade_cap)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    if approval_mode == "policy":
        reason = check_guardrails(signal, policy, daily.get("spent", 0))
        if reason:
            signal["status"]          = "held"
            signal["guardrailReason"] = reason
        else:
            order_id = None
            if exec_mode == "real":
                try:
                    secrets  = load(SECRETS, {})
                    order_id = place_coinbase_order(signal, secrets)
                except Exception as e:
                    signal["status"]          = "held"
                    signal["guardrailReason"] = f"Order failed: {e}"
                    signals.append(signal)
                    save(paths["signals"], signals)
                    print(json.dumps({"error": str(e)}), file=sys.stderr)
                    sys.exit(1)

            signal["status"] = "auto-executed"
            trade = make_trade(signal, exec_mode, order_id, approval_mode="policy")
            trades.append(trade)
            daily["spent"] = daily.get("spent", 0) + signal["size"]
            save(paths["trades"],      trades)
            save(paths["daily_spend"], daily)
    # else: manual mode — leave as "pending" for user to approve

    signals.append(signal)
    save(paths["signals"], signals)
    print(json.dumps({"status": "ok", "signal": signal}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve",   metavar="SIGNAL_ID", help="Approve a pending signal by ID")
    parser.add_argument("--accounts",  action="store_true",  help="Fetch Coinbase account balances")
    args = parser.parse_args()

    if args.approve:
        approve_signal(args.approve)
    elif args.accounts:
        fetch_accounts()
    else:
        run()
