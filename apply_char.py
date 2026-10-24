"""Adds the /char slash command (same card as /stats) to main.py in place.
Safe to run twice. Run from the discordpy-bot repo root."""
import py_compile
import re
import sys

PATH = "main.py"
src = open(PATH, encoding="utf-8").read()

if 'name="char"' in src:
    print("main.py already has /char - nothing to do")
    sys.exit(0)

deco = re.search(r'^@tree\.command\(name="stats"[^\n]*\)\n', src, re.M)
func = re.search(r'^async def stats_cmd\(inter: discord\.Interaction\):\n', src, re.M)
anchor = re.search(r'^async def _sync_guild\(', src, re.M)
if not (deco and func and anchor) or not (deco.end() == func.start() and func.end() < anchor.start()):
    sys.exit("Could not find the /stats command where expected - main.py was changed; patch by hand.")

new = (
    src[:deco.start()]
    + "async def _account_card(inter: discord.Interaction):\n"
    + src[func.end():anchor.start()]
    + '@tree.command(name="stats", description="Your MP Multiplayer account (only you see it)")\n'
    + "async def stats_cmd(inter: discord.Interaction):\n"
    + "    await _account_card(inter)\n\n\n"
    + '@tree.command(name="char", description="Your MP Multiplayer character card (only you see it)")\n'
    + "async def char_cmd(inter: discord.Interaction):\n"
    + "    await _account_card(inter)\n\n\n"
    + src[anchor.start():]
)

open(PATH + ".bak", "w", encoding="utf-8").write(src)
open(PATH, "w", encoding="utf-8").write(new)
py_compile.compile(PATH, doraise=True)
print("Added /char (backup saved as main.py.bak)")
