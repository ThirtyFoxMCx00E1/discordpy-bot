#!/usr/bin/env bash
# Run from the discordpy-bot repo root after extracting this package into it.
set -e
[ -f main.py ] || { echo "Run this inside the discordpy-bot repo (main.py not found)"; exit 1; }

python apply_char.py                       # adds /char to main.py
grep -qx '.env' .gitignore 2>/dev/null || echo '.env' >> .gitignore   # never commit secrets

git add main.py .gitignore Dockerfile docker-compose.yml .env.example README-deploy.md
git commit -m "Self-host deploy kit (Docker) + /char command"
git push
echo "Done. Now on your server: cp .env.example .env, fill it in, docker compose up -d --build"
