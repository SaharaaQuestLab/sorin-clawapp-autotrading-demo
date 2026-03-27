---
name: clawapp-autotrader
description: Monitor cryptocurrency markets and execute trade signals based on price movements, subject to configurable policy guardrails.
---

# ClawApp AutoTrader Skill

## What this skill does

On each run, the skill:
1. Reads the current policy from `data/policy.json` (allowed assets, caps, modes)
2. Fetches the live price for a randomly selected allowed asset from Coinbase
3. Generates a signal (direction, size, reasoning, confidence)
4. In **Manual mode**: queues the signal as pending for the user to approve in the dashboard
5. In **Policy mode**: checks guardrails and either auto-executes or holds the signal
6. Writes the signal to `data/signals_{mode}.json` and any fill to `data/trades_{mode}.json`

## How to run

From the repository root:

```bash
# Generate a signal
python3 skills/clawapp-autotrader/trader.py

# Approve a pending signal by ID
python3 skills/clawapp-autotrader/trader.py --approve s-abc123def4

# Fetch Coinbase account balances (real mode, requires secrets.json)
python3 skills/clawapp-autotrader/trader.py --accounts
```

Output is always JSON on stdout:
- `{"status": "ok", "signal": {...}}` on success
- `{"error": "..."}` on failure

## Scheduling

Add this skill as a ClawApp scheduled automation:

- **Trigger:** Time-based, at your chosen interval
- **Command:** `python3 skills/clawapp-autotrader/trader.py`
- **Working directory:** The repository root

You can also use the "Check Market Now" button in the dashboard to trigger a signal run on demand.

## Modes

**Execution mode** (set in `data/policy.json` → `execMode`):
- `paper` — simulated fill, no real order placed, no exchange account needed
- `real`  — live Coinbase market order, requires `secrets.json` with API credentials

**Approval mode** (set in `data/policy.json` → `approvalMode`):
- `manual` — signal queues as pending, user approves/dismisses in the dashboard
- `policy` — signal auto-executes if all guardrails pass; held with reason otherwise

## Real trading setup

1. Go to the [Coinbase CDP Portal](https://portal.cdp.coinbase.com) and create an API key
2. Signature algorithm: **Ed25519** (the only option on CDP)
3. Enable **View** and **Trade** permissions; leave **Transfer** disabled
4. Download the key JSON — it contains `id` (UUID) and `privateKey` (raw base64)
5. Copy `secrets.json.example` to `secrets.json` and fill in:
   ```json
   {
     "coinbase_api_key": "<UUID from id field>",
     "coinbase_api_secret": "<raw base64 from privateKey field>"
   }
   ```
6. Install dependencies: `pip install pyjwt cryptography`
7. Switch `execMode` to `real` in the dashboard or in `data/policy.json`

> **Never commit `secrets.json`.** It is listed in `.gitignore` by default.

## Files

| File | Purpose |
|------|---------|
| `skills/clawapp-autotrader/trader.py` | Core trading logic — price fetch, signal gen, Coinbase API, account fetch |
| `skills/clawapp-autotrader/SKILL.md`  | This file — skill instructions for ClawApp |
| `data/policy.json` | Active policy: assets, caps, exec/approval mode |
| `data/signals_paper.json` | Signal history — paper mode |
| `data/signals_real.json`  | Signal history — real mode |
| `data/trades_paper.json`  | Trade fill history — paper mode |
| `data/trades_real.json`   | Trade fill history — real mode |
| `data/daily_spend_paper.json` | Rolling daily spend tracker — paper mode |
| `data/daily_spend_real.json`  | Rolling daily spend tracker — real mode |
| `secrets.json` | Coinbase API credentials (never commit this file) |
| `server.py` | Local HTTP server — serves dashboard and bridges file I/O |

## Dashboard

Start the local server to use the visual dashboard:

```bash
python3 server.py
# → open http://localhost:8080
```

The dashboard polls `/api/state` every 3 seconds and reflects all changes made
by this skill in real time. In real mode, it also polls `/api/accounts` every 15s
to sync live Coinbase account balances to the Positions panel and Stats bar.
