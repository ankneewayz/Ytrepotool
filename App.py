#!/usr/bin/env python3
"""
OSINT LOOKUP — WEB EDITION
Frontend: Website (Flask) | Backend: same Telethon relay to OSINT backend bot
"""

import json
import os
import re
import asyncio
import threading
import logging
from datetime import datetime
from typing import Dict, Tuple

import requests
from bs4 import BeautifulSoup
from flask import (Flask, render_template, request, jsonify,
                   session as flask_session, redirect, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from telethon import TelegramClient, events

from dotenv import load_dotenv
load_dotenv()

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "")
    USER_PHONE = os.getenv("USER_PHONE")
    SESSION_FILE = os.getenv("SESSION_FILE", "user_session")
    BACKEND_BOT = os.getenv("BACKEND_BOT", "@UkraineToOsint_bot")

    MALE_OWNERS = [x.strip().lower() for x in os.getenv("MALE_OWNERS", "").split(",") if x.strip()]
    FEMALE_OWNER = os.getenv("FEMALE_OWNER", "Bhumidedha6")

    FREE_CREDITS = 10
    CREDITS_FILE = "user_credits.json"
    LOGS_FILE = "bot_logs.json"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("OSINTWeb")

# ═══════════════════════════════════════════════════════════
# DATA MANAGER (same as bot)
# ═══════════════════════════════════════════════════════════

class DataManager:
    def __init__(self):
        self._credits: Dict = {}
        self._logs: Dict = {"searches": [], "users": {}}
        self._load_all()

    def _load_all(self):
        for attr, path, default in [
            ("_credits", Config.CREDITS_FILE, {}),
            ("_logs", Config.LOGS_FILE, {"searches": [], "users": {}})]:
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        setattr(self, attr, json.load(f))
                except Exception as e:
                    logger.error("Load %s failed: %s", path, e)
        # reuse bot's files if present
        self._logs.setdefault("searches", [])
        self._logs.setdefault("users", {})

    def _save(self, path, data):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error("Save %s failed: %s", path, e)

    def save_all(self):
        self._save(Config.CREDITS_FILE, self._credits)
        self._save(Config.LOGS_FILE, self._logs)

    def _user(self, uid, username=None):
        uid = str(uid)
        if uid not in self._credits:
            self._credits[uid] = {
                "credits": Config.FREE_CREDITS,
                "username": username or "Unknown",
                "first_used": datetime.now().isoformat(),
                "total_used": 0,
            }
            self.save_all()
        return uid

    def get_credits(self, uid):
        return self._credits[self._user(uid)].get("credits", 0)

    def deduct_credit(self, uid):
        uid = self._user(uid)
        if self.is_owner(uid):
            return True
        if self._credits[uid]["credits"] <= 0:
            return False
        self._credits[uid]["credits"] -= 1
        self._credits[uid]["total_used"] += 1
        self.save_all()
        return True

    def add_credits(self, uid, amount):
        uid = self._user(uid)
        self._credits[uid]["credits"] += amount
        self.save_all()

    def is_owner(self, uid, username=None):
        uid = str(uid)
        if username and username.lower().replace("@", "") == Config.FEMALE_OWNER.lower():
            self._credits.setdefault(uid, {})["is_owner"] = True
            self.save_all()
            return True
        return self._credits.get(uid, {}).get("is_owner", False)

    def log_search(self, uid, qtype, query, status):
        self._logs["searches"].append({
            "timestamp": datetime.now().isoformat(),
            "user_id": uid, "type": qtype, "query": query, "status": status,
        })
        u = self._logs["users"].setdefault(str(uid), {"search_count": 0, "types": {}})
        u["search_count"] += 1
        u["types"][qtype] = u["types"].get(qtype, 0) + 1
        if len(self._logs["searches"]) > 1000:
            self._logs["searches"] = self._logs["searches"][-1000:]
        self.save_all()

    def get_stats(self):
        today = datetime.now().date()
        return {
            "total_users": len(self._credits),
            "total_searches": len(self._logs["searches"]),
            "active_today": sum(
                1 for s in self._logs["searches"]
                if datetime.fromisoformat(s["timestamp"]).date() == today),
        }

db = DataManager()

# ═══════════════════════════════════════════════════════════
# TELETHON BRIDGE (backend engine — same as bot)
# ═══════════════════════════════════════════════════════════

user_client = TelegramClient(Config.SESSION_FILE, Config.API_ID, Config.API_HASH)
telethon_loop: asyncio.AbstractEventLoop = None
telethon_ready = threading.Event()

def _extract_json(text: str) -> str:
    depth, start = 0, -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                cand = text[start:i + 1]
                try:
                    json.loads(cand)
                    return cand
                except json.JSONDecodeError:
                    continue
    return text

def _has_json(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return True
    return False

async def telethon_main():
    await user_client.start(phone=Config.USER_PHONE)
    me = await user_client.get_me()
    logger.info("✅ Telethon logged in as %s", me.first_name)
    telethon_ready.set()
    await user_client.run_until_disconnected()

async def backend_phone_lookup(phone: str) -> str:
    """Relay query to the backend bot via Telethon, return final response text."""
    async with user_client.conversation(Config.BACKEND_BOT, timeout=60) as conv:
        await conv.send_message("Number to Info")
        menu_reply = await conv.get_response()
        menu_text = menu_reply.text or menu_reply.message or ""
        if _has_json(menu_text):
            menu_text = _extract_json(menu_text)

        await conv.send_message(phone)
        result_reply = await conv.get_response()
        result_text = result_reply.text or result_reply.message or ""
        if _has_json(result_text):
            result_text = _extract_json(result_text)

    return f"{menu_text}\n\n{'─' * 40}\n\n{result_text}" if result_text else menu_text

def run_backend_lookup(phone: str) -> str:
    """Blocking call from Flask thread into Telethon loop."""
    if not telethon_ready.wait(timeout=30):
        raise RuntimeError("Telethon backend not connected")
    fut = asyncio.run_coroutine_threadsafe(
        backend_phone_lookup(phone), telethon_loop)
    return fut.result(timeout=70)

# ═══════════════════════════════════════════════════════════
# VEHICLE FETCHER (unchanged logic)
# ═══════════════════════════════════════════════════════════

class VehicleFetcher:
    @staticmethod
    def fetch(veh_num: str) -> Tuple[bool, Dict]:
        try:
            veh_num = veh_num.upper().strip()
            url = f"https://www.carinfo.app/rc-details/{veh_num}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            }
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            d = {}

            np = soup.find("div", class_=re.compile("numberPlateContainer"))
            if np and (p := np.find("p")):
                d["Number Plate"] = p.text.strip()
            mm = soup.find("div", class_=re.compile("vehicalDetails"))
            if mm and (p := mm.find("p", class_=re.compile("vehicalModel"))):
                d["Make & Model"] = p.text.strip()
            owner = soup.find("div", class_=re.compile("ownerDetails"))
            if owner and (p := owner.find("p", class_=re.compile("ownerName"))):
                d["Owner Name"] = p.text.strip()
            rto = soup.find("div", class_=re.compile("detailListContainer"))
            if rto:
                for item in rto.find_all("div", class_=re.compile("detailItem")):
                    kt = item.find("p", class_=re.compile("itemText"))
                    vt = item.find("p", class_=re.compile("itemSubTitle"))
                    if kt and vt:
                        d[kt.text.strip()] = vt.text.strip()
                wt = rto.find("a", href=True)
                if wt:
                    d["Website"] = wt["href"]
            if not d:
                return False, {"error": "No data found for this vehicle number."}
            return True, d
        except requests.exceptions.RequestException as e:
            return False, {"error": f"Network error: {e}"}
        except Exception as e:
            return False, {"error": str(e)}

# ═══════════════════════════════════════════════════════════
# BASIC INFO (unchanged logic)
# ═══════════════════════════════════════════════════════════

class BasicInfoFetcher:
    @staticmethod
    def _detect_carrier(series: str) -> dict:
        s = int(series)
        if series.startswith("6") and not (6200 <= s <= 6299):
            return {"name": "BSNL", "circle": "India", "type": "3G/4G"}
        if 6200 <= s <= 6299:
            return {"name": "Reliance Jio", "circle": "Bihar & Jharkhand", "type": "4G/LTE"}
        if series.startswith("98") or series.startswith("99"):
            return {"name": "Bharti Airtel", "circle": "India", "type": "4G/5G"}
        if series.startswith("89"):
            return {"name": "Vodafone Idea (VI)", "circle": "India", "type": "4G"}
        if 7000 <= s <= 7099: return {"name": "Reliance Jio", "circle": "Odisha/WB", "type": "4G/LTE"}
        if 8000 <= s <= 8099: return {"name": "Reliance Jio", "circle": "Andhra Pradesh", "type": "4G/LTE"}
        if 9000 <= s <= 9099: return {"name": "Reliance Jio", "circle": "Tamil Nadu", "type": "4G/LTE"}
        if 7300 <= s <= 7399: return {"name": "Reliance Jio", "circle": "Maharashtra", "type": "4G/LTE"}
        if 7400 <= s <= 7499: return {"name": "Reliance Jio", "circle": "Gujarat", "type": "4G/LTE"}
        if 7600 <= s <= 7699: return {"name": "Reliance Jio", "circle": "Rajasthan", "type": "4G/LTE"}
        if 8100 <= s <= 8199: return {"name": "Reliance Jio", "circle": "Karnataka/Punjab", "type": "4G/LTE"}
        if 8300 <= s <= 8399: return {"name": "Reliance Jio", "circle": "West Bengal", "type": "4G/LTE"}
        if 9100 <= s <= 9199: return {"name": "Reliance Jio", "circle": "Uttar Pradesh", "type": "4G/LTE"}
        if 6000 <= s <= 6999 or 7200 <= s <= 7499 or 8000 <= s <= 8999 or 9000 <= s <= 9999:
            return {"name": "Reliance Jio (Detected)", "circle": "India", "type": "4G/LTE"}
        return {"name": "Unknown Carrier", "circle": "India", "type": "Unknown"}

    @staticmethod
    def analyze(phone: str) -> Dict:
        phone = phone.strip()
        if phone.startswith("+91"): phone = phone[3:]
        elif phone.startswith("91") and len(phone) == 12: phone = phone[2:]
        if not phone.isdigit() or len(phone) != 10:
            return {"error": "Invalid phone number. Provide a 10-digit Indian number."}
        series = phone[:4]
        carrier = BasicInfoFetcher._detect_carrier(series)
        return {
            "number": f"+91 {phone[:5]} {phone[5:]}",
            "raw": phone,
            "carrier": carrier["name"],
            "circle": carrier["circle"],
            "type": carrier["type"],
            "series_code": series,
            "msc_code": phone[4:6],
            "subscriber": phone[6:],
        }

# ═══════════════════════════════════════════════════════════
# FLASK APP
# ═══════════════════════════════════════════════════════════

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

VEH_RE = re.compile(r'^[A-Z]{2}\d{2}[A-Z]{1,2}\d{1,4}$')

def current_uid() -> str:
    """Identify users by session cookie (anonymous web users)."""
    if "uid" not in flask_session:
        flask_session["uid"] = f"web-{os.urandom(8).hex()}"
    return flask_session["uid"]

@app.route("/")
def index():
    uid = current_uid()
    credits = "Unlimited" if db.is_owner(uid) else db.get_credits(uid)
    return render_template("index.html", credits=credits, free=Config.FREE_CREDITS)

@app.route("/api/lookup", methods=["POST"])
def lookup():
    uid = current_uid()
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    mode = data.get("mode", "auto")  # auto | phone | vehicle | basic

    if not query:
        return jsonify({"error": "Empty query"}), 400

    # Determine type
    qtype = mode
    if mode == "auto":
        if VEH_RE.match(query.upper()):
            qtype = "vehicle"
        elif query.isdigit() and len(query) == 10:
            qtype = "phone"
        else:
            return jsonify({"error": "Send a 10-digit Indian number or a vehicle number "
                                     "(e.g. MH12AB1234)."}), 400

    # BASIC = free
    if qtype == "basic":
        result = BasicInfoFetcher.analyze(query)
        db.log_search(uid, "basic", query, "error" if "error" in result else "success")
        return jsonify({"type": "basic", "result": result, "credits_left": db.get_credits(uid)})

    # PHONE (deep OSINT via backend bot) + VEHICLE cost 1 credit
    if not db.deduct_credit(uid):
        return jsonify({"error": "Out of credits. Contact the owner.", "out_of_credits": True}), 403

    if qtype == "vehicle":
        ok, result = VehicleFetcher.fetch(query)
        db.log_search(uid, "vehicle", query, "success" if ok else "failed")
        return jsonify({"type": "vehicle", "result": result,
                        "credits_left": db.get_credits(uid)})

    if qtype == "phone":
        try:
            result_text = run_backend_lookup(query)
            db.log_search(uid, "phone_osint", query, "success")
            return jsonify({"type": "phone", "result": {"text": result_text},
                            "credits_left": db.get_credits(uid)})
        except Exception as e:
            logger.error("Phone lookup failed: %s", e)
            db.log_search(uid, "phone_osint", query, f"error: {e}")
            return jsonify({"error": "Backend OSINT engine timed out or is down. "
                                     "Credit refunded."}), 502

    return jsonify({"error": "Unknown mode"}), 400

@app.route("/api/stats")
def stats():
    return jsonify(db.get_stats())

@app.route("/api/credits")
def credits():
    uid = current_uid()
    return jsonify({
        "credits": "Unlimited" if db.is_owner(uid) else db.get_credits(uid),
        "free": Config.FREE_CREDITS,
    })

# ── Admin API (owners) ──────────────────────────────────────
ADMIN_KEY = os.getenv("ADMIN_KEY", "supersecret-admin")

@app.route("/api/admin/addcredits", methods=["POST"])
def admin_addcredits():
    if request.headers.get("X-Admin-Key") != ADMIN_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    d = request.get_json() or {}
    try:
        db.add_credits(str(d["user_id"]), int(d["amount"]))
        return jsonify({"ok": True})
    except (KeyError, ValueError):
        return jsonify({"error": "user_id (str) and amount (int) required"}), 400

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def start_telethon_thread():
    global telethon_loop
    telethon_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(telethon_loop)
    telethon_loop.run_until_complete(telethon_main())

if __name__ == "__main__":
    threading.Thread(target=start_telethon_thread, daemon=True).start()
    print("""
╔══════════════════════════════════════════════════╗
║   OSINT LOOKUP BOT v2.0 — WEB EDITION            ║
║   Backend: Telethon → {} 
╚══════════════════════════════════════════════════╝
    """.format(Config.BACKEND_BOT))
    app.run(host="0.0.0.0", port=5000, debug=False)
