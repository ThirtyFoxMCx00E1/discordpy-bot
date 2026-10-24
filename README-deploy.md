# Run the MP login bot 24/7 (no Railway)

The bot only makes OUTBOUND connections (Discord + MQTT), so it needs no
open ports and no domain. Any machine that stays on will do.

## Plain Docker (any VPS)
    git clone https://github.com/ThirtyFoxMCx00E1/discordpy-bot
    cd discordpy-bot            # copy Dockerfile, docker-compose.yml, .env.example here
    cp .env.example .env && nano .env
    docker compose up -d --build
    docker compose logs -f      # look for "Logged in as ..." and "Slash commands in ..."

## Dokploy / Coolify / CapRover
Point the app at the repo, set DISCORD_TOKEN and AUTH_PRIVATE_KEY in the
panel's environment settings, and add a persistent volume mounted at /data.

## Check
- Log shows "Logged in as <bot>" -> bot is online in Discord.
- Log shows "Slash commands in <server>: /gen, /stats" -> commands synced.
- "No slash commands ... re-invite" -> invite again with the printed link
  (needs the applications.commands scope).

## Quick update (Termux or any shell)
Extract this package into the discordpy-bot repo root, then:

    bash update.sh

It patches main.py (adds /char), makes sure .env is git-ignored, commits and pushes.
Do NOT commit your Discord token or AUTH_PRIVATE_KEY. If the old key was ever pasted
or committed anywhere, generate a new one and ship its public key in the next mod build.
