# Kamelle 🍬✨

**Catch the best free models before they're gone.**

Kamelle is a lightweight CLI for discovering, ranking, and syncing the best free OpenRouter models into OpenClaw.

Think of Kamelle as your big sister at carnival 🎉 — eyes on the sky, spotting the best free candy, helping you grab the good stuff before it hits the ground. Only here, the candy is free LLM tokens.

> Free models come and go. Kamelle keeps your bag stocked with the good stuff. 👜

![Kamelle screenshot: top 10 free models with context, latency and score](assets/kamelle-list-top-10.png)

## What Kamelle does

- 🍬 discovers free OpenRouter models live from OpenRouter
- ✨ ranks them with simple, sensible scoring
- ⚡ shows cached local latency probes in `kamelle list`
- 👜 refreshes a local cache every hour if you enable the refresh helper
- 💖 can set a best primary model and a fallback chain for OpenClaw
- 🔁 stores a backup so you can roll back if needed
- 🛠️ includes a doctor command and dry-run mode for safer changes

## Commands

- `kamelle list`
- `kamelle refresh`
- `kamelle status`
- `kamelle doctor --online`
- `kamelle auto`
- `kamelle switch <model>`
- `kamelle fallbacks`
- `kamelle rollback`

## Quick start

```bash
git clone https://github.com/l0hde/kamelle.git
cd kamelle
./scripts/install-local.sh
kamelle doctor --online
kamelle list -n 10
```

If `~/.local/bin` is not on your `PATH` yet, run:

```bash
~/.local/bin/kamelle status
```

## Example commands

```bash
kamelle list -n 10 --probe-latency
kamelle auto --keep-primary --dry-run
kamelle auto --keep-primary
kamelle switch qwen3-coder --dry-run
kamelle rollback
```

## Hourly refresh on macOS

```bash
cd kamelle
./scripts/install-hourly-refresh.sh
```

This installs a LaunchAgent that runs:

```bash
kamelle refresh
```

every hour. It refreshes the cache only — it does **not** silently change your primary model. Kamelle stays helpful, not sneaky. 💫

## Safety

Mutating commands support `--dry-run`, so Kamelle can show the plan before it moves any candy.

## Why it exists

OpenRouter's free lineup changes fast. A model that looked great yesterday may be rate-limited, slower, or gone today. Kamelle keeps that moving target manageable without turning your setup into a black box.
