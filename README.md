---
title: MP login bot
description: Discord verification + account bot for the GTA SA MP mod (wagu)
tags:
  - python
  - discord.py
---

# MP login bot

The account system of the MP mod (3.21.6+): the default server, Brief Case War and every custom
server with "Authentication system" on.

1. A player connects: the game shows a random code (8 characters, new every time).
2. In the Discord server ([discord.gg/tbsDknk34y](https://discord.gg/tbsDknk34y)) the player types
   `/gen CODE` in **#verification** (the command works only there; the answer is only visible to them).
3. The game shows Sign up: user, password, gender, age -> **Create**. Next time: Login only.
4. `/stats` (Discord, only visible to you) or `/stats` / `/char` in the game: your account.

Accounts are stored only here (SQLite, password hashed with PBKDF2-SHA256 + salt). The game and the
bot talk end-to-end encrypted (ECDH P-256 + AES-256-GCM) over public MQTT brokers; the bot signs a
one-time login ticket (ECDSA P-256) that the MP server checks. The host never sees a password.

## Railway setup

Variables (Service > Variables) - never put them in the code:

| Variable | Value |
|---|---|
| `DISCORD_TOKEN` | the bot token (Discord Developer Portal > Bot > Reset Token) |
| `AUTH_PRIVATE_KEY` | the 64-hex private key that matches the public key built into the mod |
| `VERIFY_CHANNEL` | optional, default `verification` |
| `DATA_DIR` | optional, default `/data` (add a Railway **Volume** mounted at `/data` so accounts survive redeploys) |

Discord: invite the bot with the `bot` and `applications.commands` scopes, create a text channel
named `verification`, and let the bot read/send there. Slash commands are synced to every server the
bot is in when it starts.

Start command: `python main.py` (Procfile). Python 3.11.
