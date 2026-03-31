# ClawApp AutoTrader

A self-contained crypto trading automation demo built on [ClawApp](https://clawapp.ai). Generates trade signals from live Coinbase prices, enforces configurable guardrails, and lets you approve or auto-execute trades — all visible in a real-time dashboard.

Works out of the box in **paper mode** (no credentials required). Supports live Coinbase trading, AI-generated signals, and Sorin DeFi analysis with optional API keys.

---

## Quickstart (paper mode — no credentials needed)

**Requirements:** Python 3.8+

```bash
git clone <this-repo>
cd sorin-clawapp-autotrading-demo
python3 server.py
# or on a custom port:
python3 server.py --port 9090
```

Open [http://localhost:8080](http://localhost:8080) in your browser.

Click **Check Market Now** to generate a signal. In the default configuration (paper mode, manual approval), a signal card will appear for you to approve or dismiss.

Click **Reset Demo** at any time to clear all signals, restore a set of pre-populated seed trades, and reset the daily spend tracker.

---

## Signal methods

Set via `data/policy.json` → `signalMethod`:

| Method | What it does | Extra requirements |
|--------|-------------|-------------------|
| `random` | Randomized direction and reasoning — good for UI testing | None |
| `heuristic` | RSI(14) + short-term price momentum on 5-min candles from Coinbase | None |
| `ai` | Sends candle data + RSI to an AI model for analysis | `anthropic_api_key` or `openai_api_key` in `secrets.json` |
| `sorin` | Fetches Sorin DeFi token analysis, then interprets it with an AI model | `sorin_api_key` + AI key in `secrets.json` |

---

## Modes

**Execution mode** (`execMode` in `data/policy.json`):
- `paper` — simulated fill, no real order placed, no exchange account needed
- `real` — live Coinbase market order (see [Real trading setup](#real-trading-setup))

**Approval mode** (`approvalMode` in `data/policy.json`):
- `manual` — signal queues as pending; you approve or dismiss it in the dashboard
- `policy` — signal auto-executes if all guardrails pass; held with a reason if not

Both modes are also configurable live from the dashboard without editing JSON.

---

## Guardrails

Configured in `data/policy.json` and editable in the dashboard:

- **Assets** — whitelist of allowed ticker symbols (e.g. `["BTC", "ETH"]`)
- **Per-trade cap** — maximum USD size per trade
- **Daily cap** — maximum total USD spend per day (resets at UTC midnight)
- **Min confidence** — block signals below `Low`, `Medium`, or `High`

---

## ClawApp scheduling

To run signals on a schedule (rather than only via the dashboard button):

1. Open ClawApp and go to **Automations**
2. Create a new scheduled automation:
   - **Command:** `python3 skills/clawapp-autotrader/trader.py`
   - **Working directory:** the repository root
   - **Trigger:** time-based, at your chosen interval
3. The skill writes to the `data/` JSON files; the dashboard picks up changes on its next 3-second poll

---

## Real trading setup

1. Go to the [Coinbase CDP Portal](https://portal.cdp.coinbase.com) and create an API key
2. Signature algorithm: **Ed25519** (the only option on CDP)
3. Enable **View** and **Trade** permissions; leave **Transfer** disabled
4. Click **Download** — Coinbase gives you a JSON file containing two fields:
   - `id` — a UUID like `feaecdae-ad4a-4b3e-...`
   - `privateKey` — a raw base64 string (may start with `-----BEGIN EC PRIVATE KEY-----` or be plain base64)
5. Copy `secrets.json.example` to `secrets.json` and fill in:
   ```json
   {
     "coinbase_api_key": "<the id field — UUID only, no prefix>",
     "coinbase_api_secret": "<the privateKey field — paste exactly as-is>"
   }
   ```
6. Install dependencies:
   ```bash
   pip install pyjwt cryptography
   ```
7. Switch `execMode` to `real` in the dashboard or directly in `data/policy.json`

> **Key format tip:** paste `privateKey` exactly as downloaded — do not strip PEM headers or re-encode it. The trader handles both PEM and raw base64 formats automatically.

> **Never commit `secrets.json`.** It is gitignored by default.

---

## AI signal setup

For the `ai` and `sorin` signal methods, add your AI provider key to `secrets.json`:

```json
{
  "anthropic_api_key": "sk-ant-...",
  "openai_api_key": "sk-..."
}
```

Install the corresponding package:
```bash
pip install anthropic   # if using an Anthropic key
pip install openai      # if using an OpenAI key (1.57+ required for httpx 0.28 compat)
```

If both keys are present, Anthropic is used. OpenAI is the fallback.

---

## Sorin signal setup

The `sorin` signal method fetches a live DeFi token analysis from the Sorin API, then passes it to your AI model to generate a trading signal. It requires both a Sorin API key and an AI key (see above).

### Step 1 — Get a Sorin API key

Sign in and create an API key at **[tools.saharaai.com/sorin-skills](http://tools.saharaai.com/sorin-skills)**.

### Step 2 — Add the key to secrets.json

```json
{
  "sorin_api_key": "sak_live_..."
}
```

### Step 3 — Set signalMethod to `sorin`

Update `data/policy.json` or use the dashboard dropdown to switch the signal method to `sorin`.

---

## Running trader.py directly

```bash
# Generate a signal
python3 skills/clawapp-autotrader/trader.py

# Approve a pending signal by ID
python3 skills/clawapp-autotrader/trader.py --approve s-abc123def4

# Fetch live Coinbase account balances (real mode)
python3 skills/clawapp-autotrader/trader.py --accounts
```

Output is always JSON on stdout:
```json
{"status": "ok", "signal": {"id": "s-abc123", "asset": "BTC", "direction": "BUY", ...}}
```

---

## File reference

| File | Purpose |
|------|---------|
| `server.py` | Local HTTP server — serves the dashboard and bridges file I/O |
| `index.html` | Single-file dashboard UI |
| `skills/clawapp-autotrader/trader.py` | Core logic — price fetch, signal generation, Coinbase API |
| `skills/clawapp-autotrader/SKILL.md` | ClawApp skill definition |
| `skills/clawapp-autotrader/clawapp.json` | ClawApp skill metadata (name, entry command) |
| `skills/sahara-intention-level-skills/` | Sorin DeFi skill (optional) |
| `data/policy.json` | Active policy: assets, caps, exec/approval mode, signal method |
| `data/signals_paper.json` / `data/signals_real.json` | Signal history |
| `data/trades_paper.json` / `data/trades_real.json` | Trade fill history |
| `data/daily_spend_paper.json` / `data/daily_spend_real.json` | Rolling daily spend |
| `data/cron_meta.json` | ClawApp schedule metadata (written by ClawApp, read by dashboard) |
| `data/reset_meta.json` | Timestamp of last Reset Demo action |
| `secrets.json.example` | Template for credentials (copy to `secrets.json`) |
| `requirements.txt` | Optional Python dependencies |

---

## Advanced policy options

Each signal method exposes tunable parameters that can be added directly to `data/policy.json` (not exposed in the dashboard UI). All have sensible defaults so none are required.

**`heuristic` method**

| Key | Default | Description |
|-----|---------|-------------|
| `candleGranularity` | `300` | Candle size in seconds (`60`, `300`, `900`, `3600`, `21600`, `86400`) |
| `candleCount` | `25` | Number of candles to fetch per run |
| `rsiPeriod` | `14` | RSI lookback period |
| `momentumLookbackCandles` | `5` | Number of candles used to calculate price momentum |

**`ai` method**

| Key | Default | Description |
|-----|---------|-------------|
| `candleGranularity` | `300` | Candle size in seconds |
| `candleCount` | `25` | Number of candles to fetch per run |
| `rsiPeriod` | `14` | RSI lookback period (included in the AI prompt) |
| `momentumLookbackCandles` | `5` | Momentum window (included in the AI prompt) |
| `aiCandlesInPrompt` | `10` | Number of recent candles included in the AI prompt |

**`sorin` method**

| Key | Default | Description |
|-----|---------|-------------|
| `sorinAnalysisMaxChars` | `3000` | Max characters of Sorin analysis text sent to the AI model |

---

## Troubleshooting

**"⚠ Server offline" badge in the topbar**
The dashboard can't reach `server.py`. Make sure `python3 server.py` is running and you opened `http://localhost:8080` (not the HTML file directly).

**AI signal fails with "No AI API key found"**
Add `anthropic_api_key` or `openai_api_key` to `secrets.json` and run `pip install anthropic` or `pip install openai`.

**Sorin signal fails with "sorin_api_key not found"**
Add `sorin_api_key` to `secrets.json`. See [`skills/sahara-intention-level-skills/README.md`](skills/sahara-intention-level-skills/README.md) for how to obtain a key.

**Real trade fails with "Missing required scopes"**
Your Coinbase API key is missing the **Trade** permission. Edit the key on the CDP portal, enable View and Trade, then retry.

**Real trade fails with "Coinbase API error 401: Unauthorized"**
Check that `secrets.json` uses:
- `coinbase_api_key`: bare UUID only (e.g. `feaecdae-ad4a-...`), not a path or prefixed string
- `coinbase_api_secret`: raw base64 string from the downloaded JSON, no PEM headers

Also confirm: `pip install pyjwt cryptography` in the same Python environment used to run `server.py`.

**Positions panel shows $0 in Real mode**
The dashboard fetches prices for all currencies in your Coinbase account. If values are still 0, the price fetch may be failing — check the browser console for errors.

**Daily spend doesn't reset**
`daily_spend_*.json` resets automatically when the UTC date changes. To reset manually, click **Reset Demo** in the dashboard topbar.
