# `sahara-intention-level-skill` Skill

This guidance helps you use `sahara-intention-level-skill` to map crypto-related user intent to the correct DeFi AI Services Gateway endpoint and return clear, structured outputs. It is designed for questions about assets, chains, protocols, and liquidity pools.

## Quick Start

Sign in and create an API key at **[tools.saharaai.com/sorin-skills](http://tools.saharaai.com/sorin-skills)**.

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
