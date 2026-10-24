"""MP Multiplayer Discord bot (Railway, 24/7).

  /gen CODE  - only in #verification: links the code shown in the game to your Discord account
  /stats     - your game account (only you see the answer)

Railway variables (Settings -> Variables), never put them in the code:
  DISCORD_TOKEN     the bot token (Discord Developer Portal -> Bot -> Reset Token)
  AUTH_PRIVATE_KEY  the login system key (64 hex characters, matches the public key in the mod)
  DATA_DIR          optional, default /data (add a Railway Volume mounted at /data so accounts survive redeploys)
  VERIFY_CHANNEL    optional, default "verification"
"""
import asyncio
import datetime
import logging
import os
import threading

import discord
from discord import app_commands
import paho.mqtt.client as mqtt

from authcore import AuthCore, TOPIC_REQ, TOPIC_RES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mpbot")

VERIFY_CHANNEL = os.environ.get("VERIFY_CHANNEL", "verification").lstrip("#").lower()
DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
BROKERS = [("broker.hivemq.com", 1883), ("test.mosquitto.org", 1883), ("broker.emqx.io", 1883)]

core = AuthCore(os.environ.get("AUTH_PRIVATE_KEY", ""), os.path.join(DATA_DIR, "accounts.db"))
if not core.ready:
    log.warning("AUTH_PRIVATE_KEY is not set: /gen and game logins are off until you add it in Railway")
else:
    log.info("login system ready, public key %s...", core.public_hex()[:16])


# ------------------------------------------------------------------ MQTT (phones <-> bot)
class Bridge:
    def __init__(self):
        self.clients = []

    def start(self):
        for i, (host, port) in enumerate(BROKERS):
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"mpbot-{os.getpid()}-{i}", clean_session=True)
            c.user_data_set(i)
            c.on_connect = self.on_connect
            c.on_message = self.on_message
            c.reconnect_delay_set(1, 30)
            try:
                c.connect_async(host, port, keepalive=30)
                c.loop_start()
            except Exception as e:
                log.warning("broker %s: %s", host, e)
            self.clients.append(c)

    def on_connect(self, c, idx, flags, rc, props=None):
        log.info("broker %s connected (%s)", BROKERS[idx][0], rc)
        c.subscribe(TOPIC_REQ, qos=0)

    def on_message(self, c, idx, msg):
        try:
            r = core.handle_frame(msg.payload, idx)
        except Exception:
            log.exception("request failed")
            return
        if r:
            sid, frame = r
            c.publish(TOPIC_RES + sid, frame, qos=0)

    def push(self, sid, frame):
        # on every broker: the phone may have switched broker while you typed /gen
        for c in self.clients:
            c.publish(TOPIC_RES + sid, frame, qos=0)


bridge = Bridge()


# ------------------------------------------------------------------ Discord
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def in_verify_channel(inter: discord.Interaction) -> bool:
    ch = inter.channel
    name = getattr(ch, "name", "") or ""
    return name.lower().lstrip("#") == VERIFY_CHANNEL or name.lower().endswith(VERIFY_CHANNEL)


@tree.command(name="gen", description="Verify your game login with the code shown in MP Multiplayer")
@app_commands.describe(code="The code from the game screen (8 characters)")
async def gen_cmd(inter: discord.Interaction, code: str):
    if not in_verify_channel(inter):
        await inter.response.send_message(f"Use /gen in the #{VERIFY_CHANNEL} channel.", ephemeral=True)
        return
    text, pushes = await asyncio.to_thread(core.gen, inter.user.id, str(inter.user), code)
    for sid, frame in pushes:
        bridge.push(sid, frame)
    await inter.response.send_message(text, ephemeral=True)   # only you see it: the code stays private


def _date(t):
    return datetime.datetime.utcfromtimestamp(int(t)).strftime("%Y-%m-%d %H:%M UTC") if t else "-"


@tree.command(name="stats", description="Your MP Multiplayer account (only you see it)")
async def stats_cmd(inter: discord.Interaction):
    a = await asyncio.to_thread(core.account_by_discord, inter.user.id)
    if not a:
        await inter.response.send_message(f"No game account yet. Join a server, then use /gen in #{VERIFY_CHANNEL}.", ephemeral=True)
        return
    e = discord.Embed(title=f"MP account: {a['user']}", color=0xD7263D)
    e.add_field(name="Discord", value=a["discord_name"])
    e.add_field(name="Gender", value=a["gender"])
    e.add_field(name="Age", value=str(a["age"]))
    e.add_field(name="Created", value=_date(a["created"]))
    e.add_field(name="Logins", value=str(a["logins"]))
    e.add_field(name="Last login", value=_date(a["last_login"]) + (f"\n{a['last_server']}" if a["last_server"] else ""))
    await inter.response.send_message(embed=e, ephemeral=True)


async def _sync_guild(g):
    try:
        tree.copy_global_to(guild=g)
        cmds = await tree.sync(guild=g)
        log.info("Slash commands in %s: %s", g, ", ".join("/" + c.name for c in cmds))
    except discord.Forbidden:
        log.warning("No slash commands in %s: re-invite the bot WITH the applications.commands scope (link above)", g)
    except Exception as e:
        log.warning("sync %s: %s", g, e)


@client.event
async def on_ready():
    log.info("Logged in as %s, in %d server(s)", client.user, len(client.guilds))
    log.info("Invite link (bot + slash commands): https://discord.com/oauth2/authorize?client_id=%s&scope=bot+applications.commands&permissions=277025392640", client.user.id)
    for g in client.guilds:   # guild sync: the commands show up at once
        await _sync_guild(g)
    try:
        await tree.sync()     # global too (other servers, up to 1 h)
    except Exception as e:
        log.warning("global sync: %s", e)


@client.event
async def on_guild_join(g):
    log.info("Joined %s", g)
    await _sync_guild(g)


if __name__ == "__main__":
    bridge.start()
    client.run(os.environ["DISCORD_TOKEN"], log_handler=None)
