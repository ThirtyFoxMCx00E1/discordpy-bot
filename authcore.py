"""MP Multiplayer login system - the bot side (no Discord code here, so it can be tested alone).

Accounts live only here (SQLite in DATA_DIR). Phones talk to us over public MQTT brokers, but every
frame is end-to-end encrypted:  ECDH P-256 (phone's one-time key x our AUTH_PRIVATE_KEY)
-> SHA-256("wagu-auth-v1" || shared || phone key) -> AES-256-GCM.  Passwords are stored as
PBKDF2-HMAC-SHA256 (200 000 rounds, random salt) and never leave this process.

A good login / sign up returns a ticket "WAGU1|user|discord|challenge|server|time" signed with our
key (ECDSA P-256, raw r||s). The game server checks it with the public key built into the mod.
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import serialization

TOPIC_REQ = "wagu/auth1/req"
TOPIC_RES = "wagu/auth1/res/"
CODE_ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 8
CODE_TTL = 10 * 60          # a /gen code works for 10 minutes
VERIFY_TTL = 20 * 60        # after /gen: 20 minutes to fill in the sign up form
SESSION_TTL = 12 * 3600     # logged in: new tickets (reconnect / host change) without the password
PBKDF2_ROUNDS = 200_000
USER_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
GENDERS = ("Male", "Female", "Other")


def _now():
    return int(time.time())


def _kv(text):
    out = {}
    for line in text.split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v
    return out


def _line(v, n):
    return re.sub(r"[\r\n]", "", str(v))[:n]


def _clean(v, n):
    return re.sub(r"[\r\n|=]", "", str(v))[:n]


class AuthCore:
    def __init__(self, private_hex, db_path):
        self.key = None
        if private_hex:
            d = int(private_hex.strip(), 16)
            self.key = ec.derive_private_key(d, ec.SECP256R1())
        self.lock = threading.RLock()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS accounts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT NOT NULL, user_lc TEXT NOT NULL UNIQUE,
            discord_id TEXT NOT NULL UNIQUE, discord_name TEXT NOT NULL,
            pass_hash TEXT NOT NULL, salt TEXT NOT NULL, rounds INTEGER NOT NULL,
            gender TEXT NOT NULL, age INTEGER NOT NULL,
            created INTEGER NOT NULL, last_login INTEGER NOT NULL DEFAULT 0, logins INTEGER NOT NULL DEFAULT 0,
            last_server TEXT NOT NULL DEFAULT '')""")
        self.db.commit()
        self.sessions = {}      # sid -> {key, broker, seen, code, vtoken, vdiscord, vname, vexp}
        self.codes = {}         # code -> (sid, expires)
        self.logins = {}        # sess token -> (user_lc, expires)
        self.fails = {}         # key -> [timestamps]
        self.gen_rate = {}      # discord id -> [timestamps]

    # ---------------------------------------------------------------- crypto
    @property
    def ready(self):
        return self.key is not None

    def public_hex(self):
        return self.key.public_key().public_bytes(serialization.Encoding.X962,
                                                  serialization.PublicFormat.UncompressedPoint).hex()

    def _session_for(self, pub, broker):
        sid = hashlib.sha256(pub).hexdigest()[:16]
        s = self.sessions.get(sid)
        if s is None:
            peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub)
            z = self.key.exchange(ec.ECDH(), peer)
            k = hashlib.sha256(b"wagu-auth-v1" + z + pub).digest()
            s = {"key": k, "sid": sid}
            self.sessions[sid] = s
        s["broker"] = broker
        s["seen"] = _now()
        return s

    def seal(self, s, fields):
        plain = "\n".join(f"{k}={_line(v, 300)}" for k, v in fields.items()).encode()
        iv = secrets.token_bytes(12)
        return bytes([1]) + iv + AESGCM(s["key"]).encrypt(iv, plain, b"WS")

    def sign_ticket(self, user, discord_id, chal, server):
        payload = f"WAGU1|{user}|{discord_id}|{chal}|{_clean(server, 39)}|{_now()}"
        der = self.key.sign(payload.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return payload, (r.to_bytes(32, "big") + s.to_bytes(32, "big")).hex()

    # ---------------------------------------------------------------- housekeeping
    def _expire(self):
        t = _now()
        for c in [c for c, (_, e) in self.codes.items() if e < t]:
            del self.codes[c]
        for k in [k for k, (_, e) in self.logins.items() if e < t]:
            del self.logins[k]
        for sid in [sid for sid, s in self.sessions.items() if s["seen"] < t - 3600 and s.get("vexp", 0) < t]:
            del self.sessions[sid]

    def _rate(self, table, key, limit, window):
        t = time.time()
        lst = [x for x in table.get(key, []) if x > t - window]
        table[key] = lst
        if len(lst) >= limit:
            return False
        lst.append(t)
        return True

    def _account(self, where, arg):
        cur = self.db.execute(f"SELECT user,discord_id,discord_name,pass_hash,salt,rounds,gender,age,created,last_login,logins,last_server FROM accounts WHERE {where}=?", (arg,))
        row = cur.fetchone()
        if not row:
            return None
        keys = ("user", "discord_id", "discord_name", "pass_hash", "salt", "rounds", "gender", "age", "created", "last_login", "logins", "last_server")
        return dict(zip(keys, row))

    def account_by_discord(self, discord_id):
        with self.lock:
            return self._account("discord_id", str(discord_id))

    def _hash(self, password, salt, rounds):
        return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), rounds).hex()

    def _logged_in(self, a, chal, server, sid):
        self.db.execute("UPDATE accounts SET last_login=?, logins=logins+1, last_server=? WHERE user_lc=?",
                        (_now(), _clean(server, 39), a["user"].lower()))
        self.db.commit()
        sess = secrets.token_hex(16)
        self.logins[sess] = (a["user"].lower(), _now() + SESSION_TTL)
        out = {"ok": 1, "user": a["user"], "sess": sess}
        if re.fullmatch(r"[0-9a-f]{32}", chal or ""):
            out["ticket"], out["sig"] = self.sign_ticket(a["user"], a["discord_id"], chal, server)
        return out

    # ---------------------------------------------------------------- phone requests
    def handle_frame(self, frame, broker=0):
        """One encrypted request from a phone -> (sid, encrypted answer) or None."""
        if not self.ready or len(frame) < 1 + 65 + 12 + 16 or frame[0] != 1:
            return None
        pub, iv, ct = frame[1:66], frame[66:78], frame[78:]
        with self.lock:
            self._expire()
            try:
                s = self._session_for(pub, broker)
                req = _kv(AESGCM(s["key"]).decrypt(iv, ct, b"WC").decode("utf-8", "replace"))
            except Exception:
                return None
            ans = self._handle(s, req)
            ans["rid"] = req.get("rid", "")
            ans["op"] = req.get("op", "")
            return s["sid"], self.seal(s, ans)

    def _handle(self, s, q):
        op = q.get("op")
        sid = s["sid"]
        if op == "hello":
            return {"ok": 1}
        if op == "gen":
            code = q.get("code", "").upper()
            if len(code) != CODE_LEN or any(c not in CODE_ALPHA for c in code):
                return {"ok": 0, "err": "bad code"}
            other = self.codes.get(code)
            if other and other[0] != sid:
                return {"ok": 0, "err": "dup"}           # the phone picks a new random code
            if not self._rate(self.fails, "gen:" + sid, 20, 600):
                return {"ok": 0, "err": "Too many codes. Wait a few minutes."}
            for c in [c for c, (x, _) in self.codes.items() if x == sid]:
                del self.codes[c]                        # one live code per screen
            self.codes[code] = (sid, _now() + CODE_TTL)
            s["code"] = code
            return {"ok": 1, "ttl": CODE_TTL}
        if op == "signup":
            if not s.get("vtoken") or not hmac.compare_digest(s["vtoken"], q.get("vtoken", "")) or s.get("vexp", 0) < _now():
                return {"ok": 0, "err": "Verification expired. Get a new code."}
            user, pw = q.get("user", ""), q.get("pass", "")
            gender, age = q.get("gender", ""), q.get("age", "")
            if not USER_RE.match(user):
                return {"ok": 0, "err": "Username: 3-20 letters, numbers or _"}
            if not (6 <= len(pw) <= 32):
                return {"ok": 0, "err": "Password: 6-32 characters"}
            if gender not in GENDERS:
                return {"ok": 0, "err": "Pick a gender"}
            if not age.isdigit() or not (10 <= int(age) <= 99):
                return {"ok": 0, "err": "Age: 10-99"}
            if self._account("discord_id", s["vdiscord"]):
                return {"ok": 0, "err": "This Discord account already has a game account. Log in."}
            if self._account("user_lc", user.lower()):
                return {"ok": 0, "err": "That username is taken"}
            salt = secrets.token_hex(16)
            self.db.execute("INSERT INTO accounts(user,user_lc,discord_id,discord_name,pass_hash,salt,rounds,gender,age,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (user, user.lower(), s["vdiscord"], s["vname"], self._hash(pw, salt, PBKDF2_ROUNDS), salt, PBKDF2_ROUNDS, gender, int(age), _now()))
            self.db.commit()
            s["vtoken"] = None
            a = self._account("user_lc", user.lower())
            return self._logged_in(a, q.get("chal", ""), q.get("srv", ""), sid)
        if op == "login":
            user, pw = q.get("user", ""), q.get("pass", "")
            if not self._rate(self.fails, "login:" + sid, 10, 600) or not self._rate(self.fails, "user:" + user.lower(), 8, 600):
                return {"ok": 0, "err": "Too many tries. Wait 10 minutes."}
            a = self._account("user_lc", user.lower())
            if not a or not hmac.compare_digest(a["pass_hash"], self._hash(pw, a["salt"], a["rounds"])):
                return {"ok": 0, "err": "Wrong username or password"}
            return self._logged_in(a, q.get("chal", ""), q.get("srv", ""), sid)
        if op in ("ticket", "stats"):
            v = self.logins.get(q.get("sess", ""))
            if not v or v[1] < _now():
                return {"ok": 0, "err": "Session expired. Log in again.", "relogin": 1}
            a = self._account("user_lc", v[0])
            if not a:
                return {"ok": 0, "err": "Account not found", "relogin": 1}
            if op == "ticket":
                if not re.fullmatch(r"[0-9a-f]{32}", q.get("chal", "")):
                    return {"ok": 0, "err": "bad challenge"}
                t, sig = self.sign_ticket(a["user"], a["discord_id"], q["chal"], q.get("srv", ""))
                return {"ok": 1, "user": a["user"], "ticket": t, "sig": sig}
            return {"ok": 1, "user": a["user"], "discord": a["discord_name"], "gender": a["gender"], "age": a["age"],
                    "created": a["created"], "logins": a["logins"], "last": a["last_login"], "server": a["last_server"]}
        return {"ok": 0, "err": "unknown request"}

    # ---------------------------------------------------------------- Discord /gen
    def gen(self, discord_id, discord_name, code):
        """/gen CODE in #verification. Returns (reply text, [(sid, encrypted push)])."""
        code = (code or "").strip().upper().replace(" ", "").replace("-", "")
        with self.lock:
            self._expire()
            if not self.ready:
                return "Verification is not set up yet (the bot has no AUTH_PRIVATE_KEY).", []
            if not self._rate(self.gen_rate, str(discord_id), 6, 600):
                return "Too many /gen. Wait a few minutes.", []
            hit = self.codes.pop(code, None)
            if not hit:
                return "That code is wrong or expired. Look at the code in the game (it changes every time) and try again.", []
            sid = hit[0]
            s = self.sessions.get(sid)
            if not s:
                return "The game screen for that code is gone. Open it again in the game for a new code.", []
            s["vtoken"] = secrets.token_hex(16)
            s["vdiscord"], s["vname"] = str(discord_id), _clean(discord_name, 40)
            s["vexp"] = _now() + VERIFY_TTL
            a = self._account("discord_id", str(discord_id))
            push = {"op": "verified", "vtoken": s["vtoken"], "dname": s["vname"], "has": 1 if a else 0, "user": a["user"] if a else ""}
            if a:
                text = f"Verified. You already have the game account **{a['user']}**: log in with it in the game."
            else:
                text = "Verified. Go back to the game and create your account."
            return text, [(sid, self.seal(s, push))]

    def broker_of(self, sid):
        s = self.sessions.get(sid)
        return s.get("broker", 0) if s else 0
