# Kamelle 🍬✨

**Catch the best free models before they're gone.**

Kamelle is a lightweight CLI for discovering, ranking, and syncing the best free OpenRouter models into OpenClaw.

Think of Kamelle as your big sister at carnival 🎉 — eyes on the sky, spotting the best free candy, helping you grab the good stuff before it hits the ground. Only here, the candy is free LLM tokens.

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

## Local install

```bash
cd kamelle
./scripts/install-local.sh
```

This creates a local virtualenv and installs a launcher at:

```bash
~/.local/bin/kamelle
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

every hour. It refreshes the cache only — it does **not** silently change your primary model. 💫

## Safety

Mutating commands support `--dry-run`, so Kamelle can show the plan before it moves any candy.
