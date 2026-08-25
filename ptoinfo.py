#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                     🔍 OSINT LOOKUP BOT - PRO EDITION                        ║
║                     Version 2.0 | Professional Build                         ║
║                                                                              ║
║  Features: Phone Lookup | Vehicle RC | Basic Info | Credit System | Search   ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import json
import logging
import os
import re
import asyncio
import random
from datetime import datetime
from typing import Dict, Optional, Tuple, Any

import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from telegram.constants import ParseMode
from telethon import TelegramClient, events

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

from dotenv import load_dotenv
load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH")
    USER_PHONE = os.getenv("USER_PHONE")
    SESSION_FILE = os.getenv("SESSION_FILE", "user_session")
    BACKEND_BOT = os.getenv("BACKEND_BOT", "@UkraineToOsint_bot")

    MALE_OWNERS = [
        x.strip() for x in os.getenv("MALE_OWNERS", "").split(",") if x.strip()
    ]

    FEMALE_OWNER = os.getenv("FEMALE_OWNER", "Bhumidedha6")
    FEMALE_OWNER_ID = None
    # Credit System
    FREE_CREDITS = 10
    CREDITS_FILE = "user_credits.json"
    LOGS_FILE = "bot_logs.json"
    

    # Animation Settings
    TYPING_DELAY = 0.03
    PROGRESS_STEPS = ["⏳", "🔄", "⚡", "🔍", "📡", "🌐"]

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("OSINTBot")

# ═══════════════════════════════════════════════════════════════════════════════
# DATA MANAGER (Credits, Logs, Users)
# ═══════════════════════════════════════════════════════════════════════════════

class DataManager:
    """Persistent storage for credits, logs, and user data"""

    def __init__(self):
        self.credits_file = Config.CREDITS_FILE
        self.logs_file = Config.LOGS_FILE
        self._credits: Dict[str, Any] = {}
        self._logs: Dict[str, Any] = {"searches": [], "users": {}}
        self._load_all()

    def _load_all(self):
        if os.path.exists(self.credits_file):
            try:
                with open(self.credits_file, 'r', encoding='utf-8') as f:
                    self._credits = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load credits: {e}")
        if os.path.exists(self.logs_file):
            try:
                with open(self.logs_file, 'r', encoding='utf-8') as f:
                    self._logs = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load logs: {e}")

    def _save_credits(self):
        try:
            with open(self.credits_file, 'w', encoding='utf-8') as f:
                json.dump(self._credits, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save credits: {e}")

    def _save_logs(self):
        try:
            with open(self.logs_file, 'w', encoding='utf-8') as f:
                json.dump(self._logs, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save logs: {e}")

    def get_credits(self, user_id: int, username: str = None) -> int:
        uid = str(user_id)
        if uid not in self._credits:
            self._credits[uid] = {
                "credits": Config.FREE_CREDITS,
                "username": username or "Unknown",
                "first_used": datetime.now().isoformat(),
                "total_used": 0
            }
            self._save_credits()
            return self._credits[uid].get("credits", Config.FREE_CREDITS)
          
    def deduct_credit(self, user_id: int) -> bool:
        if self.is_owner(user_id):
            return True
        uid = str(user_id)
        current = self.get_credits(user_id)
        if current <= 0:
            return False
        self._credits[uid]["credits"] -= 1
        self._credits[uid]["total_used"] += 1
        self._save_credits()
        return True

    def add_credits(self, user_id: int, amount: int):
        uid = str(user_id)
        if uid not in self._credits:
            self._credits[uid] = {
                "credits": amount,
                "username": "Unknown",
                "first_used": datetime.now().isoformat(),
                "total_used": 0
            }
        else:
            self._credits[uid]["credits"] += amount
        self._save_credits()

    def is_owner(self, user_id: int, username: str = None) -> bool:
        if username:
            uname = username.lower().replace("@", "")
            if uname in Config.MALE_OWNERS:
                self._credits[str(user_id)] = self._credits.get(str(user_id), {})
                self._credits[str(user_id)]["is_owner"] = True
                self._save_credits()
                return True
            if uname == Config.FEMALE_OWNER.lower():
                Config.FEMALE_OWNER_ID = user_id
                self._credits[str(user_id)] = self._credits.get(str(user_id), {})
                self._credits[str(user_id)]["is_owner"] = True
                self._save_credits()
                return True
        if Config.FEMALE_OWNER_ID and user_id == Config.FEMALE_OWNER_ID:
            return True
        if str(user_id) in self._credits and self._credits[str(user_id)].get("is_owner"):
            return True
        return False

    def log_search(self, user_id: int, query_type: str, query: str, status: str):
        self._logs["searches"].append({
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "type": query_type,
            "query": query,
            "status": status
        })
        uid = str(user_id)
        if uid not in self._logs["users"]:
            self._logs["users"][uid] = {"search_count": 0, "types": {}}
        self._logs["users"][uid]["search_count"] += 1
        self._logs["users"][uid]["types"][query_type] = \
            self._logs["users"][uid]["types"].get(query_type, 0) + 1
        if len(self._logs["searches"]) > 1000:
            self._logs["searches"] = self._logs["searches"][-1000:]
        self._save_logs()

    def get_stats(self) -> dict:
        return {
            "total_users": len(self._credits),
            "total_searches": len(self._logs["searches"]),
            "active_today": sum(1 for s in self._logs["searches"]
                              if datetime.fromisoformat(s["timestamp"]).date() == datetime.now().date())
        }

db = DataManager()

# ═══════════════════════════════════════════════════════════════════════════════
# ANIMATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class Anim:
    """Cool animation effects for the bot"""

    @staticmethod
    async def typing(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
        """Typewriter effect"""
        msg = await update.effective_message.reply_text("▫️")
        displayed = ""
        for char in text:
            displayed += char
            if len(displayed) % 3 == 0 or char in "\n.!?:":
                try:
                    await msg.edit_text(displayed + "▮")
                    await asyncio.sleep(Config.TYPING_DELAY)
                except:
                    pass
        try:
            await msg.edit_text(text)
        except:
            pass
        return msg

    @staticmethod
    async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       steps: int = 5, duration: float = 2.0):
        """Animated progress bar"""
        msg = await update.effective_message.reply_text("⏳ Initializing...")
        step_time = duration / steps
        for i in range(steps + 1):
            filled = "█" * i
            empty = "░" * (steps - i)
            percent = int((i / steps) * 100)
            emoji = Config.PROGRESS_STEPS[i % len(Config.PROGRESS_STEPS)]
            text = f"{emoji} Processing...\n`[{filled}{empty}] {percent}%`"
            try:
                await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
            except:
                pass
            await asyncio.sleep(step_time)
        return msg

    @staticmethod
    async def spinner(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      text: str = "Loading", duration: float = 1.5):
        """Loading spinner"""
        spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        msg = await update.effective_message.reply_text(f"{spinners[0]} {text}...")
        start = asyncio.get_event_loop().time()
        idx = 0
        while asyncio.get_event_loop().time() - start < duration:
            try:
                await msg.edit_text(f"{spinners[idx % len(spinners)]} {text}...")
            except:
                pass
            idx += 1
            await asyncio.sleep(0.1)
        return msg

    @staticmethod
    async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE,
                   target: str, duration: float = 2.0):
        """Scanning animation"""
        frames = [
            f"🔍 Scanning: `{target}`\n`[░░░░░░░░░░]`",
            f"🔍 Scanning: `{target}`\n`[██░░░░░░░░]`",
            f"🔍 Scanning: `{target}`\n`[████░░░░░░]`",
            f"🔍 Scanning: `{target}`\n`[██████░░░░]`",
            f"🔍 Scanning: `{target}`\n`[████████░░]`",
            f"🔍 Scanning: `{target}`\n`[██████████]`",
        ]
        msg = await update.effective_message.reply_text(frames[0], parse_mode=ParseMode.MARKDOWN)
        for frame in frames[1:]:
            await asyncio.sleep(duration / len(frames))
            try:
                await msg.edit_text(frame, parse_mode=ParseMode.MARKDOWN)
            except:
                pass
        return msg

    @staticmethod
    async def hack(update: Update, context: ContextTypes.DEFAULT_TYPE, duration: float = 2.0):
        """Matrix-style hacking animation"""
        chars = "0123456789ABCDEF"
        msg = await update.effective_message.reply_text("Initializing hack sequence...")
        for _ in range(int(duration * 5)):
            line = "".join(random.choice(chars) for _ in range(20))
            try:
                await msg.edit_text(f"```\n{line}\nDecrypting data...\n```", parse_mode=ParseMode.MARKDOWN)
            except:
                pass
            await asyncio.sleep(0.2)
        return msg

# ═══════════════════════════════════════════════════════════════════════════════
# VEHICLE FETCHER
# ═══════════════════════════════════════════════════════════════════════════════

class VehicleFetcher:
    """Fetches vehicle RC details from carinfo.app"""

    @staticmethod
    def fetch(veh_num: str) -> Tuple[bool, str]:
        try:
            veh_num = veh_num.upper().strip()
            url = f"https://www.carinfo.app/rc-details/{veh_num}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            details = {}

            np = soup.find("div", class_=re.compile("numberPlateContainer"))
            if np:
                p = np.find("p")
                if p:
                    details["Number Plate"] = p.text.strip()

            mm = soup.find("div", class_=re.compile("vehicalDetails"))
            if mm:
                p = mm.find("p", class_=re.compile("vehicalModel"))
                if p:
                    details["Make & Model"] = p.text.strip()

            owner = soup.find("div", class_=re.compile("ownerDetails"))
            if owner:
                p = owner.find("p", class_=re.compile("ownerName"))
                if p:
                    details["Owner Name"] = p.text.strip()

            rto = soup.find("div", class_=re.compile("detailListContainer"))
            if rto:
                for item in rto.find_all("div", class_=re.compile("detailItem")):
                    kt = item.find("p", class_=re.compile("itemText"))
                    vt = item.find("p", class_=re.compile("itemSubTitle"))
                    if kt and vt:
                        details[kt.text.strip()] = vt.text.strip()
                wt = rto.find("a", href=True)
                if wt:
                    details["Website"] = wt['href']

            output = f"""╔══════════════════════════════════════════════════════════════╗
║           🚗 VEHICLE RC DETAILS - PRO LOOKUP                 ║
╠══════════════════════════════════════════════════════════════╣
║  📋 Number Plate : `{details.get('Number Plate', 'N/A')}`
║  🏭 Make & Model : {details.get('Make & Model', 'N/A')}
║  👤 Owner Name   : {details.get('Owner Name', 'N/A')}
╠══════════════════════════════════════════════════════════════╣
║  🏢 RTO INFORMATION                                          ║
╠══════════════════════════════════════════════════════════════╣
║  📌 Number       : {details.get('Number', 'N/A')}
║  🏛️  Registered RTO: {details.get('Registered RTO', 'N/A')}
║  🗺️  State        : {details.get('State', 'N/A')}
║  📞 Phone        : {details.get('RTO Phone number', 'N/A')}
║  🌐 Website      : {details.get('Website', 'N/A')}
╚══════════════════════════════════════════════════════════════╝
✅ Data fetched successfully from CarInfo.app"""
            return True, output
        except requests.exceptions.RequestException as e:
            return False, f"❌ Network error: Unable to reach RC details site.\n\n`Error: {str(e)}`"
        except Exception as e:
            return False, f"❌ An error occurred: `{str(e)}`"

# ═══════════════════════════════════════════════════════════════════════════════
# BASIC INFO FETCHER (Lightweight - no massive dict)
# ═══════════════════════════════════════════════════════════════════════════════

class BasicInfoFetcher:
    """Lightweight phone number analyzer"""

    @staticmethod
    def analyze(phone: str) -> str:
        phone = phone.strip()
        if phone.startswith("+91"):
            phone = phone[3:]
        elif phone.startswith("91") and len(phone) == 12:
            phone = phone[2:]
        if not phone.isdigit() or len(phone) != 10:
            return "❌ Invalid phone number. Please provide a 10-digit Indian number."

        formatted = f"+91 {phone[:5]} {phone[5:]}"

        # Simple series-based detection (first 4 digits)
        series = phone[:4]
        carrier = BasicInfoFetcher._detect_carrier(series)

        info = f"""╔══════════════════════════════════════════════════════════════╗
║           📱 BASIC PHONE ANALYSIS REPORT                     ║
╠══════════════════════════════════════════════════════════════╣
║  📞 Number       : `{formatted}`
║  🔢 Raw          : `{phone}`
║  🏢 Carrier      : {carrier['name']}
║  🗺️  Circle       : {carrier['circle']}
║  📡 Type         : {carrier['type']}
╠══════════════════════════════════════════════════════════════╣
║  🔍 NUMBER PATTERN ANALYSIS                                  ║
╠══════════════════════════════════════════════════════════════╣
║  • Series Code (First 4)   : {series}
║  • MSC Code pattern        : {phone[4:6]}
║  • Subscriber Number       : {phone[6:]}
║  • Number Length           : {len(phone)} digits
╚══════════════════════════════════════════════════════════════╝
💡 Use /phone for deep OSINT lookup via backend bot"""
        return info

    @staticmethod
    def _detect_carrier(series: str) -> dict:
        """Simple carrier detection based on series prefix"""
        # First digit based detection
        first_digit = series[0]

        jio_prefixes = set("6789")
        airtel_prefixes = set("789")
        vi_prefixes = set("89")
        bsnl_prefixes = set("6")

        # Simple heuristic
        s = int(series)
        if 6000 <= s <= 6999 or 7200 <= s <= 7499 or 8000 <= s <= 8999 or 9000 <= s <= 9999:
            if s >= 6200 and s <= 6299:
                return {"name": "Reliance Jio", "circle": "Bihar & Jharkhand", "type": "4G/LTE"}
            elif s >= 7000 and s <= 7099:
                return {"name": "Reliance Jio", "circle": "Odisha/WB", "type": "4G/LTE"}
            elif s >= 8000 and s <= 8099:
                return {"name": "Reliance Jio", "circle": "Andhra Pradesh", "type": "4G/LTE"}
            elif s >= 9000 and s <= 9099:
                return {"name": "Reliance Jio", "circle": "Tamil Nadu", "type": "4G/LTE"}
            elif s >= 7300 and s <= 7399:
                return {"name": "Reliance Jio", "circle": "Maharashtra", "type": "4G/LTE"}
            elif s >= 7400 and s <= 7499:
                return {"name": "Reliance Jio", "circle": "Gujarat", "type": "4G/LTE"}
            elif s >= 7600 and s <= 7699:
                return {"name": "Reliance Jio", "circle": "Rajasthan", "type": "4G/LTE"}
            elif s >= 8100 and s <= 8199:
                return {"name": "Reliance Jio", "circle": "Karnataka/Punjab", "type": "4G/LTE"}
            elif s >= 8300 and s <= 8399:
                return {"name": "Reliance Jio", "circle": "West Bengal", "type": "4G/LTE"}
            elif s >= 9100 and s <= 9199:
                return {"name": "Reliance Jio", "circle": "Uttar Pradesh", "type": "4G/LTE"}
            else:
                return {"name": "Reliance Jio (Detected)", "circle": "India", "type": "4G/LTE"}

        # Airtel detection
        if series.startswith("7") or series.startswith("8") or series.startswith("9"):
            if series.startswith("98") or series.startswith("99"):
                return {"name": "Bharti Airtel", "circle": "India", "type": "4G/5G"}
            elif series.startswith("80") or series.startswith("81"):
                return {"name": "Bharti Airtel", "circle": "South India", "type": "4G/5G"}

        # VI detection
        if series.startswith("89") or series.startswith("98"):
            return {"name": "Vodafone Idea (VI)", "circle": "India", "type": "4G"}

        return {"name": "Unknown Carrier", "circle": "India", "type": "Unknown"}

# ═══════════════════════════════════════════════════════════════════════════════
# TELETHON BACKEND INTEGRATION (Original functionality preserved)
# ═══════════════════════════════════════════════════════════════════════════════

user_client = TelegramClient(Config.SESSION_FILE, Config.API_ID, Config.API_HASH)
app_ref = None
backend_to_bot_map = {}

def extract_json(text: str) -> str:
    """Extract top-level JSON object from text"""
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start != -1:
                candidate = text[start:i+1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    continue
    return text

def has_json(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return True
    return False

# ═══════════════════════════════════════════════════════════════════════════════
# KEYBOARD BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def main_menu_keyboard(is_owner: bool = False) -> InlineKeyboardMarkup:
    """Build main menu keyboard"""
    buttons = [
        [InlineKeyboardButton("📱 Phone OSINT Lookup", callback_data="menu_phone")],
        [InlineKeyboardButton("🚗 Vehicle RC Lookup", callback_data="menu_vehicle")],
        [InlineKeyboardButton("📊 Basic Phone Info", callback_data="menu_basic")],
        [InlineKeyboardButton("🔍 Search History", callback_data="menu_history")],
        [InlineKeyboardButton("💳 My Credits", callback_data="menu_credits")],
        [InlineKeyboardButton("📈 Bot Stats", callback_data="menu_stats")],
    ]
    if is_owner:
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="menu_admin")])
    return InlineKeyboardMarkup(buttons)

def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back to Menu", callback_data="menu_main")]])

# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    user_id = user.id
    username = user.username

    is_owner = db.is_owner(user_id, username)

    # Special welcome for female owner
    if username and username.lower().replace("@", "") == Config.FEMALE_OWNER.lower():
        welcome_text = f"""✨ *Welcome back, Queen!* ✨

👑 Hey {user.first_name or 'Beautiful'},

💜 You are the *Special Female Owner* of this bot.
🌸 All features are *unlimited* for you.
🔮 You have *divine access* to everything.

This bot is yours to command. 💕

*What would you like to do today?*"""
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN,
                                       reply_markup=main_menu_keyboard(is_owner=True))
        return

    # Normal welcome
    credits = db.get_credits(user_id, username)
    credit_text = "♾️ Unlimited" if is_owner else f"{credits} / {Config.FREE_CREDITS}"

    welcome_text = f"""🎯 *Welcome to OSINT Pro Bot!*

Hello {user.first_name or 'there'}! 👋

I'm your personal OSINT lookup assistant with powerful features:

📱 *Phone OSINT* — Deep number lookup via backend bot
🚗 *Vehicle RC* — Fetch vehicle registration details  
📊 *Basic Info* — Quick phone number analysis
🔍 *Search History* — Track your lookups

💳 *Your Credits:* `{credit_text}`

*Select an option below or send a 10-digit number directly!*"""
try:
    photo_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "Bot.png"
    )

    with open(photo_path, "rb") as photo:
        await update.message.reply_photo(
            photo=photo,
            caption=welcome_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(is_owner)
        )

except Exception as e:
    logger.warning(f"Could not send Bot.png: {e}")

    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(is_owner)
    )
    

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = """📚 *OSINT Pro Bot - Help Guide*

*Commands:*
`/start` — Main menu & welcome
`/help` — This help message
`/phone <number>` — Deep OSINT phone lookup
`/vehicle <number>` — Vehicle RC lookup
`/basic <number>` — Basic phone info
`/credits` — Check your credits
`/stats` — Bot statistics

*How to use:*
1️⃣ Send a 10-digit Indian number directly for phone lookup
2️⃣ Use buttons in the menu for specific features
3️⃣ Each lookup costs 1 credit (except for owners)

*Owners:*
👨‍💼 @ankneewayz @d4rxuv

*Note:* Normal users get 10 free credits. Contact owners for more."""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN,
                                   reply_markup=back_button())

async def credits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /credits command"""
    user_id = update.effective_user.id
    username = update.effective_user.username
    is_owner = db.is_owner(user_id, username)

    if is_owner:
        text = "💎 *Your Status:* `OWNER`\n♾️ *Credits:* `Unlimited`\n✨ You have divine access!"
    else:
        credits = db.get_credits(user_id, username)
        text = f"💳 *Your Credits:* `{credits}` / `{Config.FREE_CREDITS}`\n\n💡 Each lookup costs 1 credit.\n📞 Contact owners for more credits."

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    stats = db.get_stats()
    text = f"""📊 *Bot Statistics*

👥 Total Users: `{stats['total_users']}`
🔍 Total Searches: `{stats['total_searches']}`
📈 Active Today: `{stats['active_today']}`

⚡ Bot Status: `Online & Running`
🔄 `"""
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())

async def basic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /basic command"""
    if not context.args:
        await update.message.reply_text(
            "❌ *Usage:* `/basic <10-digit-number>`\n\nExample: `/basic 6205923286`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_button()
        )
        return

    phone = context.args[0].strip()
    user_id = update.effective_user.id
    username = update.effective_user.username

    # Basic info is FREE - no credit deduction
    await Anim.scan(update, context, phone[:4] + "XXXX" + phone[-2:], duration=1.5)
    result = BasicInfoFetcher.analyze(phone)
    db.log_search(user_id, "basic", phone, "success")
    await update.message.reply_text(result, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())

async def vehicle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /vehicle command"""
    if not context.args:
        await update.message.reply_text(
            "❌ *Usage:* `/vehicle <vehicle-number>`\n\nExample: `/vehicle MH12AB1234`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_button()
        )
        return

    veh_num = " ".join(context.args).strip().upper()
    user_id = update.effective_user.id
    username = update.effective_user.username

    # Check credits
    if not db.deduct_credit(user_id):
        await update.message.reply_text(
            "❌ *Out of Credits!*\n\nYou have used all your free lookups.\n📞 Contact owners for more credits.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_button()
        )
        return

    # Show animation
    progress_msg = await Anim.progress(update, context, steps=6, duration=2.5)

    success, result = VehicleFetcher.fetch(veh_num)

    try:
        await progress_msg.delete()
    except:
        pass

    status = "success" if success else "failed"
    db.log_search(user_id, "vehicle", veh_num, status)

    credits_left = "♾️" if db.is_owner(user_id, username) else db.get_credits(user_id, username)
    footer = f"\n\n💳 Credits Left: `{credits_left}`"

    await update.message.reply_text(result + footer, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())

async def phone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /phone command - Deep OSINT via backend bot"""
    if not context.args:
        await update.message.reply_text(
            "❌ *Usage:* `/phone <10-digit-number>`\n\nExample: `/phone 6205923286`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_button()
        )
        return

    text = context.args[0].strip()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username

    if not text.isdigit() or len(text) != 10:
        await update.message.reply_text(
            "❌ Send a valid **10-digit Indian phone number** (without +91).\n\nExample: `6205923286`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_button()
        )
        return

    # Check credits
    if not db.deduct_credit(user_id):
        await update.message.reply_text(
            "❌ *Out of Credits!*\n\nYou have used all your free lookups.\n📞 Contact owners for more credits.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_button()
        )
        return

    # Show scanning animation
    scan_msg = await Anim.scan(update, context, text[:4] + "XXXX" + text[-2:], duration=2.0)
    status_msg = await update.message.reply_text("🔍 Connecting to backend OSINT engine...")

    try:
        async with user_client.conversation(Config.BACKEND_BOT, timeout=60) as conv:
            # Step 1: Send "Number to Info" to get menu
            await conv.send_message("Number to Info")
            menu_reply = await conv.get_response()
            menu_text = menu_reply.text or menu_reply.message or ""
            if has_json(menu_text):
                menu_text = extract_json(menu_text)

            sent_menu = await context.bot.send_message(chat_id, menu_text)
            backend_to_bot_map[menu_reply.id] = (chat_id, sent_menu.message_id)

            # Step 2: Send the number
            await conv.send_message(text)
            result_reply = await conv.get_response()
            result_text = result_reply.text or result_reply.message or ""
            if has_json(result_text):
                result_text = extract_json(result_text)

            sent_result = await context.bot.send_message(chat_id, result_text)
            backend_to_bot_map[result_reply.id] = (chat_id, sent_result.message_id)

        try:
            await scan_msg.delete()
            await status_msg.delete()
        except:
            pass

        db.log_search(user_id, "phone_osint", text, "success")

        credits_left = "♾️" if db.is_owner(user_id, username) else db.get_credits(user_id, username)
        await context.bot.send_message(
            chat_id,
            f"💳 Credits Left: `{credits_left}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button()
        )

    except TimeoutError:
        try:
            await scan_msg.delete()
            await status_msg.edit_text("❌ Backend bot timed out. Try again.")
        except:
            pass
        db.log_search(user_id, "phone_osint", text, "timeout")
    except Exception as e:
        logger.error("Error in phone lookup: %s", e)
        try:
            await scan_msg.delete()
            await status_msg.edit_text("❌ Failed. Service may be down.")
        except:
            pass
        db.log_search(user_id, "phone_osint", text, f"error: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACK QUERY HANDLER (Button clicks)
# ═══════════════════════════════════════════════════════════════════════════════

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses"""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    user_id = user.id
    username = user.username
    is_owner = db.is_owner(user_id, username)

    data = query.data

    if data == "menu_main":
        credits = db.get_credits(user_id, username)
        credit_text = "♾️ Unlimited" if is_owner else f"{credits} / {Config.FREE_CREDITS}"
        text = f"""🎯 *OSINT Pro Bot - Main Menu*

Hello {user.first_name or 'there'}! 👋

💳 *Your Credits:* `{credit_text}`

*Select an option below:*"""
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                     reply_markup=main_menu_keyboard(is_owner))

    elif data == "menu_phone":
        text = """📱 *Phone OSINT Lookup*

This performs a *deep OSINT search* via our backend bot.

*How to use:*
• Send a 10-digit number directly
• Or use: `/phone 6205923286`

*Cost:* 1 credit per lookup
*Note:* Results may include name, location, and more."""
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())

    elif data == "menu_vehicle":
        text = """🚗 *Vehicle RC Lookup*

Fetch vehicle registration details from CarInfo.app

*How to use:*
• Use: `/vehicle MH12AB1234`
• Or send vehicle number directly with /vehicle prefix

*Cost:* 1 credit per lookup
*Info:* Make, Model, Owner, RTO details"""
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())

    elif data == "menu_basic":
        text = """📊 *Basic Phone Info*

Quick analysis of any Indian phone number:
• Carrier detection
• Circle/State info
• Number pattern analysis

*How to use:*
• Use: `/basic 6205923286`

*Cost:* FREE (no credits deducted!)"""
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())

    elif data == "menu_history":
        uid = str(user_id)
        if uid in db._logs["users"]:
            udata = db._logs["users"][uid]
            types_text = "\n".join([f"  • {k}: `{v}`" for k, v in udata["types"].items()])
            text = f"""🔍 *Your Search History*

📊 Total Searches: `{udata['search_count']}`

*Breakdown:*
{types_text}

💡 Use /stats for global bot stats."""
        else:
            text = "🔍 *No searches yet!*\n\nStart using the bot to see your history here."
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())

    elif data == "menu_credits":
        if is_owner:
            text = "💎 *Your Status:* `OWNER`\n♾️ *Credits:* `Unlimited`\n✨ You have divine access!"
        else:
            credits = db.get_credits(user_id, username)
            text = f"💳 *Your Credits:* `{credits}` / `{Config.FREE_CREDITS}`\n\n💡 Each lookup costs 1 credit.\n📞 Contact owners for more credits."
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())

    elif data == "menu_stats":
        stats = db.get_stats()
        text = f"""📊 *Bot Statistics*

👥 Total Users: `{stats['total_users']}`
🔍 Total Searches: `{stats['total_searches']}`
📈 Active Today: `{stats['active_today']}`

⚡ Bot Status: `Online`
🔄 Backend: `{Config.BACKEND_BOT}`"""
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())

    elif data == "menu_admin" and is_owner:
        text = f"""⚙️ *Admin Panel*

Welcome, *{user.first_name or 'Owner'}*!

*Owner Commands:*
`/addcredits <user_id> <amount>` — Add credits to user
`/broadcast <message>` — Send message to all users
`/ban <user_id>` — Ban a user

*Bot Owners:*
👨‍💼 @ankneewayz @d4rxuv
👩‍💼 @Bhumidedha6 (Special)"""
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())

# ═══════════════════════════════════════════════════════════════════════════════
# DIRECT MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle direct text messages (10-digit numbers)"""
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username

    # Check if it's a vehicle number format (has letters)
    if any(c.isalpha() for c in text) and len(text) >= 5:
        # Treat as vehicle number
        if not db.deduct_credit(user_id):
            await update.message.reply_text(
                "❌ *Out of Credits!*\n\nYou have used all your free lookups.\n📞 Contact owners for more credits.",
                parse_mode=ParseMode.MARKDOWN, reply_markup=back_button()
            )
            return

        progress_msg = await Anim.progress(update, context, steps=6, duration=2.5)
        success, result = VehicleFetcher.fetch(text)
        try:
            await progress_msg.delete()
        except:
            pass

        db.log_search(user_id, "vehicle", text, "success" if success else "failed")
        credits_left = "♾️" if db.is_owner(user_id, username) else db.get_credits(user_id, username)
        await update.message.reply_text(result + f"\n\n💳 Credits Left: `{credits_left}`",
                                       parse_mode=ParseMode.MARKDOWN, reply_markup=back_button())
        return

    # Check if it's a 10-digit phone number
    if not text.isdigit() or len(text) != 10:
        await update.message.reply_text(
            "❌ Please send a valid **10-digit Indian phone number** (without +91)\n\n"
            "Or use the menu buttons below:",
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard(db.is_owner(user_id, username))
        )
        return

    # It's a phone number - do deep OSINT lookup
    if not db.deduct_credit(user_id):
        await update.message.reply_text(
            "❌ *Out of Credits!*\n\nYou have used all your free lookups.\n📞 Contact owners for more credits.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_button()
        )
        return

    scan_msg = await Anim.scan(update, context, text[:4] + "XXXX" + text[-2:], duration=2.0)
    status_msg = await update.message.reply_text("🔍 Connecting to backend OSINT engine...")

    try:
        async with user_client.conversation(Config.BACKEND_BOT, timeout=60) as conv:
            await conv.send_message("Number to Info")
            menu_reply = await conv.get_response()
            menu_text = menu_reply.text or menu_reply.message or ""
            if has_json(menu_text):
                menu_text = extract_json(menu_text)

            sent_menu = await context.bot.send_message(chat_id, menu_text)
            backend_to_bot_map[menu_reply.id] = (chat_id, sent_menu.message_id)

            await conv.send_message(text)
            result_reply = await conv.get_response()
            result_text = result_reply.text or result_reply.message or ""
            if has_json(result_text):
                result_text = extract_json(result_text)

            sent_result = await context.bot.send_message(chat_id, result_text)
            backend_to_bot_map[result_reply.id] = (chat_id, sent_result.message_id)

        try:
            await scan_msg.delete()
            await status_msg.delete()
        except:
            pass

        db.log_search(user_id, "phone_osint", text, "success")

        credits_left = "♾️" if db.is_owner(user_id, username) else db.get_credits(user_id, username)
        await context.bot.send_message(
            chat_id,
            f"💳 Credits Left: `{credits_left}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button()
        )

    except TimeoutError:
        try:
            await scan_msg.delete()
            await status_msg.edit_text("❌ Backend bot timed out. Try again.")
        except:
            pass
        db.log_search(user_id, "phone_osint", text, "timeout")
    except Exception as e:
        logger.error("Error: %s", e)
        try:
            await scan_msg.delete()
            await status_msg.edit_text("❌ Failed. Service may be down.")
        except:
            pass
        db.log_search(user_id, "phone_osint", text, f"error: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# OWNER COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def addcredits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Add credits to a user"""
    user_id = update.effective_user.id
    username = update.effective_user.username

    if not db.is_owner(user_id, username):
        await update.message.reply_text("❌ *Access Denied!*\nYou are not authorized.", parse_mode=ParseMode.MARKDOWN)
        return

    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: `/addcredits <user_id> <amount>`", parse_mode=ParseMode.MARKDOWN)
        return

    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
        db.add_credits(target_id, amount)
        await update.message.reply_text(f"✅ Added `{amount}` credits to user `{target_id}`", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID or amount.", parse_mode=ParseMode.MARKDOWN)

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Broadcast message to all users"""
    user_id = update.effective_user.id
    username = update.effective_user.username

    if not db.is_owner(user_id, username):
        await update.message.reply_text("❌ *Access Denied!*", parse_mode=ParseMode.MARKDOWN)
        return

    if not context.args:
        await update.message.reply_text("❌ Usage: `/broadcast <message>`", parse_mode=ParseMode.MARKDOWN)
        return

    message = " ".join(context.args)
    count = 0
    for uid in db._credits:
        try:
            await context.bot.send_message(int(uid), f"📢 *Broadcast:*\n\n{message}", parse_mode=ParseMode.MARKDOWN)
            count += 1
        except:
            pass

    await update.message.reply_text(f"✅ Broadcast sent to `{count}` users.", parse_mode=ParseMode.MARKDOWN)

# ═══════════════════════════════════════════════════════════════════════════════
# TELETHON EDIT HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

async def start_telethon():
    """Start Telethon and register edit handler"""
    await user_client.start(phone=Config.USER_PHONE)
    me = await user_client.get_me()
    logger.info("✅ Telethon logged in as %s", me.first_name)

    @user_client.on(events.MessageEdited(from_users=Config.BACKEND_BOT))
    async def handle_backend_edit(event):
        backend_msg_id = event.message.id
        if backend_msg_id not in backend_to_bot_map:
            return

        chat_id, bot_msg_id = backend_to_bot_map[backend_msg_id]
        new_text = event.message.text or event.message.message or ""

        if has_json(new_text):
            new_text = extract_json(new_text)

        try:
            await app_ref.bot.edit_message_text(
                chat_id=chat_id,
                message_id=bot_msg_id,
                text=new_text,
            )
            logger.info("✏️ Edited bot msg %d (backend msg %d)", bot_msg_id, backend_msg_id)
        except Exception as e:
            logger.warning("Could not edit message: %s", e)

async def post_init(app):
    global app_ref
    app_ref = app
    await start_telethon()

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║   ██████╗ ███████╗██╗███╗   ██╗████████╗    ██████╗ ██████╗  ██████╗        ║
║   ██╔═══██╗██╔════╝██║████╗  ██║╚══██╔══╝    ██╔══██╗██╔══██╗██╔═══██╗       ║
║   ██║   ██║███████╗██║██╔██╗ ██║   ██║       ██████╔╝██████╔╝██║   ██║       ║
║   ██║   ██║╚════██║██║██║╚██╗██║   ██║       ██╔═══╝ ██╔══██╗██║   ██║       ║
║   ╚██████╔╝███████║██║██║ ╚████║   ██║       ██║     ██║  ██║╚██████╔╝       ║
║    ╚═════╝ ╚══════╝╚═╝╚═╝  ╚═══╝   ╚═╝       ╚═╝     ╚═╝  ╚═╝ ╚═════╝        ║
║                                                                              ║
║                    OSINT LOOKUP BOT v2.0 - PRO EDITION                       ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """)

    builder = Application.builder().token(Config.BOT_TOKEN).post_init(post_init).build()

    # Command handlers
    builder.add_handler(CommandHandler("start", start_cmd))
    builder.add_handler(CommandHandler("help", help_cmd))
    builder.add_handler(CommandHandler("phone", phone_cmd))
    builder.add_handler(CommandHandler("vehicle", vehicle_cmd))
    builder.add_handler(CommandHandler("basic", basic_cmd))
    builder.add_handler(CommandHandler("credits", credits_cmd))
    builder.add_handler(CommandHandler("stats", stats_cmd))
    builder.add_handler(CommandHandler("addcredits", addcredits_cmd))
    builder.add_handler(CommandHandler("broadcast", broadcast_cmd))

    # Callback query handler
    builder.add_handler(CallbackQueryHandler(button_handler))

    # Message handler
    builder.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🚀 Starting OSINT Pro Bot...")
    builder.run_polling(allowed_updates=Update.ALL_TYPES)
