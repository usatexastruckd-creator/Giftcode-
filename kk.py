
#!/usr/bin/env python3
# main.py — Dual bot: Main + Showcase (Render-ready)
import os, sys, json, time, random, sqlite3, threading, uuid, csv, io, logging, html, base64
from datetime import datetime, timedelta
from collections import defaultdict, deque
from queue import PriorityQueue
import requests, telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask

# ═══════════════════════ CONFIG ═══════════════════════
MAIN_BOT_TOKEN     = "8710044999:AAGsGCewdnb4sqrwE8dkRfQErKvLklpwP8M"
SHOWCASE_BOT_TOKEN = "8805691874:AAF9qAn8-AkGYaeq7xxKUZRlFsDHa5sQzz0"
OWNER_ID = 6366853738
CHANNEL_TG = "thaish12"
CHANNEL_YT = "https://youtube.com/@tahish159?si=5ehTRVzB7WOnOj5s"
BOT_USERNAME = "Rame124673_bot"

# 🛒 رابط القناة (افتح القناة باسم المستخدم من تلجرام)
SHOWCASE_URL = "https://t.me/اسم_قناتك"

INITIAL_POINTS = 50
REFERRAL_POINTS = 20
DAILY_BASE = 5
DAILY_STREAK_BONUS = 2
DAILY_STREAK_MAX = 10

DB_FILE = "bot.db"
OLD_DATA_DIR = "user_data"
PROXIES_FILE = "proxies.txt"
USER_PROXIES_FILE = "user_proxies.txt"
AUDIT_LOG = "audit.log"
WORKERS = 5
RATE_LIMIT_PER_MIN = 30

GC_BASE_URL   = "https://giftcode.betelgeuse.app"
GC_LEADERS    = f"{GC_BASE_URL}/api/leaders"
GC_STORE      = f"{GC_BASE_URL}/api/store"
GC_REFER      = f"{GC_BASE_URL}/api/referrer"
GC_CONFIGS    = f"{GC_BASE_URL}/api/configs"
GC_REF_CODE   = "4094894"
GC_TOKEN      = os.getenv("GC_TOKEN", "")
GC_FEED_INTERVAL   = 60
GC_AUTO_INTERVAL   = 1800
SHOWCASE_POLL_INTERVAL = 60
STORE_CACHE_TTL = 3600

# GitHub sync (اختياري — اتركه فارغاً لتعطيله)
GITHUB_REPO   = os.getenv("GH_REPO", "")
GITHUB_TOKEN  = os.getenv("GH_TOKEN", "")
GH_API        = "https://api.github.com"
DATA_SYNC_INTERVAL = 900   # 15 دقيقة

PORT = int(os.getenv("PORT", "8080"))

# ═══════════════════════ LOGGING ═══════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')
log = logging.getLogger("bot")

# ═══════════════════════ DB ═══════════════════════
_db_lock = threading.Lock()
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")


def _init_db():
    with _db_lock:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            first_name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            lang TEXT DEFAULT 'ar',
            points INTEGER DEFAULT 50,
            streak INTEGER DEFAULT 0,
            last_daily TEXT DEFAULT '',
            youtube_verified INTEGER DEFAULT 0,
            youtube_date TEXT DEFAULT '',
            referral_code TEXT DEFAULT '4094894',
            start_number INTEGER DEFAULT 4084879,
            mode TEXT DEFAULT 'giftcode',
            banned INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, mode TEXT, target TEXT,
            result TEXT, proxy TEXT,
            ts TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_hist_u ON history(user_id);
        CREATE INDEX IF NOT EXISTS ix_hist_ts ON history(ts);
        CREATE TABLE IF NOT EXISTS proxies(
            proxy TEXT PRIMARY KEY,
            owner INTEGER DEFAULT 0,
            success INTEGER DEFAULT 0,
            fail INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS referrals(
            new_user INTEGER PRIMARY KEY,
            referrer INTEGER,
            ts TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS seen_ids(
            uid TEXT PRIMARY KEY,
            ref_code TEXT,
            result TEXT,
            ts TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS purchase_seen(
            purchase_key TEXT PRIMARY KEY,
            ts TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS showcase_seen(
            purchase_key TEXT PRIMARY KEY,
            ts TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS showcase_cfg(
            key TEXT PRIMARY KEY, value TEXT
        );
        """)
        conn.commit()


def db_exec(query, params=(), fetch=None):
    with _db_lock:
        cur = conn.execute(query, params)
        if fetch == "one":
            r = cur.fetchone(); conn.commit(); return r
        if fetch == "all":
            r = cur.fetchall(); conn.commit(); return r
        conn.commit()
        return cur


def ensure_user(user_id, first_name="", username=""):
    r = db_exec("SELECT user_id FROM users WHERE user_id=?", (user_id,), "one")
    if not r:
        db_exec("INSERT INTO users(user_id,first_name,username,points) VALUES(?,?,?,?)",
                (user_id, first_name, username, INITIAL_POINTS))
    else:
        db_exec("UPDATE users SET first_name=?, username=? WHERE user_id=?",
                (first_name, username, user_id))


def get_user(user_id):
    return db_exec("SELECT * FROM users WHERE user_id=?", (user_id,), "one")


def set_user(user_id, **kw):
    if not kw: return
    cols = ", ".join(f"{k}=?" for k in kw)
    db_exec(f"UPDATE users SET {cols} WHERE user_id=?", (*kw.values(), user_id))


def add_points(user_id, delta):
    db_exec("UPDATE users SET points=points+? WHERE user_id=?", (delta, user_id))


def get_points(user_id):
    r = db_exec("SELECT points FROM users WHERE user_id=?", (user_id,), "one")
    return r["points"] if r else INITIAL_POINTS


def log_history(user_id, mode, target, result, proxy=""):
    db_exec("INSERT INTO history(user_id,mode,target,result,proxy) VALUES(?,?,?,?,?)",
            (user_id, mode, str(target), result, proxy or ""))


def history_stats(user_id):
    r = db_exec("""SELECT
        SUM(CASE WHEN result='success' THEN 1 ELSE 0 END) AS s,
        SUM(CASE WHEN result='failed' THEN 1 ELSE 0 END) AS f,
        SUM(CASE WHEN result='already' THEN 1 ELSE 0 END) AS a
        FROM history WHERE user_id=?""", (user_id,), "one")
    return {"success": r["s"] or 0, "failed": r["f"] or 0, "already": r["a"] or 0}


def get_setting(key, default=None):
    r = db_exec("SELECT value FROM settings WHERE key=?", (key,), "one")
    return r["value"] if r else default


def set_setting(key, value):
    db_exec("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
            (key, str(value)))


def get_showcase_cfg(key, default=None):
    r = db_exec("SELECT value FROM showcase_cfg WHERE key=?", (key,), "one")
    return r["value"] if r else default


def set_showcase_cfg(key, value):
    db_exec("INSERT OR REPLACE INTO showcase_cfg(key,value) VALUES(?,?)",
            (key, str(value)))


def audit(actor, action, target=""):
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} | {actor} | {action} | {target}\n")
    except Exception:
        pass


# ═══════════ MIGRATION + REIMPORT ═══════════
def migrate_legacy():
    if not os.path.isdir(OLD_DATA_DIR):
        return
    done = get_setting("migrated")
    if done:
        return
    migrated = 0
    for fn in os.listdir(OLD_DATA_DIR):
        try:
            if fn.startswith("points_") and fn.endswith(".json"):
                uid = int(fn.replace("points_", "").replace(".json", ""))
                with open(os.path.join(OLD_DATA_DIR, fn), encoding="utf-8") as f:
                    pts = json.load(f).get("points", INITIAL_POINTS)
                ensure_user(uid)
                set_user(uid, points=pts)
                migrated += 1
            elif fn == "user_sessions.json":
                with open(os.path.join(OLD_DATA_DIR, fn), encoding="utf-8") as f:
                    data = json.load(f)
                for uid, s in data.items():
                    ensure_user(int(uid), s.get("first_name", ""),
                                s.get("username", ""))
        except Exception as e:
            log.warning(f"migrate {fn}: {e}")
    set_setting("migrated", migrated)
    log.info(f"Migrated {migrated} users from JSON")


def reimport_sessions(verbose=False):
    if not os.path.isdir(OLD_DATA_DIR):
        return 0, 0
    added_users = 0
    added_points = 0

    sp = os.path.join(OLD_DATA_DIR, "user_sessions.json")
    if os.path.exists(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                data = json.load(f)
            for uid_str, s in data.items():
                try:
                    uid = int(uid_str)
                except Exception:
                    continue
                existing = db_exec(
                    "SELECT user_id, first_name, username FROM users WHERE user_id=?",
                    (uid,), "one")
                if not existing:
                    ensure_user(uid, s.get("first_name", "") or "",
                                s.get("username", "") or "")
                    added_users += 1
                else:
                    upd = {}
                    if not existing["first_name"] and s.get("first_name"):
                        upd["first_name"] = s["first_name"]
                    if not existing["username"] and s.get("username"):
                        upd["username"] = s["username"]
                    if upd:
                        set_user(uid, **upd)
        except Exception as e:
            log.warning(f"reimport sessions: {e}")

    for fn in os.listdir(OLD_DATA_DIR):
        if not (fn.startswith("points_") and fn.endswith(".json")):
            continue
        try:
            uid = int(fn.replace("points_", "").replace(".json", ""))
        except Exception:
            continue
        try:
            with open(os.path.join(OLD_DATA_DIR, fn), encoding="utf-8") as f:
                pts = json.load(f).get("points", INITIAL_POINTS)
        except Exception:
            continue
        existing = db_exec("SELECT user_id FROM users WHERE user_id=?", (uid,), "one")
        if not existing:
            ensure_user(uid)
            set_user(uid, points=pts)
            added_points += 1

    if verbose:
        log.info(f"reimport: +{added_users} users, +{added_points} points")
    return added_users, added_points


# ═══════════════════════ I18N ═══════════════════════
T = {
    "ar": {
        "welcome": "✨ مرحباً {name}!",
        "points": "نقاطك", "code": "كودك", "mode": "وضع",
        "success": "نجاح", "choose": "اختر من القائمة:",
        "not_sub": "🔒 اشترك وأكد يوتيوب.", "sub_first": "اشترك أولاً.",
        "start_attack": "▶️ بدء", "stop_attack": "⏹️ إيقاف",
        "toggle_mode": "🔄 تبديل الوضع", "status": "📊 الحالة",
        "referral": "🔗 رابط الإحالة", "add_proxy": "➕ إضافة بروكسي",
        "owner": "👑 المالك", "set_referral": "🔑 كود الإحالة",
        "set_start": "🔢 رقم البداية", "daily": "🎁 هدية يومية",
        "leaderboard": "🏆 المتصدرون", "quick_menu": "⌨️ قائمة سريعة",
        "my_stats": "📊 إحصائياتي", "lang": "🌐 اللغة",
    },
    "en": {
        "welcome": "✨ Welcome {name}!",
        "points": "Points", "code": "Code", "mode": "Mode",
        "success": "Success", "choose": "Choose:",
        "not_sub": "🔒 Subscribe & verify YouTube.", "sub_first": "Subscribe first.",
        "start_attack": "▶️ Start", "stop_attack": "⏹️ Stop",
        "toggle_mode": "🔄 Toggle Mode", "status": "📊 Status",
        "referral": "🔗 Referral Link", "add_proxy": "➕ Add Proxy",
        "owner": "👑 Owner", "set_referral": "🔑 Referral Code",
        "set_start": "🔢 Start Number", "daily": "🎁 Daily Gift",
        "leaderboard": "🏆 Leaderboard", "quick_menu": "⌨️ Quick Menu",
        "my_stats": "📊 My Stats", "lang": "🌐 Language",
    },
}


def tr(uid, key, **kw):
    u = get_user(uid)
    lang = u["lang"] if u else "ar"
    s = T.get(lang, T["ar"]).get(key, key)
    return s.format(**kw) if kw else s


# ═══════════════════════ RATE LIMIT ═══════════════════════
_rl = defaultdict(deque)


def rate_limited(uid, limit=RATE_LIMIT_PER_MIN):
    now = time.time()
    dq = _rl[uid]
    while dq and now - dq[0] > 60:
        dq.popleft()
    if len(dq) >= limit:
        return True
    dq.append(now)
    return False


# ═══════════════════════ PROXIES ═══════════════════════
def load_file_proxies(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]


def add_proxy(proxy, owner=0):
    if not proxy.startswith("http"):
        proxy = f"http://{proxy}"
    try:
        db_exec("INSERT OR IGNORE INTO proxies(proxy,owner) VALUES(?,?)", (proxy, owner))
        return True
    except Exception:
        return False


def del_proxy(proxy):
    db_exec("DELETE FROM proxies WHERE proxy=?", (proxy,))


def all_proxies():
    file_p = load_file_proxies(PROXIES_FILE)
    user_p = load_file_proxies(USER_PROXIES_FILE)
    db_p = [r["proxy"] for r in db_exec(
        "SELECT proxy FROM proxies WHERE banned=0", fetch="all")]
    seen = set()
    out = []
    for p in file_p + user_p + db_p:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def test_proxy(proxy, timeout=6):
    try:
        r = requests.get("https://httpbin.org/ip",
                         proxies={"http": proxy, "https": proxy}, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def batch_test(proxies, workers=20):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(test_proxy, proxies))
    return [p for p, ok in zip(proxies, results) if ok]


def proxy_score(proxy, success):
    if not proxy:
        return
    col = "success" if success else "fail"
    db_exec(f"UPDATE proxies SET {col}={col}+1 WHERE proxy=?", (proxy,))


def best_proxies(n=15):
    rows = db_exec("""SELECT proxy, success, fail FROM proxies
        WHERE banned=0 AND (success+fail)>=3
        ORDER BY CAST(success AS REAL)/(success+fail+1) DESC LIMIT ?""",
                   (n,), "all")
    return [r["proxy"] for r in rows]


def pick_proxy(banned):
    pool = all_proxies()
    if not pool:
        return None
    ranked = best_proxies(30)
    candidates = [p for p in ranked if p not in banned]
    if len(candidates) < 5:
        candidates += [p for p in pool if p not in banned and p not in candidates]
    random.shuffle(candidates)
    return candidates[0] if candidates else None


# ═══════════════════════ GiftCode CLIENT ═══════════════════════
GC_DEFAULT_HEADERS = {
    "User-Agent": "okhttp/4.12.0",
    "Accept-Encoding": "gzip",
}


class GiftCodeClient:
    def __init__(self, ref_code=GC_REF_CODE, token=GC_TOKEN,
                 proxies_file=PROXIES_FILE):
        self.ref_code = str(ref_code)
        self.token = token or ""
        self.proxies_file = proxies_file
        self._p_idx = 0
        self._lock = threading.Lock()

    def _load_proxies(self):
        out = []
        for line in load_file_proxies(self.proxies_file):
            if not line.startswith("http"):
                line = "http://" + line
            out.append(line)
        return out

    def _next_proxy(self, exclude=None):
        exclude = exclude or set()
        pool = self._load_proxies()
        if not pool:
            return None
        with self._lock:
            n = len(pool)
            for _ in range(n):
                p = pool[self._p_idx % n]
                self._p_idx += 1
                if p not in exclude:
                    return p
        return None

    def _req(self, method, url, proxy=None, use_token=False, **kw):
        headers = dict(GC_DEFAULT_HEADERS)
        if use_token and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if "headers" in kw:
            headers.update(kw.pop("headers"))
        proxies = {"http": proxy, "https": proxy} if proxy else None
        return requests.request(method, url, headers=headers,
                                proxies=proxies, timeout=20, **kw)

    def fetch_leaders(self):
        r = requests.get(GC_LEADERS, headers=GC_DEFAULT_HEADERS, timeout=15)
        r.raise_for_status()
        d = r.json()

        gold = [{"name": p.get("name") or "?",
                 "value": p.get("gold", 0) or 0,
                 "photo": p.get("photo_url") or ""}
                for p in d.get("top_gold", []) or []]

        diamond = [{"name": p.get("name") or "?",
                    "value": p.get("diamond", 0) or 0,
                    "photo": p.get("photo_url") or ""}
                   for p in d.get("top_diamond", []) or []]

        tasks = [{"name": t.get("name") or "?",
                  "company": t.get("company") or "?",
                  "offer": t.get("offer_name") or "?",
                  "gold": t.get("gold_earned", 0) or 0,
                  "completed": t.get("completed_at") or "",
                  "photo": t.get("photo_url") or ""}
                 for t in d.get("recent_tasks", []) or []]

        purchases = [{"user_id": str(p.get("user_id") or ""),
                      "name": p.get("user_name") or "?",
                      "product": (p.get("product_title_en") or
                                  p.get("product_title_tr") or "?"),
                      "currency": p.get("currency") or "",
                      "time": p.get("purchase_time") or "",
                      "photo": p.get("photo_url") or ""}
                     for p in d.get("recent_purchases", []) or []]

        return {"gold": gold, "diamond": diamond,
                "tasks": tasks, "purchases": purchases}

    def fetch_store(self):
        r = requests.get(GC_STORE, headers=GC_DEFAULT_HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()

    def collect_ids(self, leaders=None):
        if leaders is None:
            leaders = self.fetch_leaders()
        numeric, hashed = set(), set()
        for p in leaders["purchases"]:
            uid = p.get("user_id") or ""
            if not uid:
                continue
            try:
                if uid.isdigit():
                    numeric.add(int(uid))
                else:
                    hashed.add(uid)
            except Exception:
                continue
        return sorted(numeric), sorted(hashed)

    def refer(self, target_id, proxy=None):
        params = {"referred_user_id": str(target_id), "ref_code": self.ref_code}
        try:
            r = self._req("GET", GC_REFER, proxy=proxy, use_token=True, params=params)
            if r.status_code != 200:
                return {"success": False, "reason": f"HTTP_{r.status_code}"}
            return r.json()
        except Exception as e:
            return {"success": False, "reason": f"CONN:{e}"}

    def harvest(self, max_attempts=None, progress_cb=None, delay=None):
        result = {"ok": 0, "ip_limit": 0, "already": 0, "invalid": 0,
                  "gold": 0, "hits": [], "reason": "", "attempts": 0,
                  "skipped_seen": 0}

        proxy_pool = self._load_proxies()
        if delay is None:
            delay = max(0.5, 3.0 / max(1, len(proxy_pool))) if proxy_pool else 1.0

        try:
            leaders = self.fetch_leaders()
        except Exception as e:
            result["reason"] = f"fetch_failed:{e}"
            return result

        numeric, hashed = self.collect_ids(leaders)
        queue = numeric + hashed

        seen_rows = db_exec("SELECT uid FROM seen_ids WHERE ref_code=?",
                            (self.ref_code,), "all")
        seen = {r["uid"] for r in seen_rows}
        fresh = [uid for uid in queue if str(uid) not in seen]
        result["skipped_seen"] = len(queue) - len(fresh)
        queue = fresh

        if max_attempts:
            queue = queue[:max_attempts]

        total = len(queue)
        banned = set()

        for i, uid in enumerate(queue):
            if proxy_pool and len(banned) >= len(proxy_pool):
                banned.clear()
            proxy = self._next_proxy(exclude=banned) if proxy_pool else None

            res = self.refer(uid, proxy=proxy)
            reason = res.get("reason", "") or ""
            result["attempts"] += 1

            db_result = "failed"
            if res.get("success"):
                g = res.get("referred_gold", 0) or 0
                result["ok"] += 1
                result["gold"] += g
                result["hits"].append({"uid": uid, "gold": g})
                db_result = "success"
            elif "Aynı IP" in reason:
                result["ip_limit"] += 1
                if proxy:
                    banned.add(proxy)
                db_result = "same_ip"
            elif "Zaten" in reason:
                result["already"] += 1
                db_result = "already"
            elif "Geçersiz" in reason:
                result["invalid"] += 1
                db_result = "invalid"
            elif reason.startswith("HTTP_429"):
                if proxy:
                    banned.add(proxy)
                db_result = "rate_limit"

            try:
                db_exec("INSERT OR REPLACE INTO seen_ids(uid,ref_code,result) VALUES(?,?,?)",
                        (str(uid), self.ref_code, db_result))
            except Exception:
                pass

            if progress_cb:
                try:
                    progress_cb(i + 1, total, result["ok"], result["gold"])
                except Exception:
                    pass
            time.sleep(delay)

        return result

    @staticmethod
    def fmt_gold(v):
        try:
            return f"{int(v):,}".replace(",", ".")
        except Exception:
            return str(v)


_gc_client = None


def get_gc():
    global _gc_client
    if _gc_client is None:
        _gc_client = GiftCodeClient()
    return _gc_client


# ═══════════ Store cache ═══════════
_store_cache = {"data": {}, "ts": 0}
_store_lock = threading.Lock()


def _refresh_store_cache():
    global _store_cache
    try:
        r = requests.get(GC_STORE, headers=GC_DEFAULT_HEADERS, timeout=15)
        r.raise_for_status()
        items = r.json() or []
    except Exception as e:
        log.warning(f"store fetch: {e}")
        return

    mapping = {}
    for it in items:
        for key in (it.get("title_en"), it.get("title_tr")):
            if key:
                mapping[key.lower().strip()] = {
                    "gold": it.get("gold_price", 0) or 0,
                    "cost": it.get("cost", 0) or 0,
                    "image": it.get("image_url") or "",
                    "on_stock": bool(it.get("on_stock")),
                }

    with _store_lock:
        _store_cache["data"] = mapping
        _store_cache["ts"] = time.time()
    log.info(f"store cache: {len(mapping)} keys")


def store_lookup(title):
    if not title:
        return None
    with _store_lock:
        data = _store_cache["data"]
        age = time.time() - _store_cache["ts"]
    if age > STORE_CACHE_TTL or not data:
        _refresh_store_cache()
        with _store_lock:
            data = _store_cache["data"]
    return data.get(str(title).lower().strip())


def store_loop():
    time.sleep(30)
    _refresh_store_cache()
    while True:
        time.sleep(STORE_CACHE_TTL)
        _refresh_store_cache()


# ═══════════ HTML renderers ═══════════
def _esc(s):
    return html.escape(str(s or ""))


def gc_send(chat_id, text, reply_markup=None):
    try:
        main_bot.send_message(chat_id, text, reply_markup=reply_markup,
                              parse_mode="HTML")
    except Exception:
        plain = (text.replace("<b>", "").replace("</b>", "")
                     .replace("<i>", "").replace("</i>", "")
                     .replace("<code>", "").replace("</code>", ""))
        try:
            main_bot.send_message(chat_id, plain, reply_markup=reply_markup)
        except Exception as e:
            log.error(f"gc_send: {e}")


def render_gold(leaders, top=50):
    gold = leaders.get("gold", [])[:top]
    if not gold:
        return "🏆 <b>لا يوجد متصدرون حالياً.</b>"
    lines = ["🏆 <b>أعلى اللاعبين — الذهب (GP)</b>",
             "━━━━━━━━━━━━━━━━━━━━"]
    for i, p in enumerate(gold, 1):
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}.".rjust(3)
        lines.append(f"{medal}  {_esc(p['name'])[:22]}")
        lines.append(f"      💰 {_esc(GiftCodeClient.fmt_gold(p['value']))} GP")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_diamond(leaders, top=50):
    d = leaders.get("diamond", [])[:top]
    if not d:
        return "💎 <b>لا توجد بيانات.</b>"
    lines = ["💎 <b>أعلى اللاعبين — الألماس</b>",
             "━━━━━━━━━━━━━━━━━━━━"]
    for i, p in enumerate(d, 1):
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}.".rjust(3)
        lines.append(f"{medal}  {_esc(p['name'])[:22]}")
        lines.append(f"      💎 {_esc(p['value'])}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_tasks(leaders, top=20):
    tasks = leaders.get("tasks", [])[:top]
    if not tasks:
        return "📭 <b>لا توجد مهام حديثة.</b>"
    lines = ["📋 <b>آخر المهام المكتملة</b>",
             "━━━━━━━━━━━━━━━━━━━━"]
    for i, t in enumerate(tasks, 1):
        lines.append(f"{i}. <b>{_esc(t['name'])[:22]}</b>")
        lines.append(f"   🏢 {_esc(t['company'])[:20]}")
        lines.append(f"   📌 {_esc(t['offer'])[:30]}")
        lines.append(f"   💰 {_esc(GiftCodeClient.fmt_gold(t['gold']))} GP")
        if t.get("completed"):
            lines.append(f"   🕒 {_esc(t['completed'])}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_purchases(leaders, is_owner=False, top=20):
    ps = leaders.get("purchases", [])[:top]
    if not ps:
        return "🛒 <b>لا توجد مشتريات حديثة.</b>"
    lines = ["🛒 <b>آخر المشتريات</b>",
             "━━━━━━━━━━━━━━━━━━━━"]
    for i, p in enumerate(ps, 1):
        lines.append(f"{i}. <b>{_esc(p['name'])[:22]}</b>")
        lines.append(f"   📦 {_esc(p['product'])[:32]}")
        if p.get("currency"):
            lines.append(f"   💱 {_esc(p['currency'])}")
        if is_owner and p.get("user_id"):
            lines.append(f"   🆔 <code>{_esc(p['user_id'])}</code>")
        if p.get("time"):
            lines.append(f"   🕒 {_esc(p['time'])}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_top_refs(limit=10):
    rows = db_exec("""SELECT u.user_id, u.first_name, u.username,
        (SELECT COUNT(*) FROM referrals r WHERE r.referrer=u.user_id) AS c
        FROM users u ORDER BY c DESC LIMIT ?""", (limit,), "all")
    rows = [r for r in rows if r["c"] > 0]
    if not rows:
        return "🏅 <b>لا توجد إحالات بعد.</b>"
    lines = ["🏅 <b>أكثر المُحيلين</b>",
             "━━━━━━━━━━━━━━━━━━━━"]
    for i, r in enumerate(rows, 1):
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}.".rjust(3)
        name = _esc(r["first_name"] or "?")
        if r["username"]:
            name += f"  (@{_esc(r['username'])})"
        lines.append(f"{medal}  {name}")
        lines.append(f"      🎯 {r['c']} إحالة")
        lines.append("")
    return "\n".join(lines).rstrip()


# ═══════════════════════ Original APIs ═══════════════════════
BASE_URL = "https://giftcode.betelgeuse.app/api/referrer"
TOKEN_API = ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
             "eyJzdWIiOiJndGF2NTEwMzFAZ21haWwuY29tIn0."
             "LR0lbOdO6Qq5d_4X0jKUC6mx18PP1-w2ChvBXQTETw0")


def giftcode_send(code, target, proxy=None):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = requests.get(BASE_URL,
                         params={"referred_user_id": str(target),
                                 "ref_code": str(code)},
                         headers={"Authorization": TOKEN_API,
                                  "User-Agent": "okhttp/5.3.2"},
                         timeout=15, proxies=proxies)
        if r.status_code == 200:
            d = r.json()
            if d.get("success"):
                return "success", d.get("referred_gold", 0)
            reason = d.get("reason", "")
            if "Zaten" in reason: return "already", reason
            if "Geçersiz" in reason: return "invalid", reason
            if "Aynı IP" in reason: return "same_ip", reason
            return "failed", reason
        if r.status_code == 429:
            return "rate_limit", "429"
        return "failed", f"HTTP_{r.status_code}"
    except Exception as e:
        return "error", str(e)


FIREBASE_KEY = "AIzaSyDR1RcaMP9IOmIy7i_daFPNr3e7kmWid6o"
FB_URL = "https://us-central1-gift-sheep-b21df.cloudfunctions.net/submitReferral"


def firebase_signup(email, password):
    try:
        r = requests.post(
            "https://identitytoolkit.googleapis.com/v1/accounts:signUp",
            params={"key": FIREBASE_KEY},
            json={"email": email, "password": password, "returnSecureToken": True},
            timeout=30)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def firebase_refer(token, code, proxy=None):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = requests.post(FB_URL,
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json; charset=utf-8",
                                   "User-Agent": "okhttp/3.12.13"},
                          json={"data": {"code": code}},
                          timeout=30, proxies=proxies)
        d = r.json()
        return (d.get("result", {}).get("success", False),
                d.get("result", {}).get("message", ""))
    except Exception as e:
        return False, str(e)


# ═══════════════════════ BOT INSTANCES ═══════════════════════
main_bot     = telebot.TeleBot(MAIN_BOT_TOKEN,     parse_mode=None)
showcase_bot = telebot.TeleBot(SHOWCASE_BOT_TOKEN, parse_mode="HTML")

app = Flask(__name__)
attack_status, attack_mode = {}, {}
_cancel = {}
_gc_lock = threading.Lock()
proxy_fallback_notified = {}
showcase_feed_enabled = {"on": True}


def is_owner(uid): return str(uid) == str(OWNER_ID)


def is_banned(uid):
    r = db_exec("SELECT banned FROM users WHERE user_id=?", (uid,), "one")
    return bool(r and r["banned"])


def subscribed_tg(uid):
    if is_owner(uid): return True
    try:
        m = main_bot.get_chat_member(f"@{CHANNEL_TG}", uid)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False


def subscribed_yt(uid):
    if is_owner(uid): return True
    u = get_user(uid)
    if not u or not u["youtube_verified"]:
        return False
    try:
        d = datetime.fromisoformat(u["youtube_date"])
        if (datetime.now() - d).days > 7:
            return False
    except Exception:
        return False
    return True


def check_subs(uid):
    if is_owner(uid): return True, None
    if not subscribed_tg(uid): return False, "telegram"
    if not subscribed_yt(uid): return False, "youtube"
    return True, None


# ═══════════════════════ MAIN MENU ═══════════════════════
def main_menu(uid, chat_id):
    u = get_user(uid)
    if not u: return
    name = u["first_name"] or "user"
    mode = u["mode"]
    kb = InlineKeyboardMarkup(row_width=2)
    if mode == "giftcode":
        kb.add(InlineKeyboardButton(tr(uid, "set_referral"), callback_data="set_referral"),
               InlineKeyboardButton(tr(uid, "set_start"), callback_data="set_start"))
    else:
        kb.add(InlineKeyboardButton(tr(uid, "set_referral"), callback_data="set_referral"))
    kb.add(
        InlineKeyboardButton(tr(uid, "start_attack"), callback_data="start_attack"),
        InlineKeyboardButton(tr(uid, "stop_attack"), callback_data="stop_attack"),
        InlineKeyboardButton(tr(uid, "toggle_mode"), callback_data="toggle_mode"),
        InlineKeyboardButton(tr(uid, "status"), callback_data="status"),
        InlineKeyboardButton(tr(uid, "referral"), callback_data="my_referral"),
        InlineKeyboardButton(tr(uid, "add_proxy"), callback_data="user_add_proxy"),
        InlineKeyboardButton(tr(uid, "daily"), callback_data="daily"),
        InlineKeyboardButton(tr(uid, "leaderboard"), callback_data="leaderboard"),
        InlineKeyboardButton(tr(uid, "my_stats"), callback_data="my_stats"),
        InlineKeyboardButton(tr(uid, "lang"), callback_data="lang_menu"),
        InlineKeyboardButton(tr(uid, "quick_menu"), callback_data="show_quick_menu"),
        InlineKeyboardButton("🎯 لوحة GiftCode", callback_data="gc_panel"),
        InlineKeyboardButton("🏅 المتصدرون بالإحالة", callback_data="gc_top_refs"),
        InlineKeyboardButton("🛒 آخر المشتريات", url=SHOWCASE_URL),
    )
    if is_owner(uid):
        kb.add(InlineKeyboardButton(tr(uid, "owner"), callback_data="owner_commands"))
    stats = history_stats(uid)
    text = (f"{tr(uid,'welcome', name=name)}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💎 {tr(uid,'points')}: {u['points']}\n"
            f"🔑 {tr(uid,'code')}: {u['referral_code']}\n"
            f"🔄 {tr(uid,'mode')}: {mode.upper()}\n"
            f"✅ {tr(uid,'success')}: {stats['success']}\n"
            f"🔥 Streak: {u['streak']}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{tr(uid,'choose')}")
    try:
        main_bot.send_message(chat_id, text, reply_markup=kb)
    except Exception as e:
        log.error(f"main_menu: {e}")


def quick_menu(uid, chat_id):
    kb = {
        "keyboard": [
            [{"text": "▶️ Start", "color": "success"}],
            [{"text": "⏹️ Stop", "color": "danger"},
             {"text": "📊 Status", "color": "primary"}],
            [{"text": "🎁 Daily", "color": "success"},
             {"text": "🏆 Top", "color": "primary"}],
            [{"text": "📊 Stats", "color": "primary"},
             {"text": "🔗 Link", "color": "primary"}],
            [{"text": "🎯 GiftCode", "color": "success"}],
        ],
        "resize_keyboard": True,
        "is_persistent": False,
    }
    try:
        requests.post(f"https://api.telegram.org/bot{MAIN_BOT_TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": tr(uid, "quick_menu"),
                            "reply_markup": kb}, timeout=10)
    except Exception as e:
        log.error(f"quick: {e}")


# ═══════════════════════ ATTACK ═══════════════════════
def process_giftcode(uid, target, proxy):
    u = get_user(uid)
    code = u["referral_code"]
    res, reason = giftcode_send(code, target, proxy)
    log_history(uid, "giftcode", target,
                res if res in ("success", "already", "failed") else "failed", proxy)
    return res, reason


def process_sheep(uid, target, proxy):
    email = f"fb_{uuid.uuid4().hex[:10]}@temp-mail.org"
    auth = firebase_signup(email, "Test@2026")
    if not auth or "idToken" not in auth:
        return "failed", "firebase_signup"
    u = get_user(uid)
    success, msg = firebase_refer(auth["idToken"], u["referral_code"] or "W27PO5", proxy)
    log_history(uid, "giftsheep", target, "success" if success else "failed", proxy)
    return ("success" if success else "failed"), msg


def attack_loop(uid, chat_id):
    u = get_user(uid)
    if not u: return
    mode = u["mode"]
    current = u["start_number"]
    attempts = successes = 0
    banned = set()
    attack_status[uid] = {"running": True, "number": current,
                          "attempts": 0, "success": 0}
    try:
        main_bot.send_message(chat_id, f"🚀 بدء {mode.upper()}")
    except Exception:
        pass

    progress_msg = None
    try:
        progress_msg = main_bot.send_message(chat_id, "⏳ بدء...").message_id
    except Exception:
        pass

    last_update = time.time()

    while attack_status.get(uid, {}).get("running"):
        if _cancel.get(uid): break
        if is_banned(uid): break
        if not is_owner(uid) and get_points(uid) <= 0:
            try:
                main_bot.send_message(chat_id, f"⚠️ نفدت نقاطك\n{get_referral_link(uid)}")
            except Exception:
                pass
            break

        proxy = pick_proxy(banned)
        if not proxy:
            if not proxy_fallback_notified.get(uid):
                try:
                    main_bot.send_message(chat_id,
                        "ℹ️ لا بروكسيات متوفرة — يعمل الآن باتصال مباشر.\n"
                        "أضف بروكسي لتحسين النتائج.")
                except Exception:
                    pass
                proxy_fallback_notified[uid] = True

        attempts += 1
        attack_status[uid]["attempts"] = attempts

        if mode == "giftcode":
            target = str(current)
            current += 1
            attack_status[uid]["number"] = current
            res, reason = process_giftcode(uid, target, proxy)
            if res == "invalid":
                continue
        else:
            target = uuid.uuid4().hex[:8]
            res, reason = process_sheep(uid, target, proxy)

        if res == "success":
            successes += 1
            attack_status[uid]["success"] = successes
            if not is_owner(uid):
                add_points(uid, -1)
            if proxy:
                proxy_score(proxy, True)
            try:
                main_bot.send_message(OWNER_ID,
                    f"✅ {mode.upper()} | {get_display(uid)}\n"
                    f"ID: {uid}\nRemaining: {get_points(uid)}")
            except Exception:
                pass
        elif res == "already":
            if proxy:
                proxy_score(proxy, True)
        elif res in ("same_ip", "rate_limit", "error"):
            if proxy:
                proxy_score(proxy, False)
                banned.add(proxy)
            else:
                time.sleep(2)
        elif res == "failed":
            if proxy:
                proxy_score(proxy, False)
            time.sleep(1)

        if time.time() - last_update > 3 and progress_msg:
            rate = (successes / attempts * 100) if attempts else 0
            bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
            try:
                main_bot.edit_message_text(
                    f"📊 {mode.upper()}\n"
                    f"🎯 #{attempts} | ✅ {successes}\n"
                    f"📈 {rate:.1f}% {bar}\n"
                    f"💎 {get_points(uid)}",
                    chat_id=chat_id, message_id=progress_msg)
                last_update = time.time()
            except Exception:
                pass

    attack_status[uid]["running"] = False
    _cancel.pop(uid, None)
    try:
        main_bot.send_message(chat_id, f"⏹️ توقف. نجاح: {successes}/{attempts}")
    except Exception:
        pass


queue_jobs = PriorityQueue()


def worker():
    while True:
        _, uid, chat_id = queue_jobs.get()
        try:
            attack_loop(uid, chat_id)
        except Exception as e:
            log.error(f"worker: {e}")
        finally:
            queue_jobs.task_done()


for _ in range(WORKERS):
    threading.Thread(target=worker, daemon=True).start()


# ═══════════════════════ Helpers ═══════════════════════
def get_display(uid):
    u = get_user(uid)
    if not u: return str(uid)
    n = u["first_name"] or "?"
    return f"{n} (@{u['username']})" if u["username"] else n


def get_referral_link(uid):
    return f"https://t.me/{BOT_USERNAME}?start={uid}"


def find_uid_by_input(s):
    s = s.strip()
    if not s: return None
    if s.lstrip("@").isdigit():
        return int(s.lstrip("@"))
    uname = s.lstrip("@").lower()
    r = db_exec("SELECT user_id FROM users WHERE LOWER(username)=?",
                (uname,), "one")
    return r["user_id"] if r else None


def _purchase_key(p):
    return f"{p.get('user_id')}|{p.get('product')}|{p.get('time')}"


# ═══════════════════════ Background: main owner feed ═══════════════════════
def live_purchase_feed():
    time.sleep(15)
    while True:
        try:
            if get_setting("gc_feed_enabled", "1") == "1":
                leaders = get_gc().fetch_leaders()
                for p in leaders.get("purchases", [])[:20]:
                    key = _purchase_key(p)
                    if not key or key == "||":
                        continue
                    if db_exec("SELECT purchase_key FROM purchase_seen "
                               "WHERE purchase_key=?", (key,), "one"):
                        continue
                    db_exec("INSERT OR IGNORE INTO purchase_seen(purchase_key) VALUES(?)",
                            (key,))
                    try:
                        main_bot.send_message(
                            OWNER_ID,
                            f"🛒 مشتري جديد\n"
                            f"👤 {p['name']}\n"
                            f"📦 {p['product']} [{p['currency']}]\n"
                            f"🆔 {p['user_id']}\n"
                            f"🕒 {p['time']}")
                    except Exception:
                        pass
        except Exception as e:
            log.debug(f"feed: {e}")
        time.sleep(GC_FEED_INTERVAL)


def auto_harvest_loop():
    time.sleep(60)
    while True:
        try:
            if get_setting("gc_auto_enabled", "0") == "1":
                if not _gc_lock.locked():
                    with _gc_lock:
                        log.info("auto_harvest: starting")
                        res = get_gc().harvest(delay=None)
                        log.info(f"auto_harvest: {res['ok']} ok, "
                                 f"{res['gold']} gold, skipped={res['skipped_seen']}")
                        try:
                            main_bot.send_message(
                                OWNER_ID,
                                f"🤖 مسح تلقائي\n"
                                f"✅ {res['ok']} | 💰 {res['gold']}\n"
                                f"🚫 IP: {res['ip_limit']} | "
                                f"🔁 مُحال: {res['already']} | "
                                f"⏭️ متجاوز: {res['skipped_seen']}")
                        except Exception:
                            pass
        except Exception as e:
            log.error(f"auto_harvest: {e}")
        time.sleep(GC_AUTO_INTERVAL)


# ═══════════════════════ Background: showcase loop ═══════════════════════
def showcase_load_channel():
    return get_showcase_cfg("channel_id", os.getenv("SHOWCASE_CHANNEL", ""))


def showcase_caption(p):
    product = p.get("product", "?")
    info = store_lookup(product)

    pl = product.lower()
    if "diamond" in pl: icon = "💎"
    elif "steam" in pl: icon = "🎮"
    elif "robux" in pl: icon = "🟢"
    elif "valorant" in pl or "lol" in pl or "rp" in pl: icon = "🎯"
    elif "vpn" in pl: icon = "🔐"
    elif "play" in pl or "wallet" in pl: icon = "🛒"
    else: icon = "📦"

    lines = [
        "🛒 <b>مشترى جديد</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👤 <b>{_esc(p['name'])}</b>",
        f"{icon} <b>{_esc(product)}</b>",
    ]
    if info and info.get("gold"):
        lines.append(f"💰 {_esc(GiftCodeClient.fmt_gold(info['gold']))} GP")
    if p.get("currency"):
        lines.append(f"💱 {_esc(p['currency'])}")
    if p.get("time"):
        lines.append(f"🕒 {_esc(str(p['time']).replace('T', ' ')[:16])}")
    return "\n".join(lines)


def showcase_publish(p):
    channel = showcase_load_channel()
    if not channel:
        log.warning("showcase: no channel")
        return False
    caption = showcase_caption(p)
    photo = p.get("photo")
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🚀 احصل على GiftCode",
                                url=f"https://t.me/{BOT_USERNAME}"))
    try:
        if photo:
            showcase_bot.send_photo(channel, photo, caption=caption,
                                    reply_markup=kb, parse_mode="HTML")
        else:
            showcase_bot.send_message(channel, caption, reply_markup=kb,
                                      parse_mode="HTML")
        log.info(f"showcase published: {p['name']} — {p['product']}")
        return True
    except Exception as e:
        log.error(f"showcase publish: {e}")
        try:
            showcase_bot.send_message(channel, caption, reply_markup=kb,
                                      parse_mode="HTML")
            return True
        except Exception as e2:
            log.error(f"showcase publish fallback: {e2}")
            return False


def showcase_feed_loop():
    time.sleep(25)
    log.info("showcase feed loop started")
    while True:
        try:
            if showcase_feed_enabled["on"]:
                leaders = get_gc().fetch_leaders()
                new_count = 0
                for p in leaders.get("purchases", [])[:20]:
                    key = _purchase_key(p)
                    if not key or key == "||":
                        continue
                    if db_exec("SELECT purchase_key FROM showcase_seen "
                               "WHERE purchase_key=?", (key,), "one"):
                        continue
                    db_exec("INSERT OR IGNORE INTO showcase_seen(purchase_key) VALUES(?)",
                            (key,))
                    if showcase_publish(p):
                        new_count += 1
                        time.sleep(2)
                if new_count:
                    log.info(f"showcase: published {new_count}")
        except Exception as e:
            log.error(f"showcase_feed_loop: {e}")
        time.sleep(SHOWCASE_POLL_INTERVAL)


def showcase_feed_now(chat_id):
    try:
        leaders = get_gc().fetch_leaders()
        new = 0
        for p in leaders.get("purchases", [])[:20]:
            key = _purchase_key(p)
            if not key or key == "||":
                continue
            if db_exec("SELECT 1 FROM showcase_seen WHERE purchase_key=?",
                       (key,), "one"):
                continue
            db_exec("INSERT OR IGNORE INTO showcase_seen(purchase_key) VALUES(?)", (key,))
            if showcase_publish(p):
                new += 1
                time.sleep(2)
        showcase_bot.send_message(chat_id, f"✅ نُشر {new} مشترى جديد")
    except Exception as e:
        showcase_bot.send_message(chat_id, f"❌ {e}")


# ═══════════════════════ GitHub sync (اختياري) ═══════════════════════
def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"}


def gh_pull_user_data():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return 0
    os.makedirs(OLD_DATA_DIR, exist_ok=True)
    downloaded = 0
    try:
        url = f"{GH_API}/repos/{GITHUB_REPO}/contents/{OLD_DATA_DIR}"
        r = requests.get(url, headers=_gh_headers(), timeout=15)
        if r.status_code != 200:
            return 0
        for item in r.json():
            if item["type"] != "file" or item["name"] == ".gitkeep":
                continue
            content = requests.get(item["download_url"],
                                   headers=_gh_headers(), timeout=15).content
            with open(os.path.join(OLD_DATA_DIR, item["name"]), "wb") as f:
                f.write(content)
            downloaded += 1
        log.info(f"gh_pull: {downloaded} files")
    except Exception as e:
        log.error(f"gh_pull: {e}")
    return downloaded


def gh_push_user_data():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False
    if not os.path.isdir(OLD_DATA_DIR):
        return False
    pushed = 0
    for fn in os.listdir(OLD_DATA_DIR):
        if fn == ".gitkeep":
            continue
        path = os.path.join(OLD_DATA_DIR, fn)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                content = base64.b64encode(f.read()).decode()
            url = f"{GH_API}/repos/{GITHUB_REPO}/contents/{OLD_DATA_DIR}/{fn}"
            r = requests.get(url, headers=_gh_headers(), timeout=15)
            sha = r.json().get("sha") if r.status_code == 200 else None
            body = {"message": f"update {fn}", "content": content}
            if sha:
                body["sha"] = sha
            r = requests.put(url, headers=_gh_headers(), json=body, timeout=20)
            if r.status_code in (200, 201):
                pushed += 1
        except Exception as e:
            log.error(f"gh_push {fn}: {e}")
    if pushed:
        log.info(f"gh_push: {pushed} files")
    return pushed > 0


def gh_sync_loop():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log.info("gh_sync: disabled (no repo/token)")
        return
    time.sleep(120)
    while True:
        try:
            gh_push_user_data()
        except Exception as e:
            log.error(f"gh_sync: {e}")
        time.sleep(DATA_SYNC_INTERVAL)


# ═══════════════════════ MAIN BOT handlers ═══════════════════════
@main_bot.message_handler(commands=["start"])
def main_cmd_start(m):
    uid = m.from_user.id
    ensure_user(uid, m.from_user.first_name or "", m.from_user.username or "")
    if is_banned(uid):
        main_bot.reply_to(m, "🚫 أنت محظور.")
        return
    ref = None
    if m.text and m.text.startswith("/start"):
        p = m.text.split()
        if len(p) > 1 and p[1].isdigit():
            ref = int(p[1])
    if ref and ref != uid:
        r = db_exec("SELECT new_user FROM referrals WHERE new_user=?", (uid,), "one")
        if not r:
            ok, _ = check_subs(uid)
            if ok:
                db_exec("INSERT OR IGNORE INTO referrals(new_user,referrer) VALUES(?,?)",
                        (uid, ref))
                add_points(ref, REFERRAL_POINTS)
                main_bot.reply_to(m, f"✅ +{REFERRAL_POINTS} نقطة للمُحيل")
    ok, kind = check_subs(uid)
    if not ok:
        kb = InlineKeyboardMarkup(row_width=1)
        if kind == "telegram":
            kb.add(InlineKeyboardButton("📢 اشترك", url=f"https://t.me/{CHANNEL_TG}"))
        if kind == "youtube":
            kb.add(InlineKeyboardButton("🎬 يوتيوب", callback_data="verify_youtube"))
        kb.add(InlineKeyboardButton("✅ تحقق", callback_data="check_sub"))
        main_bot.reply_to(m, tr(uid, "not_sub"), reply_markup=kb)
        return
    main_menu(uid, m.chat.id)


@main_bot.message_handler(func=lambda m: m.text == "▶️ Start")
def main_qs_start(m):
    uid = m.from_user.id
    ensure_user(uid)
    if not attack_status.get(uid, {}).get("running"):
        attack_status[uid] = {"running": True}
        queue_jobs.put((0 if is_owner(uid) else -get_points(uid), uid, m.chat.id))
        main_bot.reply_to(m, "🚀 بدأ")


@main_bot.message_handler(func=lambda m: m.text == "⏹️ Stop")
def main_qs_stop(m):
    uid = m.from_user.id
    _cancel[uid] = True
    if uid in attack_status:
        attack_status[uid]["running"] = False
    main_bot.reply_to(m, "⏹️ جاري الإيقاف")


@main_bot.message_handler(func=lambda m: m.text == "📊 Status")
def main_qs_status(m):
    uid = m.from_user.id
    ensure_user(uid)
    st = attack_status.get(uid, {}).get("running", False)
    sts = history_stats(uid)
    main_bot.reply_to(m, f"📊 Status\n▶️ {'ON' if st else 'OFF'}\n"
                         f"💎 {get_points(uid)}\n✅ {sts['success']}")


@main_bot.message_handler(func=lambda m: m.text == "🎁 Daily")
def main_qs_daily(m):
    do_daily(m.from_user.id, m.chat.id)


@main_bot.message_handler(func=lambda m: m.text == "🏆 Top")
def main_qs_top(m):
    do_leaderboard(m.chat.id)


@main_bot.message_handler(func=lambda m: m.text == "📊 Stats")
def main_qs_stats(m):
    do_stats(m.from_user.id, m.chat.id)


@main_bot.message_handler(func=lambda m: m.text == "🔗 Link")
def main_qs_link(m):
    main_bot.reply_to(m, f"🔗 {get_referral_link(m.from_user.id)}")


@main_bot.message_handler(func=lambda m: m.text == "🎯 GiftCode")
def main_qs_gc(m):
    gc_send(m.chat.id, "🎯 <b>لوحة GiftCode</b> — استخدم الأزرار في القائمة.")


# ═══════════════════════ MAIN BOT owner commands ═══════════════════════
@main_bot.message_handler(commands=["reimport"])
def main_cmd_reimport(m):
    if not is_owner(m.from_user.id): return
    u, p = reimport_sessions(verbose=True)
    main_bot.reply_to(m, f"✅ مستخدمون جدد: {u}\n💎 نقاط مستوردة: {p}")


@main_bot.message_handler(commands=["sessions"])
def main_cmd_sessions(m):
    if not is_owner(m.from_user.id): return
    n = db_exec("SELECT COUNT(*) c FROM users", fetch="one")["c"]
    old = 0
    sp = os.path.join(OLD_DATA_DIR, "user_sessions.json")
    if os.path.exists(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                old = len(json.load(f))
        except Exception:
            old = -1
    main_bot.reply_to(m,
        f"👥 في قاعدة البيانات: {n}\n"
        f"📁 في user_sessions.json: {old if old >= 0 else 'خطأ قراءة'}")


@main_bot.message_handler(commands=["sync"])
def main_cmd_sync(m):
    if not is_owner(m.from_user.id): return
    u, p = reimport_sessions(verbose=True)
    n = db_exec("SELECT COUNT(*) c FROM users", fetch="one")["c"]
    main_bot.reply_to(m, f"🔄 زامن\n+{u} مستخدم\n+{p} نقاط\nالإجمالي: {n}")


@main_bot.message_handler(commands=["gh_sync"])
def main_cmd_gh_sync(m):
    if not is_owner(m.from_user.id): return
    ok = gh_push_user_data()
    main_bot.reply_to(m, "✅ تمت المزامنة" if ok else "❌ فشل (تحقق من GH_TOKEN/GH_REPO)")


@main_bot.message_handler(commands=["broadcast"])
def main_cmd_broadcast(m):
    if not is_owner(m.from_user.id): return
    text = m.text.replace("/broadcast", "").strip()
    if not text:
        main_bot.reply_to(m, "الاستخدام: /broadcast النص")
        return
    users = db_exec("SELECT user_id FROM users WHERE banned=0", fetch="all")
    sent = failed = 0
    for row in users:
        try:
            main_bot.send_message(row["user_id"], f"📢 إعلان\n\n{text}")
            sent += 1
            time.sleep(0.05)
        except Exception:
            failed += 1
    main_bot.reply_to(m, f"✅ {sent} | ❌ {failed}")
    audit(m.from_user.id, "broadcast", f"sent={sent},failed={failed}")


@main_bot.message_handler(commands=["emergency_stop"])
def main_cmd_estop(m):
    if not is_owner(m.from_user.id): return
    n = 0
    for uid in list(attack_status.keys()):
        if attack_status[uid].get("running"):
            attack_status[uid]["running"] = False
            _cancel[uid] = True
            n += 1
    main_bot.reply_to(m, f"🛑 أُوقف {n}")
    audit(m.from_user.id, "estop", str(n))


@main_bot.message_handler(commands=["export"])
def main_cmd_export(m):
    if not is_owner(m.from_user.id): return
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["uid", "name", "username", "points", "success",
                "failed", "streak", "lang"])
    for row in db_exec("SELECT * FROM users", fetch="all"):
        s = history_stats(row["user_id"])
        w.writerow([row["user_id"], row["first_name"], row["username"],
                    row["points"], s["success"], s["failed"],
                    row["streak"], row["lang"]])
    bio = io.BytesIO(buf.getvalue().encode("utf-8"))
    bio.name = "users.csv"
    main_bot.send_document(m.chat.id, bio)


@main_bot.message_handler(commands=["ban"])
def main_cmd_ban(m):
    if not is_owner(m.from_user.id): return
    p = m.text.split()
    if len(p) != 2: return
    uid = find_uid_by_input(p[1])
    if uid:
        set_user(uid, banned=1)
        main_bot.reply_to(m, f"🚫 {uid}")
    audit(m.from_user.id, "ban", str(uid))


@main_bot.message_handler(commands=["unban"])
def main_cmd_unban(m):
    if not is_owner(m.from_user.id): return
    p = m.text.split()
    if len(p) != 2: return
    uid = find_uid_by_input(p[1])
    if uid:
        set_user(uid, banned=0)
        main_bot.reply_to(m, f"✅ {uid}")
    audit(m.from_user.id, "unban", str(uid))


@main_bot.message_handler(commands=["live"])
def main_cmd_live(m):
    if not is_owner(m.from_user.id): return
    lines = [f"🔴 {get_display(int(uid))} #{attack_status[uid].get('number',0)}"
             for uid in attack_status if attack_status[uid].get("running")]
    main_bot.reply_to(m, "📡 Active:\n" + ("\n".join(lines) if lines else "لا يوجد"))


@main_bot.message_handler(commands=["auto_harvest"])
def main_cmd_auto_harvest(m):
    if not is_owner(m.from_user.id): return
    arg = m.text.replace("/auto_harvest", "").strip().lower()
    if arg in ("on", "1", "تشغيل"):
        set_setting("gc_auto_enabled", "1")
        main_bot.reply_to(m, "✅ المسح التلقائي مُفعّل")
    elif arg in ("off", "0", "إيقاف"):
        set_setting("gc_auto_enabled", "0")
        main_bot.reply_to(m, "⏹️ المسح التلقائي مُعطّل")
    else:
        cur = get_setting("gc_auto_enabled", "0")
        main_bot.reply_to(m, f"الحالة: {'ON' if cur=='1' else 'OFF'}\n"
                             f"الاستخدام: /auto_harvest on|off")


@main_bot.message_handler(commands=["feed"])
def main_cmd_feed(m):
    if not is_owner(m.from_user.id): return
    arg = m.text.replace("/feed", "").strip().lower()
    if arg in ("on", "1"):
        set_setting("gc_feed_enabled", "1")
        main_bot.reply_to(m, "✅ تنبيهات المشتريات مُفعّلة")
    elif arg in ("off", "0"):
        set_setting("gc_feed_enabled", "0")
        main_bot.reply_to(m, "⏹️ تنبيهات المشتريات مُعطّلة")
    else:
        cur = get_setting("gc_feed_enabled", "1")
        main_bot.reply_to(m, f"الحالة: {'ON' if cur=='1' else 'OFF'}")


# ═══════════════════════ MAIN BOT callbacks ═══════════════════════
@main_bot.callback_query_handler(func=lambda c: True)
def main_on_callback(c):
    uid = c.from_user.id
    chat_id = c.message.chat.id if c.message else uid
    if is_banned(uid):
        main_bot.answer_callback_query(c.id, "🚫 محظور", show_alert=True)
        return
    if rate_limited(uid):
        main_bot.answer_callback_query(c.id, "⚠️ تمهل", show_alert=True)
        return

    try:
        if c.data == "check_sub":
            ok, _ = check_subs(uid)
            if ok:
                main_bot.answer_callback_query(c.id, "✅ تم")
                main_menu(uid, chat_id)
            else:
                main_bot.answer_callback_query(c.id, "❌ لم تشترك", show_alert=True)
            return

        if c.data == "verify_youtube":
            set_user(uid, youtube_verified=1,
                     youtube_date=datetime.now().isoformat())
            main_bot.answer_callback_query(c.id, "✅ يوتيوب", show_alert=True)
            main_menu(uid, chat_id)
            return

        if c.data == "toggle_mode":
            u = get_user(uid) or {}
            new = "giftsheep" if u.get("mode") == "giftcode" else "giftcode"
            set_user(uid, mode=new)
            main_bot.answer_callback_query(c.id, f"🔄 {new.upper()}", show_alert=True)
            main_menu(uid, chat_id)
            return

        if c.data == "start_attack":
            if attack_status.get(uid, {}).get("running"):
                main_bot.answer_callback_query(c.id, "يعمل بالفعل", show_alert=True)
                return
            if not is_owner(uid) and get_points(uid) <= 0:
                main_bot.answer_callback_query(c.id, "💎 0 نقاط", show_alert=True)
                return
            attack_status[uid] = {"running": True}
            _cancel.pop(uid, None)
            prio = 0 if is_owner(uid) else -get_points(uid)
            queue_jobs.put((prio, uid, chat_id))
            main_bot.answer_callback_query(c.id, "▶️ بدأ", show_alert=True)
            return

        if c.data == "stop_attack":
            _cancel[uid] = True
            if uid in attack_status:
                attack_status[uid]["running"] = False
            main_bot.answer_callback_query(c.id, "⏹️", show_alert=True)
            return

        if c.data == "status":
            st = attack_status.get(uid, {}).get("running", False)
            s = history_stats(uid)
            main_bot.answer_callback_query(c.id)
            main_bot.send_message(chat_id, f"📊\n▶️ {'ON' if st else 'OFF'}\n"
                                            f"💎 {get_points(uid)}\n✅ {s['success']}")
            return

        if c.data == "my_referral":
            main_bot.answer_callback_query(c.id)
            main_bot.send_message(chat_id, f"🔗 {get_referral_link(uid)}")
            return

        if c.data == "my_stats":
            main_bot.answer_callback_query(c.id)
            do_stats(uid, chat_id)
            return

        if c.data == "leaderboard":
            main_bot.answer_callback_query(c.id)
            do_leaderboard(chat_id)
            return

        if c.data == "daily":
            main_bot.answer_callback_query(c.id)
            do_daily(uid, chat_id)
            return

        if c.data == "lang_menu":
            main_bot.answer_callback_query(c.id)
            kb = InlineKeyboardMarkup(row_width=3)
            kb.add(InlineKeyboardButton("🇾🇪 العربية", callback_data="set_lang_ar"),
                   InlineKeyboardButton("🇬🇧 English", callback_data="set_lang_en"))
            main_bot.send_message(chat_id, "🌐 Language:", reply_markup=kb)
            return

        if c.data.startswith("set_lang_"):
            lang = c.data.replace("set_lang_", "")
            set_user(uid, lang=lang)
            main_bot.answer_callback_query(c.id, f"✅ {lang}")
            main_menu(uid, chat_id)
            return

        if c.data == "show_quick_menu":
            main_bot.answer_callback_query(c.id)
            quick_menu(uid, chat_id)
            return

        if c.data == "cancel_step":
            _cancel[uid] = True
            main_bot.answer_callback_query(c.id, "❌ ملغي", show_alert=True)
            main_menu(uid, chat_id)
            return

        if c.data == "set_referral":
            main_bot.answer_callback_query(c.id)
            kb = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ إلغاء", callback_data="cancel_step"))
            msg = main_bot.send_message(chat_id, "🔑 أرسل كود الإحالة:", reply_markup=kb)
            main_bot.register_next_step_handler(msg, step_set_referral)
            return

        if c.data == "set_start":
            main_bot.answer_callback_query(c.id)
            kb = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ إلغاء", callback_data="cancel_step"))
            msg = main_bot.send_message(chat_id, "🔢 أرسل رقم البداية:", reply_markup=kb)
            main_bot.register_next_step_handler(msg, step_set_start)
            return

        if c.data == "user_add_proxy":
            main_bot.answer_callback_query(c.id)
            kb = InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ إلغاء", callback_data="cancel_step"))
            msg = main_bot.send_message(chat_id, "🔐 أرسل البروكسي:", reply_markup=kb)
            main_bot.register_next_step_handler(msg, step_add_proxy)
            return

        if c.data == "owner_commands":
            if not is_owner(uid):
                main_bot.answer_callback_query(c.id, "للمالك فقط", show_alert=True)
                return
            main_bot.answer_callback_query(c.id)
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("➕ بروكسي", callback_data="owner_add_proxy"),
                InlineKeyboardButton("📋 القائمة", callback_data="owner_list_proxies"),
                InlineKeyboardButton("🧪 اختبار", callback_data="owner_test_proxies"),
                InlineKeyboardButton("🗑️ حذف", callback_data="owner_del_proxy"),
                InlineKeyboardButton("📈 إحصائيات", callback_data="owner_stats"),
                InlineKeyboardButton("🔧 نقاط", callback_data="owner_edit_points"),
                InlineKeyboardButton("📢 بث", callback_data="owner_broadcast"),
                InlineKeyboardButton("📡 مباشر", callback_data="owner_live"),
                InlineKeyboardButton("🛑 إيقاف طارئ", callback_data="owner_estop"),
                InlineKeyboardButton("🔙 رجوع", callback_data="start"))
            main_bot.send_message(chat_id, "👑", reply_markup=kb)
            return

        if c.data == "owner_stats":
            if not is_owner(uid): return
            main_bot.answer_callback_query(c.id)
            do_owner_stats(chat_id)
            return

        if c.data == "owner_live":
            if not is_owner(uid): return
            main_bot.answer_callback_query(c.id)
            lines = [f"🔴 {get_display(int(x))} #{attack_status[x].get('number',0)}"
                     for x in attack_status if attack_status[x].get("running")]
            main_bot.send_message(chat_id,
                                  "📡\n" + ("\n".join(lines) if lines else "لا يوجد"))
            return

        if c.data == "owner_estop":
            if not is_owner(uid): return
            n = 0
            for x in list(attack_status.keys()):
                if attack_status[x].get("running"):
                    attack_status[x]["running"] = False
                    _cancel[x] = True
                    n += 1
            main_bot.answer_callback_query(c.id, f"🛑 {n}", show_alert=True)
            return

        if c.data == "owner_broadcast":
            if not is_owner(uid): return
            main_bot.answer_callback_query(c.id)
            msg = main_bot.send_message(chat_id, "📢 أرسل نص الإعلان:")
            main_bot.register_next_step_handler(msg, step_broadcast)
            return

        if c.data == "owner_edit_points":
            if not is_owner(uid): return
            main_bot.answer_callback_query(c.id)
            msg = main_bot.send_message(chat_id, "🔧 @user points أو uid points")
            main_bot.register_next_step_handler(msg, step_edit_points)
            return

        if c.data == "owner_add_proxy":
            if not is_owner(uid): return
            main_bot.answer_callback_query(c.id)
            msg = main_bot.send_message(chat_id, "أرسل البروكسي:")
            main_bot.register_next_step_handler(msg, step_owner_add_proxy)
            return

        if c.data == "owner_list_proxies":
            if not is_owner(uid): return
            main_bot.answer_callback_query(c.id)
            rows = db_exec("SELECT proxy, success, fail FROM proxies "
                           "ORDER BY success DESC LIMIT 50", fetch="all")
            lines = [f"{r['proxy']} ✅{r['success']} ❌{r['fail']}" for r in rows]
            main_bot.send_message(chat_id, "\n".join(lines) if lines else "لا يوجد")
            return

        if c.data == "owner_test_proxies":
            if not is_owner(uid): return
            main_bot.answer_callback_query(c.id, "🧪 اختبار...", show_alert=True)
            proxies = all_proxies()
            alive = batch_test(proxies)
            main_bot.send_message(chat_id, f"🧪 حي: {len(alive)}/{len(proxies)}")
            return

        if c.data == "owner_del_proxy":
            if not is_owner(uid): return
            main_bot.answer_callback_query(c.id)
            msg = main_bot.send_message(chat_id, "أرسل البروكسي للحذف:")
            main_bot.register_next_step_handler(msg, step_owner_del_proxy)
            return

        # GiftCode callbacks
        if c.data == "gc_panel":
            main_bot.answer_callback_query(c.id)
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("🥇 متصدرون الذهب", callback_data="gc_top_gold"),
                InlineKeyboardButton("💎 متصدرون الألماس", callback_data="gc_top_diamond"),
                InlineKeyboardButton("📋 آخر المهام", callback_data="gc_tasks"),
                InlineKeyboardButton("🏅 المتصدرون بالإحالة", callback_data="gc_top_refs"))
            if is_owner(uid):
                kb.add(InlineKeyboardButton("🛒 آخر المشتريات", callback_data="gc_purchases"),
                       InlineKeyboardButton("🎯 مسح الإحالات", callback_data="gc_harvest"))
            kb.add(InlineKeyboardButton("🛍️ القناة", url=SHOWCASE_URL),
                   InlineKeyboardButton("🔙 رجوع", callback_data="start"))
            gc_send(chat_id, "🎯 <b>لوحة GiftCode</b>\nاختر من القائمة:", kb)
            return

        if c.data == "gc_top_gold":
            main_bot.answer_callback_query(c.id, "⏳ جلب...")
            try:
                gc_send(chat_id, render_gold(get_gc().fetch_leaders(), 50))
            except Exception as e:
                gc_send(chat_id, f"❌ فشل الجلب: {_esc(e)}")
            return

        if c.data == "gc_top_diamond":
            main_bot.answer_callback_query(c.id, "⏳ جلب...")
            try:
                gc_send(chat_id, render_diamond(get_gc().fetch_leaders(), 50))
            except Exception as e:
                gc_send(chat_id, f"❌ فشل الجلب: {_esc(e)}")
            return

        if c.data == "gc_tasks":
            main_bot.answer_callback_query(c.id, "⏳ جلب...")
            try:
                gc_send(chat_id, render_tasks(get_gc().fetch_leaders(), 20))
            except Exception as e:
                gc_send(chat_id, f"❌ فشل الجلب: {_esc(e)}")
            return

        if c.data == "gc_purchases":
            if not is_owner(uid):
                main_bot.answer_callback_query(c.id, "للمالك فقط", show_alert=True)
                return
            main_bot.answer_callback_query(c.id, "⏳ جلب...")
            try:
                gc_send(chat_id, render_purchases(get_gc().fetch_leaders(),
                                                  is_owner=True, top=20))
            except Exception as e:
                gc_send(chat_id, f"❌ فشل الجلب: {_esc(e)}")
            return

        if c.data == "gc_top_refs":
            main_bot.answer_callback_query(c.id)
            gc_send(chat_id, render_top_refs(10))
            return

        if c.data == "gc_harvest":
            if not is_owner(uid):
                main_bot.answer_callback_query(c.id, "للمالك فقط", show_alert=True)
                return
            if _gc_lock.locked():
                main_bot.answer_callback_query(c.id, "⏳ مسح آخر يعمل", show_alert=True)
                return
            main_bot.answer_callback_query(c.id, "بدأ المسح...")

            def run_harvest():
                with _gc_lock:
                    try:
                        def cb(i, total, ok, gold):
                            if i % 5 == 0 or i == total:
                                try:
                                    main_bot.send_message(chat_id,
                                        f"⏳ {i}/{total} | ✅ {ok} | 💰 {gold}")
                                except Exception:
                                    pass
                        res = get_gc().harvest(progress_cb=cb, delay=None)
                        if res.get("reason"):
                            main_bot.send_message(chat_id, f"❌ {_esc(res['reason'])}")
                            return
                        main_bot.send_message(chat_id,
                            f"✅ انتهى المسح\n"
                            f"✅ نجح: {res['ok']}\n"
                            f"🚫 IP محد: {res['ip_limit']}\n"
                            f"🔁 مُحال: {res['already']}\n"
                            f"❌ غير صالح: {res['invalid']}\n"
                            f"⏭️ متجاوز: {res['skipped_seen']}\n"
                            f"💰 Gold: {res['gold']}")
                        audit(uid, "gc_harvest", f"ok={res['ok']},gold={res['gold']}")
                    except Exception as e:
                        try:
                            main_bot.send_message(chat_id, f"❌ خطأ: {_esc(e)}")
                        except Exception:
                            pass

            threading.Thread(target=run_harvest, daemon=True).start()
            return

        if c.data == "start":
            main_bot.answer_callback_query(c.id)
            main_menu(uid, chat_id)
            return

        main_bot.answer_callback_query(c.id)

    except Exception as e:
        log.error(f"main cb err: {e}")
        try:
            main_bot.answer_callback_query(c.id, f"⚠️ {e}", show_alert=True)
        except Exception:
            pass


# ═══════════════════════ Features ═══════════════════════
def do_daily(uid, chat_id):
    u = get_user(uid)
    if not u: return
    last = u["last_daily"]
    streak = u["streak"]
    now = datetime.now()
    last_dt = None
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
        except Exception:
            last_dt = None
        if last_dt and last_dt.date() == now.date():
            main_bot.send_message(chat_id, "⏰ استلمتها اليوم")
            return
    streak = streak + 1 if last_dt and (now - last_dt).days == 1 else 1
    reward = DAILY_BASE + min(streak, DAILY_STREAK_MAX) * DAILY_STREAK_BONUS
    set_user(uid, last_daily=now.isoformat(), streak=streak)
    add_points(uid, reward)
    main_bot.send_message(chat_id, f"🎁 +{reward} نقطة\n🔥 يوم {streak}")


def do_leaderboard(chat_id):
    rows = db_exec("""SELECT u.user_id, u.first_name, u.username, u.points,
        (SELECT COUNT(*) FROM history h
         WHERE h.user_id=u.user_id AND h.result='success') s
        FROM users u WHERE u.banned=0 ORDER BY s DESC LIMIT 10""", fetch="all")
    if not rows:
        main_bot.send_message(chat_id, "لا يوجد")
        return
    lines = ["🏆 <b>المتصدرون — أفضل 10</b>",
             "━━━━━━━━━━━━━━━━━━━━"]
    for i, r in enumerate(rows, 1):
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}.".rjust(3)
        name = r["first_name"] or "?"
        if r["username"]:
            name += f"  (@{r['username']})"
        lines.append(f"{medal}  {html.escape(name)[:30]}")
        lines.append(f"      ✅ {r['s']} | 💎 {r['points']}")
        lines.append("")
    try:
        main_bot.send_message(chat_id, "\n".join(lines).rstrip(), parse_mode="HTML")
    except Exception:
        main_bot.send_message(chat_id, "\n".join(lines).rstrip())


def do_stats(uid, chat_id):
    s = history_stats(uid)
    total = s["success"] + s["failed"] + s["already"]
    rate = (s["success"] / total * 100) if total else 0
    u = get_user(uid)
    main_bot.send_message(chat_id,
        f"📊 إحصائياتك\n━━━━━━━━━━━\n"
        f"✅ {s['success']}\n❌ {s['failed']}\n🔁 {s['already']}\n"
        f"📈 {rate:.1f}%\n💎 {u['points']}\n🔥 Streak: {u['streak']}")


def do_owner_stats(chat_id):
    rows = db_exec("SELECT user_id, first_name, username, points FROM users "
                   "ORDER BY points DESC", fetch="all")
    if not rows:
        main_bot.send_message(chat_id, "لا يوجد")
        return
    header = (f"📈 مستخدمون: {len(rows)}\n"
              f"💎 {sum(r['points'] for r in rows)}\n\n")
    body = ""
    for i, r in enumerate(rows[:50], 1):
        s = history_stats(r["user_id"])
        name = r["first_name"] or "?"
        if r["username"]:
            name += f" (@{r['username']})"
        body += (f"{i}. {name}\n 🆔 {r['user_id']} "
                 f"💎{r['points']} ✅{s['success']}\n")
    main_bot.send_message(chat_id, (header + body)[:4000])


# ═══════════════════════ Steps ═══════════════════════
def step_set_referral(m):
    if _cancel.pop(m.from_user.id, None): return
    set_user(m.from_user.id, referral_code=m.text.strip())
    main_bot.reply_to(m, f"✅ {m.text.strip()}")
    main_menu(m.from_user.id, m.chat.id)


def step_set_start(m):
    if _cancel.pop(m.from_user.id, None): return
    if m.text.strip().isdigit():
        set_user(m.from_user.id, start_number=int(m.text.strip()))
        main_bot.reply_to(m, f"✅ {m.text.strip()}")
    else:
        main_bot.reply_to(m, "❌ أرقام فقط")
    main_menu(m.from_user.id, m.chat.id)


def step_add_proxy(m):
    if _cancel.pop(m.from_user.id, None): return
    if add_proxy(m.text.strip(), m.from_user.id):
        main_bot.reply_to(m, "✅ تمت")
        try:
            main_bot.send_message(OWNER_ID,
                f"🔐 بروكسي من {m.from_user.id}: {m.text.strip()}")
        except Exception:
            pass
    else:
        main_bot.reply_to(m, "موجود")


def step_owner_add_proxy(m):
    if add_proxy(m.text.strip(), 0):
        main_bot.reply_to(m, "✅")
    else:
        main_bot.reply_to(m, "موجود")


def step_owner_del_proxy(m):
    del_proxy(m.text.strip())
    main_bot.reply_to(m, "✅")


def step_edit_points(m):
    p = m.text.split()
    if len(p) != 2:
        main_bot.reply_to(m, "صيغة خاطئة")
        return
    uid = find_uid_by_input(p[0])
    try:
        pts = int(p[1])
    except ValueError:
        main_bot.reply_to(m, "أرقام فقط")
        return
    if uid:
        set_user(uid, points=pts)
        main_bot.reply_to(m, f"✅ {uid} = {pts}")


def step_broadcast(m):
    text = m.text.strip()
    if not text: return
    users = db_exec("SELECT user_id FROM users WHERE banned=0", fetch="all")
    sent = failed = 0
    for row in users:
        try:
            main_bot.send_message(row["user_id"], f"📢 إعلان\n\n{text}")
            sent += 1
            time.sleep(0.05)
        except Exception:
            failed += 1
    main_bot.reply_to(m, f"✅ {sent} | ❌ {failed}")


# ═══════════════════════ SHOWCASE handlers ═══════════════════════
@showcase_bot.message_handler(commands=["start"])
def showcase_cmd_start(m):
    if is_owner(m.from_user.id):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("📡 نشر الآن", callback_data="feed_now"),
            InlineKeyboardButton("📊 حالة البوت", callback_data="status"),
            InlineKeyboardButton("🎯 تعيين القناة", callback_data="set_channel_hint"),
            InlineKeyboardButton("⏯️ تشغيل/إيقاف", callback_data="toggle_feed"),
        )
        showcase_bot.reply_to(m,
            "👑 <b>لوحة تحكم بوت العرض</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 القناة: <code>{showcase_load_channel() or 'غير معينة'}</code>\n"
            f"⏯️ التغذية: {'✅ تعمل' if showcase_feed_enabled['on'] else '⏹️ متوقفة'}\n"
            f"⏱️ الفحص كل {SHOWCASE_POLL_INTERVAL} ثانية",
            reply_markup=kb)
        return
    channel = showcase_load_channel()
    text = ("🎯 <b>هذا بوت عرض فقط</b>\n\n"
            "يعرض مشتريات GiftCode الجديدة تلقائياً.\n"
            "لا يمكنك التحكم به من هنا.")
    kb = InlineKeyboardMarkup()
    if channel and str(channel).startswith("@"):
        kb.add(InlineKeyboardButton(
            "📢 شاهد القناة", url=f"https://t.me/{channel.lstrip('@')}"))
    showcase_bot.reply_to(m, text, reply_markup=kb)


@showcase_bot.message_handler(commands=["set_channel"])
def showcase_cmd_set_channel(m):
    if not is_owner(m.from_user.id): return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        showcase_bot.reply_to(m, "الاستخدام: /set_channel -1001234567890")
        return
    ch = parts[1].strip()
    try:
        chat = showcase_bot.get_chat(ch)
        showcase_bot.send_message(ch, "🔧 <b>تم ربط بوت العرض بهذه القناة.</b>",
                                  parse_mode="HTML")
    except Exception as e:
        showcase_bot.reply_to(m, f"❌ فشل الوصول للقناة: {e}\n"
                                  f"تأكد أن البوت أدمن فيها.")
        return
    set_showcase_cfg("channel_id", ch)
    showcase_bot.reply_to(m, f"✅ تم تعيين القناة:\n{chat.title}\n<code>{ch}</code>")


@showcase_bot.message_handler(commands=["feed_on"])
def showcase_cmd_feed_on(m):
    if not is_owner(m.from_user.id): return
    showcase_feed_enabled["on"] = True
    showcase_bot.reply_to(m, "✅ التغذية تعمل")


@showcase_bot.message_handler(commands=["feed_off"])
def showcase_cmd_feed_off(m):
    if not is_owner(m.from_user.id): return
    showcase_feed_enabled["on"] = False
    showcase_bot.reply_to(m, "⏹️ التغذية متوقفة")


@showcase_bot.message_handler(commands=["feed_now"])
def showcase_cmd_feed_now(m):
    if not is_owner(m.from_user.id): return
    showcase_bot.reply_to(m, "⏳ جاري الفحص والنشر...")
    threading.Thread(target=lambda: showcase_feed_now(m.chat.id), daemon=True).start()


@showcase_bot.message_handler(commands=["stats"])
def showcase_cmd_stats(m):
    if not is_owner(m.from_user.id): return
    total = db_exec("SELECT COUNT(*) c FROM showcase_seen", fetch="one")["c"]
    showcase_bot.reply_to(m, f"📊 مشتريات مُنشَرة: {total}")


@showcase_bot.message_handler(commands=["clear_seen"])
def showcase_cmd_clear_seen(m):
    if not is_owner(m.from_user.id): return
    db_exec("DELETE FROM showcase_seen")
    showcase_bot.reply_to(m, "🗑️ تم مسح السجل.")


@showcase_bot.callback_query_handler(func=lambda c: True)
def showcase_on_cb(c):
    if not is_owner(c.from_user.id):
        showcase_bot.answer_callback_query(c.id, "🔒 مغلق", show_alert=True)
        return
    if c.data == "feed_now":
        showcase_bot.answer_callback_query(c.id, "⏳")
        threading.Thread(target=lambda: showcase_feed_now(c.message.chat.id),
                         daemon=True).start()
    elif c.data == "status":
        channel = showcase_load_channel() or "غير معينة"
        total = db_exec("SELECT COUNT(*) c FROM showcase_seen", fetch="one")["c"]
        showcase_bot.answer_callback_query(c.id)
        showcase_bot.send_message(c.message.chat.id,
            f"📊 <b>حالة البوت</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 القناة: <code>{channel}</code>\n"
            f"⏯️ التغذية: {'✅' if showcase_feed_enabled['on'] else '⏹️'}\n"
            f"📦 مُنشَرة: {total}\n"
            f"⏱️ الفحص: {SHOWCASE_POLL_INTERVAL}s")
    elif c.data == "toggle_feed":
        showcase_feed_enabled["on"] = not showcase_feed_enabled["on"]
        showcase_bot.answer_callback_query(c.id,
            f"{'✅ تعمل' if showcase_feed_enabled['on'] else '⏹️ متوقفة'}",
            show_alert=True)
    elif c.data == "set_channel_hint":
        showcase_bot.answer_callback_query(c.id)
        showcase_bot.send_message(c.message.chat.id,
            "أرسل:\n<code>/set_channel -1001234567890</code>\n"
            "أو\n<code>/set_channel @mychannel</code>\n\n"
            "⚠️ يجب أن يكون البوت أدمن في القناة.")
    else:
        showcase_bot.answer_callback_query(c.id)


# ═══════════════════════ Polling threads ═══════════════════════
def poll_main():
    while True:
        try:
            main_bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            log.error(f"main polling: {e}")
            time.sleep(5)


def poll_showcase():
    while True:
        try:
            showcase_bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            log.error(f"showcase polling: {e}")
            time.sleep(5)


# ═══════════════════════ Flask ═══════════════════════
@app.route("/")
def flask_root():
    n = db_exec("SELECT COUNT(*) c FROM users", fetch="one")["c"]
    return {"status": "ok", "users": n}


@app.route("/health")
def flask_health():
    return {"status": "ok"}


# ═══════════════════════ MAIN ═══════════════════════
if __name__ == "__main__":
    _init_db()
    gh_pull_user_data()           # من GitHub (إن مُفعّل)
    migrate_legacy()
    u, p = reimport_sessions(verbose=True)
    log.info(f"Dual bot starting | GC: {'OK' if GC_TOKEN else 'MISSING'} | "
             f"GH: {'OK' if GITHUB_TOKEN else 'off'} | +{u}u +{p}p")

    for label, b in (("main", main_bot), ("showcase", showcase_bot)):
        try:
            b.delete_webhook(drop_pending_updates=True)
            log.info(f"{label}: webhook cleared")
        except Exception as e:
            log.warning(f"{label} delete_webhook: {e}")

    threading.Thread(target=live_purchase_feed, daemon=True).start()
    threading.Thread(target=auto_harvest_loop, daemon=True).start()
    threading.Thread(target=showcase_feed_loop, daemon=True).start()
    threading.Thread(target=store_loop, daemon=True).start()
    threading.Thread(target=gh_sync_loop, daemon=True).start()
    threading.Thread(target=poll_main, daemon=True).start()
    threading.Thread(target=poll_showcase, daemon=True).start()

    log.info(f"Flask on 0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
