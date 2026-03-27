# `sahara-intention-level-skill` Skill

This guidance helps you use `sahara-intention-level-skill` to map crypto-related user intent to the correct DeFi AI Services Gateway endpoint and return clear, structured outputs. It is designed for questions about assets, chains, protocols, and liquidity pools.

## Quick Start

1. **Get an API Key**

   Follow this flow to create a long-lived user API key.

   **Environment**

   - Authentication: `https://dev-authentication.saharaa.info`
   - DeFi Proxy: `https://defi-tools-proxy.saharaa.info`

   **Step 1: Login and get a JWT token**

   **Option A: Wallet signature login**

   ```bash
   # 1) Generate a signing message
   curl -X POST 'https://dev-authentication.saharaa.info/v1/auth/generate-message' \
     -H 'Content-Type: application/json' \
     -d '{"walletAddress": "0xYOUR_WALLET_ADDRESS"}'

   # 2) Sign the message with your wallet, then login
   curl -X POST 'https://dev-authentication.saharaa.info/v1/auth/login' \
     -H 'Content-Type: application/json' \
     -d '{
       "walletAddress": "0xYOUR_WALLET_ADDRESS",
       "signature": "0xYOUR_SIGNATURE",
       "message": "Sign this message to authenticate: ..."
     }'
   ```

   **Option B: Email login**

   ```bash
   curl -X POST 'https://dev-authentication.saharaa.info/v1/auth/email-login' \
     -H 'Content-Type: application/json' \
     -d '{
       "email": "your@email.com",
       "password": "your_password",
       "captcha": "captcha_token",
       "role": 1
     }'
   ```

   Save the `token` field from the login response.

   **Step 2: Create a long-lived User API Key**

   ```bash
   # Replace YOUR_JWT_TOKEN with the token from Step 1
   curl -X POST 'https://dev-authentication.saharaa.info/v1/user/api-keys' \
     -H 'Content-Type: application/json' \
     -H 'Authorization: Bearer YOUR_JWT_TOKEN' \
     -d '{
       "name": "my-defi-api-key",
       "scopes": "all"
     }'
   ```

   The response includes `data.apiKey` (for example: `sak_live_...`). This is your long-lived key.

   > **Important:** Save `apiKey` immediately. It is shown only once.

## Installation Guide

To install this skill package, place the entire `sahara-intention-level-skill` directory under `~/.openclaw/skills`. After copying the package, it is recommended to restart the OpenClaw Gateway service. Once restarted, you should be able to see and manage this skill within the OpenClaw skill dashboard.

## OpenClaw Environment Variable Setup Guide

To use the `sahara-intention-level-skill` skill in OpenClaw, you must correctly add the `DEFI_TOOLS_API_KEY` environment variable.

**Step-by-step (with screenshot):**

1. Open the **Config** page from the left sidebar in OpenClaw.
2. Switch to the `Environment` tab.
3. In `Custom entries` or the global environment variable section, click `Add entry` (or edit an existing variable).
4. In **Name**, enter: `DEFI_TOOLS_API_KEY`
5. In **Value**, paste your API key.

As shown below:

![OpenClaw Config - Environment Variable](../media/openclaw_env_var_example.png)

(As shown in the screenshot, enter the variable name and key in the highlighted input fields.)

6. Save the settings, then **restart the OpenClaw service** to ensure the environment variable takes effect.

> **Notes**  
> - The variable name must be exactly `DEFI_TOOLS_API_KEY` (case-sensitive).
> - Store the API key only in local or secure secret-management systems. Do not share it publicly.

3. **Usage and examples**

   Ask natural-language questions about DeFi, and the skill will route the request to the correct API endpoint based on user intent.

   Example prompts:
   - "Analyze this token's current risk and opportunity profile."
   - "Compare the yield and risk of these two pools."
   - "What are the key metrics for this protocol on Arbitrum?"

