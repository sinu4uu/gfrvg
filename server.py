import sys
import os
import re
import io
import json
import zipfile
import time

import html
import random
import string
import base64
import secrets
import asyncio
import threading
from typing import Dict, Any, Optional
from collections import defaultdict
from contextlib import asynccontextmanager
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

# Polyfill missing imghdr for Python 3.14+
try:
    import imghdr
except ImportError:
    import types
    imghdr_mock = types.ModuleType("imghdr")
    def what(file, h=None):
        if h is None:
            with open(file, "rb") as f:
                h = f.read(32)
        if h.startswith(b"\xff\xd8"):
            return "jpeg"
        if h.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if h.startswith(b"GIF87a") or h.startswith(b"GIF89a"):
            return "gif"
        if h.startswith(b"RIFF") and h[8:12] == b"WEBP":
            return "webp"
        return None
    imghdr_mock.what = what
    sys.modules["imghdr"] = imghdr_mock

# Polyfill DummyJobQueue for Python 3.12+ (prevents apscheduler event loop crash)
import types
mock_jq_mod = types.ModuleType("telegram.ext._jobqueue")
class DummyJobQueue:
    def __init__(self, *args, **kwargs):
        pass
    def set_application(self, *args, **kwargs):
        pass
    async def start(self, *args, **kwargs):
        pass
    async def stop(self, *args, **kwargs):
        pass
    def __class_getitem__(cls, item):
        return cls

class DummyJob:
    def __init__(self, *args, **kwargs):
        pass
    def __class_getitem__(cls, item):
        return cls

mock_jq_mod.JobQueue = DummyJobQueue
mock_jq_mod.Job = DummyJob
sys.modules["telegram.ext._jobqueue"] = mock_jq_mod

import telegram
import telegram.error
from telegram.request import HTTPXRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, ForceReply
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters, ApplicationHandlerStop
from telegram.constants import ParseMode




import qrcode
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Directory definitions
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(WEB_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# Add web directory to path for local engine import
if WEB_DIR not in sys.path:
    sys.path.insert(0, WEB_DIR)

# Load web environment configuration (.env)
ROOT_DIR = os.path.dirname(WEB_DIR)
ROOT_ENV = os.path.join(ROOT_DIR, ".env")
if os.path.exists(ROOT_ENV):
    load_dotenv(ROOT_ENV)
ENV_FILE = os.path.join(WEB_DIR, ".env")
if os.path.exists(ENV_FILE):
    load_dotenv(ENV_FILE, override=True)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
raw_admin_ids = (os.environ.get("ADMIN_IDS") or os.environ.get("ADMIN_ID") or "").strip()
ADMIN_IDS = [int(x.strip()) for x in raw_admin_ids.split(",") if x.strip().isdigit()]
REQUIRED_CHANNELS = [c.strip() for c in os.environ.get("TELEGRAM_REQUIRED_CHANNELS", "@xdro1d,@xdroidchat").split(",") if c.strip()]

# Local TV engine
try:
    from tv_engine import *
except Exception as e:
    print(f"[!] Warning: Failed to import tv_engine: {e}")
    count_vault_cookies = lambda s: 0
    process_tv_login_service = lambda s, c: {"success": False, "error": "TV Engine initialization failed"}
    SUPPORTED_SERVICES = ["netflix", "crunchyroll", "hbomax", "surfshark", "spotify", "jiohotstar", "primevideo"]
    load_proxies = lambda: []
    get_raw_proxies = lambda: []
    save_proxies_to_file = lambda lines: False
    parse_proxy_line = lambda l: None
    get_proxy_country = lambda p: "UNKNOWN"
    get_service_proxies = lambda s: []
    COOKIES_DIR = os.path.join(os.path.dirname(WEB_DIR), "vault")


# ==============================================================================
# Isolated Web Database (web/db.json)
# ==============================================================================
class WebDB:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.lock = threading.Lock()
        self.data: Dict[str, Any] = {
            "users": {},
            "web_users": {},
            "orders": {},
            "redeemed_utrs": {},
            "payment_config": {
                "upi_id": "sudhirxd@fam",
                "payee_name": "Sudhir kumar"
            },
            "plans": {
                "monthly": {"name": "Monthly", "price": 30.0, "days": 30, "logins": 10, "enabled": True},
                "quarterly": {"name": "Quarterly", "price": 50.0, "days": 90, "logins": 35, "enabled": True},
                "yearly": {"name": "Yearly", "price": 321.0, "days": 365, "logins": 150, "enabled": True}
            }
        }
        self.load()

    def load(self):
        with self.lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        content = json.load(f)
                        if isinstance(content, dict):
                            self.data.update(content)
                    print(f"[*] Loaded web database from {self.filepath} ({len(self.data.get('users', {}))} users)")
                except Exception as e:
                    print(f"[!] Warning loading db.json: {e}")

    def save_unlocked(self):
        try:
            tmp = self.filepath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4)
            os.replace(tmp, self.filepath)
        except Exception as e:
            print(f"[!] Warning saving db.json: {e}")

    def save(self):
        with self.lock:
            self.save_unlocked()

    def get_user(self, user_key: Any) -> Optional[dict]:
        with self.lock:
            return self.data["users"].get(str(user_key))

    def register_user(self, user_id: Any, username: Optional[str] = None, first_name: str = "User") -> dict:
        with self.lock:
            key = str(user_id)
            if key not in self.data["users"]:
                self.data["users"][key] = {
                    "user_id": key,
                    "username": username or key,
                    "first_name": first_name,
                    "joined_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "free_trial_claimed": False,
                    "stats": {"successful_logins": 0, "failed_logins": 0},
                    "premium": {
                        "is_premium": False,
                        "plan": None,
                        "remaining_logins": 0,
                        "total_logins_granted": 0,
                        "expires_at": None
                    }
                }
                self.save_unlocked()
            return self.data["users"][key]

    def add_user_logins(self, user_id: Any, count: int) -> int:
        with self.lock:
            key = str(user_id)
            u = self.data["users"].setdefault(key, {
                "user_id": key,
                "joined_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "premium": {"is_premium": False, "remaining_logins": 0}
            })
            prem = u.setdefault("premium", {})
            cur = int(prem.get("remaining_logins", 0) or 0)
            prem["remaining_logins"] = cur + count
            prem["total_logins_granted"] = int(prem.get("total_logins_granted", 0) or 0) + count
            self.save_unlocked()
            return prem["remaining_logins"]

    def set_user_logins(self, user_id: Any, count: int) -> int:
        with self.lock:
            key = str(user_id)
            u = self.data["users"].setdefault(key, {
                "user_id": key,
                "joined_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "premium": {"is_premium": False, "remaining_logins": 0}
            })
            prem = u.setdefault("premium", {})
            prem["remaining_logins"] = max(0, count)
            self.save_unlocked()
            return prem["remaining_logins"]

    def get_user_premium(self, user_id: Any) -> dict:
        with self.lock:
            u = self.data["users"].get(str(user_id))
            if not u:
                return {"is_premium": False, "remaining_logins": 0}
            return u.get("premium", {"is_premium": False, "remaining_logins": 0})

    def activate_user_premium(self, user_id: Any, plan_key: str, duration_days: int = 30, logins: int = 10) -> dict:
        with self.lock:
            key = str(user_id)
            u = self.data["users"].setdefault(key, {
                "user_id": key,
                "joined_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "premium": {}
            })
            prem = u.setdefault("premium", {})
            cur_logins = int(prem.get("remaining_logins", 0) or 0)
            now = datetime.now()
            curr_exp = prem.get("expires_at")
            if curr_exp:
                try:
                    exp_dt = datetime.strptime(curr_exp, "%Y-%m-%d %H:%M:%S")
                    if exp_dt > now:
                        now = exp_dt
                except Exception:
                    pass
            new_exp = (now + timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M:%S")
            prem["is_premium"] = True
            prem["plan"] = plan_key
            prem["expires_at"] = new_exp
            prem["remaining_logins"] = cur_logins + logins
            prem["total_logins_granted"] = int(prem.get("total_logins_granted", 0) or 0) + logins
            prem["activated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.save_unlocked()
            return prem

    def remove_user_premium(self, user_id: Any) -> bool:
        with self.lock:
            key = str(user_id)
            u = self.data["users"].get(key)
            if u and "premium" in u:
                u["premium"] = {
                    "is_premium": False,
                    "plan": None,
                    "remaining_logins": 0,
                    "total_logins_granted": 0,
                    "expires_at": None
                }
                self.save_unlocked()
                return True
            return False

    def deduct_user_login(self, user_id: Any, service: str):
        with self.lock:
            key = str(user_id)
            u = self.data["users"].get(key)
            if u and "premium" in u:
                rem = int(u["premium"].get("remaining_logins", 0) or 0)
                u["premium"]["remaining_logins"] = max(0, rem - 1)
                self.save_unlocked()

    def record_attempt(self, user_id: Any, success: bool):
        with self.lock:
            key = str(user_id)
            u = self.data["users"].get(key)
            if u:
                stats = u.setdefault("stats", {"successful_logins": 0, "failed_logins": 0})
                if success:
                    stats["successful_logins"] = stats.get("successful_logins", 0) + 1
                else:
                    stats["failed_logins"] = stats.get("failed_logins", 0) + 1
                self.save_unlocked()

    def create_order(self, order_id: str, user_id: Any, email: str, plan_key: str, amount: float, logins: int, days: int):
        with self.lock:
            self.data["orders"][order_id] = {
                "order_id": order_id,
                "user_id": str(user_id),
                "email": email,
                "plan_key": plan_key,
                "amount": amount,
                "logins": logins,
                "days": days,
                "status": "pending",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            self.save_unlocked()

    def get_order(self, order_id: str) -> Optional[dict]:
        with self.lock:
            return self.data["orders"].get(order_id)

    def update_order_status(self, order_id: str, status: str, utr: Optional[str] = None):
        with self.lock:
            order = self.data["orders"].get(order_id)
            if order:
                order["status"] = status
                if utr:
                    order["utr"] = utr
                order["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self.save_unlocked()
                return order
            return None

    def record_redeemed_utr(self, utr: str, user_id: Any, order_id: str):
        with self.lock:
            self.data["redeemed_utrs"][utr.strip().upper()] = {
                "user_id": str(user_id),
                "order_id": order_id,
                "redeemed_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            self.save_unlocked()

    def is_utr_redeemed(self, utr: str) -> bool:
        with self.lock:
            return utr.strip().upper() in self.data["redeemed_utrs"]

    def get_payment_config(self) -> dict:
        with self.lock:
            return self.data.get("payment_config", {"upi_id": "sudhirxd@fam", "payee_name": "Sudhir kumar"})

    def get_premium_plans(self) -> dict:
        with self.lock:
            return self.data.get("plans", {
                "monthly": {"name": "Monthly", "price": 30.0, "days": 30, "logins": 10, "enabled": True},
                "quarterly": {"name": "Quarterly", "price": 50.0, "days": 90, "logins": 35, "enabled": True},
                "yearly": {"name": "Yearly", "price": 321.0, "days": 365, "logins": 150, "enabled": True}
            })

    def get_service_mode(self, service: str, default: str = "free") -> str:
        with self.lock:
            modes = self.data.setdefault("service_modes", {})
            return modes.get(service.lower().strip(), default)

    def set_service_mode(self, service: str, mode: str):
        with self.lock:
            modes = self.data.setdefault("service_modes", {})
            modes[service.lower().strip()] = mode.lower().strip()
            self.save_unlocked()

    def grant_free_service_pass(self, user_key: Any, services: list) -> dict:
        with self.lock:
            key = str(user_key)
            u = self.data["users"].setdefault(key, {
                "user_id": key,
                "joined_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "premium": {"is_premium": False, "remaining_logins": 0}
            })
            free_passes = u.setdefault("free_service_logins", {})
            for svc in services:
                svc_clean = str(svc).lower().strip()
                free_passes[svc_clean] = int(free_passes.get(svc_clean, 0) or 0) + 1
            self.save_unlocked()
            return free_passes

    def get_free_service_logins(self, user_key: Any) -> dict:
        with self.lock:
            u = self.data["users"].get(str(user_key))
            if not u:
                return {}
            return u.get("free_service_logins", {})

    def use_free_service_login(self, user_key: Any, service: str) -> bool:
        with self.lock:
            u = self.data["users"].get(str(user_key))
            if not u:
                return False
            passes = u.setdefault("free_service_logins", {})
            svc_clean = str(service).lower().strip()
            cur = int(passes.get(svc_clean, 0) or 0)
            if cur > 0:
                passes[svc_clean] = cur - 1
                self.save_unlocked()
                return True
            return False

    def save_ad_token(self, token: str, session_data: dict):
        with self.lock:
            tokens = self.data.setdefault("ad_tokens", {})
            tokens[token] = session_data
            self.save_unlocked()

    def get_ad_token(self, token: str) -> Optional[dict]:
        with self.lock:
            tokens = self.data.setdefault("ad_tokens", {})
            return tokens.get(token)

    def use_ad_token_login(self, token: str, service: str) -> bool:
        with self.lock:
            tokens = self.data.setdefault("ad_tokens", {})
            sess = tokens.get(token)
            if not sess:
                return False
            passes = sess.setdefault("services", {})
            svc_clean = str(service).lower().strip()
            cur = int(passes.get(svc_clean, 0) or 0)
            if cur > 0:
                passes[svc_clean] = cur - 1
                self.save_unlocked()
                return True
            return False

    def is_ads_gate_enabled(self) -> bool:
        with self.lock:
            settings = self.data.setdefault("settings", {})
            return settings.get("ads_gate_enabled", True)

    def set_ads_gate(self, enabled: bool):
        with self.lock:
            settings = self.data.setdefault("settings", {})
            settings["ads_gate_enabled"] = bool(enabled)
            self.save_unlocked()

    def toggle_ads_gate(self) -> bool:
        with self.lock:
            settings = self.data.setdefault("settings", {})
            cur = settings.get("ads_gate_enabled", True)
            settings["ads_gate_enabled"] = not cur
            self.save_unlocked()
            return settings["ads_gate_enabled"]


# Initialize DB targeting web/db.json
bot_db = WebDB(os.path.join(WEB_DIR, "db.json"))


# ==============================================================================
# Standalone Payment Verification & Helpers
# ==============================================================================
def generate_order_id() -> str:
    date_str = time.strftime("%Y%m%d")
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    suffix = "".join(secrets.choice(chars) for _ in range(6))
    return f"SX-{date_str}-{suffix}"


def generate_payment_qr(upi_id: str, payee_name: str, amount: float, order_id: str) -> io.BytesIO:
    encoded_pn = urllib.parse.quote(payee_name)
    encoded_tn = urllib.parse.quote(order_id)
    upi_uri = f"upi://pay?pa={upi_id}&pn={encoded_pn}&am={amount:.2f}&cu=INR&tn={encoded_tn}"

    qr = qrcode.QRCode(version=1, box_size=10, border=3)
    qr.add_data(upi_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    bio = io.BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


def verify_fampay_order_payment(order_id: str, expected_amount: float) -> tuple[bool, str, dict]:
    cleaned_order_id = order_id.strip()
    amt_str = str(int(expected_amount)) if expected_amount == int(expected_amount) else f"{expected_amount:.2f}"
    url = f"https://paymt-verify.vercel.app/api/fampay?order_id={urllib.parse.quote(cleaned_order_id)}&amount={amt_str}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            return False, f"Server HTTP {e.code}", {}
    except Exception as e:
        return False, f"Connection error: {e}", {}

    if data.get("verified") is True:
        results = data.get("results", [])
        detail = results[0] if results else {}
        return True, "Payment verified successfully.", detail

    msg = data.get("verification_message") or "Payment not found or not verified yet."
    return False, msg, data


def verify_fampay_payment(utr: str, expected_amount: float) -> tuple[bool, str, dict]:
    cleaned_utr = utr.strip().upper()
    url = f"https://paymt-verify.vercel.app/api/fampay?utr={urllib.parse.quote(cleaned_utr)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            return False, f"Verification service HTTP {e.code}", {}
    except Exception as e:
        return False, f"Verification service error: {e}", {}

    if data.get("verified") is True or data.get("found") is True:
        results = data.get("results", [])
        for r in results:
            amount_num = float(r.get("amount_num", 0) or 0)
            status = str(r.get("payment", "")).strip().lower()
            if (status == "success" or r.get("verified") is True) and amount_num >= expected_amount:
                return True, f"Payment verified! (₹{amount_num:.2f})", r
        if results and data.get("verified") is True:
            return True, "Payment verified successfully.", results[0]

    msg = data.get("verification_message") or "Payment not found. Ensure UTR is correct."
    return False, msg, data


# ==============================================================================
# Telegram Admin Bot (Runs in Background Daemon Thread)
# ==============================================================================
# Telegram Admin Bot (Runs in Background Daemon Thread - Strictly Admin Only)
# ==============================================================================
def is_admin(user_id) -> bool:
    if not user_id:
        return False
    if not ADMIN_IDS:
        return True  # If ADMIN_ID is not configured yet in env, permit operator rather than failing silently
    try:
        return int(user_id) in ADMIN_IDS or str(user_id) in [str(x) for x in ADMIN_IDS]
    except Exception:
        return False


def btn(text: str, callback_data: str = None, url: str = None, style: str = "primary", icon_custom_emoji_id: str = None) -> InlineKeyboardButton:
    kw = {"style": style}
    if icon_custom_emoji_id:
        kw["icon_custom_emoji_id"] = icon_custom_emoji_id
    if url:
        return InlineKeyboardButton(text=text, url=url, api_kwargs=kw)
    return InlineKeyboardButton(text=text, callback_data=callback_data, api_kwargs=kw)


def fmt_bot(text: str) -> str:
    """Format string with bold and italic tags for all Telegram bot output."""
    if not text:
        return text
    t = text.strip()
    if t.startswith("<b><i>") and t.endswith("</i></b>"):
        return t
    return f"<b><i>{t}</i></b>"


def get_admin_main_keyboard() -> InlineKeyboardMarkup:
    ads_on = bot_db.is_ads_gate_enabled()
    ads_btn_text = "📢 Ads Gate: ON 🟢" if ads_on else "📢 Ads Gate: OFF 🔴"
    ads_style = "success" if ads_on else "danger"

    keyboard = [
        [
            btn(text=ads_btn_text, callback_data="toggle_ads_gate", style=ads_style),
            btn(text="📊 Bot Statistics", callback_data="admin_stats", style="primary")
        ],
        [
            btn(text="📥 Upload Cookies", callback_data="admin_upload_prompt", style="success"),
            btn(text="👑 Premium Users List", callback_data="admin_premium_users_list", style="primary")
        ],
        [
            btn(text="🎁 Grant Subscription", callback_data="admin_grant_sub_prompt", style="success"),
            btn(text="💎 Premium Settings", callback_data="admin_premium_menu", style="primary")
        ],
        [
            btn(text="👥 Refer & Wallet", callback_data="admin_referral_menu", style="success"),
            btn(text="📢 Broadcast Message", callback_data="admin_broadcast_prompt", style="primary")
        ],
        [
            btn(text="🔌 Manage Proxies", callback_data="admin_proxies", style="success"),
            btn(text="🛠️ Settings Panel", callback_data="admin_settings", style="primary")
        ],
        [
            btn(text="💾 Backup DB", callback_data="admin_backup_db_now", style="success"),
            btn(text="🔄 Restore DB", callback_data="admin_restore_db_now", style="primary")
        ],
        [
            btn(text="🗑️ Delete Vault", callback_data="admin_delete_vault_menu", style="danger"),
            btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_admin_main_text(admin_name: str) -> str:
    with bot_db.lock:
        u_cnt = len(bot_db.data.get("users", {}))
        o_cnt = len(bot_db.data.get("orders", {}))
    ads_status = "ENABLED 🟢 (Tokens/Ads Required)" if bot_db.is_ads_gate_enabled() else "DISABLED 🔴 (Free Access / No Tokens Needed)"
    return (
        f"⚡ <b>XDROID TV · ADMIN CONTROL PANEL</b> ⚡\n"
        f"<i>» Master Terminal & Cloud Activation Core</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 <b>Welcome back, SuperAdmin {admin_name}!</b>\n"
        f"<i>Use the interactive buttons below to manage the platform in real-time.</i>\n\n"
        f"📢 <b>Ads Token Gate:</b> <code>{ads_status}</code>\n"
        f"👥 <b>Total Users:</b> <code>{u_cnt}</code>\n"
        f"📦 <b>Orders Logged:</b> <code>{o_cnt}</code>\n"
        f"🌐 <b>Web Portal:</b> <code>http://localhost:5000</code>\n"
        f"📁 <b>Isolated Database:</b> <code>web/db.json</code>\n"
        f"🔒 <b>Access Security:</b> <i>Admin Whitelist Active (Private)</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Tip: Tap any module button below to view details.</i>"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    if not is_admin(user.id):
        await update.message.reply_html(
            f"⚠️ <b>Access Restricted</b>\n\n"
            f"This bot is configured exclusively for administrators of the XDROID TV portal.\n"
            f"Your Telegram ID: <code>{user.id}</code>\n\n"
            f"<i>Set <code>ADMIN_ID={user.id}</code> in your Render environment variables to grant access.</i>"
        )
        return

    bot_db.register_user(user.id, username=user.username, first_name=user.first_name)
    bot_db.save()

    text = get_admin_main_text(user.first_name)
    keyboard = get_admin_main_keyboard()
    await update.message.reply_html(text, reply_markup=keyboard)



async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    with bot_db.lock:
        users = bot_db.data.get("users", {})
        total_users = len(users)
        premium_users = sum(1 for u in users.values() if u.get("premium", {}).get("is_premium"))
        orders = bot_db.data.get("orders", {})
        total_orders = len(orders)
        completed_orders = sum(1 for o in orders.values() if o.get("status") == "completed")
        total_revenue = sum(float(o.get("amount", 0)) for o in orders.values() if o.get("status") == "completed")

    total_cookies = sum(count_vault_cookies(s) for s in SUPPORTED_SERVICES)

    text = (
        f"📊 <b>SYSTEM & DATABASE METRICS</b>\n"
        f"<i>» Real-Time Telemetry & Transaction Analytics</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Total Registered Users:</b> <code>{total_users}</code>\n"
        f"👑 <b>Active Premium Members:</b> <code>{premium_users}</code>\n"
        f"📦 <b>Total Orders Placed:</b> <code>{total_orders}</code>\n"
        f"✅ <b>Verified Completed Orders:</b> <code>{completed_orders}</code>\n"
        f"💰 <b>Total Verified Revenue:</b> <code>₹{total_revenue:.2f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔐 <b>Total Vault Cookies:</b> <code>{total_cookies} active</code>\n"
        f"⏱ <b>Server Timestamp:</b> <i>{time.strftime('%Y-%m-%d %H:%M:%S')}</i>"
    )
    keyboard = InlineKeyboardMarkup([
        [btn("🔄 Refresh Stats", callback_data="admin_stats", style="primary")],
        [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
    ])
    await update.message.reply_html(text, reply_markup=keyboard)


async def cmd_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    lines = [
        "🔐 <b>VAULT COOKIE INVENTORY</b>",
        "<i>» Live Streaming Session Tokens in Storage</i>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ]
    icons = {
        "netflix": "🔴",
        "primevideo": "🎬",
        "crunchyroll": "🟠",
        "hbomax": "🟣",
        "spotify": "🟢",
        "jiohotstar": "🔵",
        "surfshark": "🦈"
    }
    for svc in SUPPORTED_SERVICES:
        c = count_vault_cookies(svc)
        ic = icons.get(svc, "📺")
        lines.append(f"{ic} <b>{svc.upper()}:</b> <code>{c} active cookies</code>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 <i>Auto-Rotation: Working cookies are paired automatically.</i>")

    keyboard = InlineKeyboardMarkup([
        [btn("🔄 Refresh Vault", callback_data="admin_vault", style="primary")],
        [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
    ])
    await update.message.reply_html("\n".join(lines), reply_markup=keyboard)


async def cmd_userstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    if not context.args:
        await update.message.reply_html("Usage: <code>/userstats &lt;user_id or email&gt;</code>")
        return
    target = context.args[0].strip()
    u = bot_db.get_user(target)
    if not u:
        with bot_db.lock:
            for uid, udata in bot_db.data.get("users", {}).items():
                if udata.get("username", "").lower() == target.lower() or udata.get("email", "").lower() == target.lower():
                    u = udata
                    break
    if not u:
        await update.message.reply_html(f"❌ <i>User</i> <code>{target}</code> <i>not found in database.</i>")
        return

    prem = u.get("premium", {})
    stats = u.get("stats", {})
    text = (
        f"👤 <b>USER PROFILE DOSSIER</b>\n"
        f"<i>» Account Diagnostics & Quotas</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>User ID:</b> <code>{u.get('user_id')}</code>\n"
        f"📛 <b>Name:</b> <b>{u.get('first_name', 'N/A')}</b>\n"
        f"🔗 <b>Username:</b> @{u.get('username', 'N/A')}\n"
        f"📅 <b>Member Since:</b> <i>{u.get('joined_at', 'N/A')}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 <b>Premium Status:</b> <b>{'Active 👑' if prem.get('is_premium') else 'Inactive ❌'}</b>\n"
        f"📦 <b>Tier Plan:</b> <code>{prem.get('plan', 'None')}</code>\n"
        f"🔑 <b>Remaining TV Logins:</b> <code>{prem.get('remaining_logins', 0)}</code>\n"
        f"⏳ <b>Expiry Timestamp:</b> <i>{prem.get('expires_at', 'N/A')}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>Successful Activations:</b> <code>{stats.get('successful_logins', 0)}</code>\n"
        f"⚠️ <b>Failed Attempts:</b> <code>{stats.get('failed_logins', 0)}</code>"
    )
    keyboard = InlineKeyboardMarkup([
        [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
    ])
    await update.message.reply_html(text, reply_markup=keyboard)


async def cmd_addpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_html(
            "<b>Usage:</b>\n"
            "<code>/addpremium &lt;user_id&gt; &lt;plan&gt; [days=30] [logins=10]</code>\n\n"
            "<i>Example:</i> <code>/addpremium 5784912530 monthly 30 10</code>"
        )
        return
    target_id = context.args[0].strip()
    plan = context.args[1].strip()
    days = int(context.args[2]) if len(context.args) > 2 and context.args[2].isdigit() else 30
    logins = int(context.args[3]) if len(context.args) > 3 and context.args[3].isdigit() else 10

    prem = bot_db.activate_user_premium(target_id, plan, duration_days=days, logins=logins)
    await update.message.reply_html(
        f"✅ <b>PREMIUM MEMBERSHIP ACTIVATED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> <code>{target_id}</code>\n"
        f"💎 <b>Tier Plan:</b> <b>{plan.upper()}</b>\n"
        f"🔑 <b>Total Logins Granted:</b> <code>{prem.get('remaining_logins')}</code>\n"
        f"⏳ <b>Valid Until:</b> <i>{prem.get('expires_at')}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


async def cmd_removepremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    if not context.args:
        await update.message.reply_html("Usage: <code>/removepremium &lt;user_id&gt;</code>")
        return
    target_id = context.args[0].strip()
    removed = bot_db.remove_user_premium(target_id)
    if removed:
        await update.message.reply_html(f"✅ <i>Premium membership revoked for</i> <code>{target_id}</code>.")
    else:
        await update.message.reply_html(f"❌ <i>User</i> <code>{target_id}</code> <i>not found.</i>")


async def cmd_toggleads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    new_state = bot_db.toggle_ads_gate()
    state_str = "ENABLED 🟢 (Tokens & Ads Required)" if new_state else "DISABLED 🔴 (Free Access / No Tokens Needed)"
    await update.message.reply_html(
        f"📢 <b>ADS TOKEN GATE TOGGLED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Current State: <b>{state_str}</b>\n\n"
        f"<i>• If OFF: Users can pair TVs freely without any access token or ads.</i>\n"
        f"<i>• If ON: Users must have premium or watch an ad to get access tokens.</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


async def cmd_setlogins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    if len(context.args) < 2 or not context.args[1].isdigit():
        await update.message.reply_html("Usage: <code>/setlogins &lt;user_id&gt; &lt;count&gt;</code>")
        return
    target_id = context.args[0].strip()
    count = int(context.args[1])
    bot_db.set_user_logins(target_id, count)
    await update.message.reply_html(
        f"✅ <b>Login Quota Updated!</b>\n"
        f"User: <code>{target_id}</code> · Balance: <b>{count} logins</b>"
    )


async def cmd_setplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Updates a subscription plan's price, validity days, and login count.
    Usage: /setplan <monthly|quarterly|yearly> <price> [days] [logins]
    Example: /setplan monthly 49 30 15
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_html(
            "<b>Usage:</b>\n"
            "<code>/setplan &lt;monthly|quarterly|yearly&gt; &lt;price&gt; [days] [logins]</code>\n\n"
            "<b>Example:</b>\n"
            "<code>/setplan monthly 49 30 15</code>\n"
            "<code>/setplan quarterly 129 90 40</code>\n"
            "<code>/setplan yearly 399 365 150</code>"
        )
        return

    plan_key = context.args[0].strip().lower()
    try:
        price = float(context.args[1].strip())
    except ValueError:
        await update.message.reply_html("❌ <i>Invalid price! Please provide a numeric value.</i>")
        return

    with bot_db.lock:
        plans = bot_db.data.setdefault("plans", {})
        if plan_key not in plans:
            plans[plan_key] = {"name": plan_key.capitalize(), "price": price, "days": 30, "logins": 10, "enabled": True}
        plan = plans[plan_key]
        plan["price"] = price
        if len(context.args) > 2 and context.args[2].isdigit():
            plan["days"] = int(context.args[2])
        if len(context.args) > 3 and context.args[3].isdigit():
            plan["logins"] = int(context.args[3])
        bot_db.save_unlocked()

    await update.message.reply_html(
        f"✅ <b>PLAN CONFIGURATION UPDATED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>Plan:</b> <b>{plan.get('name', plan_key.capitalize())}</b>\n"
        f"💰 <b>New Price:</b> <code>₹{plan['price']:.2f}</code>\n"
        f"⏳ <b>Validity:</b> <code>{plan['days']} Days</code>\n"
        f"🔑 <b>TV Logins:</b> <code>{plan['logins']} Logins</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Changes are live on the web store instantly!</i>"
    )


async def cmd_setservice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_html(
            "🏷️ <b>SERVICE ACCESS MODE CONFIGURATION</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>Usage:</b> <code>/setservice &lt;service&gt; &lt;free|premium&gt;</code>\n\n"
            "<b>Supported services:</b>\n"
            "<code>netflix, primevideo, crunchyroll, hbomax, spotify, surfshark, jiohotstar</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/setservice netflix premium</code> (👑 Premium Required to login)\n"
            "• <code>/setservice jiohotstar free</code> (🎁 Free for any user without premium)\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return

    svc_id = context.args[0].strip().lower()
    mode = context.args[1].strip().lower()

    if svc_id not in SERVICE_CONFIG:
        await update.message.reply_html(f"❌ <i>Unknown service '<code>{svc_id}</code>'. Check available services.</i>")
        return

    if mode not in ("free", "premium"):
        await update.message.reply_html("❌ <i>Mode must be either '<code>free</code>' or '<code>premium</code>'.</i>")
        return

    bot_db.set_service_mode(svc_id, mode)
    svc_name = SERVICE_CONFIG[svc_id]["name"]
    badge_title = "👑 Premium Required" if mode == "premium" else "🎁 Free (No Premium Needed)"
    desc = "Users MUST have active logins to pair TV." if mode == "premium" else "ANY user can pair TV freely without premium!"

    await update.message.reply_html(
        f"✅ <b>SERVICE ACCESS MODE UPDATED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📺 <b>Service:</b> <b>{svc_name}</b> (<code>{svc_id}</code>)\n"
        f"🏷️ <b>New Access Mode:</b> <b>{badge_title}</b>\n"
        f"ℹ️ <b>Policy:</b> <i>{desc}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Website badge and authorization rules updated instantly!</i>"
    )


async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    with bot_db.lock:
        orders = list(bot_db.data.get("orders", {}).values())[-8:]
    if not orders:
        await update.message.reply_html("<i>No transactions recorded in database yet.</i>")
        return
    lines = [
        "📦 <b>RECENT WEB TRANSACTIONS</b>",
        "<i>» Latest Portal UPI Orders</i>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ]
    for o in reversed(orders):
        status_emoji = "✅" if o.get("status") == "completed" else "⏳"
        lines.append(
            f"{status_emoji} <code>{o.get('order_id')}</code> · <b>₹{o.get('amount')}</b> ({o.get('plan_key').upper()})\n"
            f"   👤 <code>{o.get('user_id')}</code> | <i>{o.get('created_at', '')}</i>"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    keyboard = InlineKeyboardMarkup([
        [btn("🔄 Refresh Orders", callback_data="admin_orders", style="primary")],
        [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
    ])
    await update.message.reply_html("\n".join(lines), reply_markup=keyboard)


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    if not context.args:
        await update.message.reply_html("Usage: <code>/broadcast &lt;message&gt;</code>")
        return
    msg = " ".join(context.args)
    with bot_db.lock:
        uids = [k for k in bot_db.data.get("users", {}).keys() if k.isdigit()]
    sent, failed = 0, 0
    for uid in uids:
        try:
            await context.bot.send_message(chat_id=int(uid), text=msg, parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            failed += 1
    await update.message.reply_html(
        f"📢 <b>BROADCAST DISPATCH SUMMARY</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <b>Successfully Delivered:</b> <code>{sent}</code>\n"
        f"❌ <b>Failed / Blocked:</b> <code>{failed}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


async def admin_callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    user = query.from_user
    if not user or not is_admin(user.id):
        await query.answer("Access Denied: SuperAdmin Only.", show_alert=True)
        return

    data = query.data

    if data in ("adm_home", "menu_main", "menu_admin"):
        text = get_admin_main_text(user.first_name)
        keyboard = get_admin_main_keyboard()
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_stats", "admin_stats"):
        with bot_db.lock:
            users = bot_db.data.get("users", {})
            total_users = len(users)
            premium_users = sum(1 for u in users.values() if u.get("premium", {}).get("is_premium"))
            orders = bot_db.data.get("orders", {})
            total_orders = len(orders)
            completed_orders = sum(1 for o in orders.values() if o.get("status") == "completed")
            total_revenue = sum(float(o.get("amount", 0)) for o in orders.values() if o.get("status") == "completed")

        total_cookies = sum(count_vault_cookies(s) for s in SUPPORTED_SERVICES)

        text = (
            f"📊 <b>SYSTEM & DATABASE METRICS</b>\n"
            f"<i>» Real-Time Telemetry & Transaction Analytics</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 <b>Total Registered Users:</b> <code>{total_users}</code>\n"
            f"👑 <b>Active Premium Members:</b> <code>{premium_users}</code>\n"
            f"📦 <b>Total Orders Placed:</b> <code>{total_orders}</code>\n"
            f"✅ <b>Verified Completed Orders:</b> <code>{completed_orders}</code>\n"
            f"💰 <b>Total Verified Revenue:</b> <code>₹{total_revenue:.2f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔐 <b>Total Vault Cookies:</b> <code>{total_cookies} active</code>\n"
            f"⏱ <b>Server Timestamp:</b> <i>{time.strftime('%Y-%m-%d %H:%M:%S')}</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [btn("🔄 Refresh Stats", callback_data="admin_stats", style="primary")],
            [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
        ])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_vault", "admin_vault"):
        lines = [
            "🔐 <b>VAULT COOKIE INVENTORY</b>",
            "<i>» Live Streaming Session Tokens in Storage</i>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ]
        icons = {
            "netflix": "🔴",
            "primevideo": "🎬",
            "crunchyroll": "🟠",
            "hbomax": "🟣",
            "spotify": "🟢",
            "jiohotstar": "🔵",
            "surfshark": "🦈"
        }
        for svc in SUPPORTED_SERVICES:
            c = count_vault_cookies(svc)
            ic = icons.get(svc, "📺")
            lines.append(f"{ic} <b>{svc.upper()}:</b> <code>{c} active cookies</code>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💡 <i>Auto-Rotation: Working cookies are paired automatically.</i>")

        keyboard = InlineKeyboardMarkup([
            [btn("🔄 Refresh Vault", callback_data="admin_vault", style="primary")],
            [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
        ])
        await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_users", "admin_premium_users_list"):
        with bot_db.lock:
            total_users = len(bot_db.data.get("users", {}))
        text = (
            f"👥 <b>USER MANAGEMENT COMMANDS</b>\n"
            f"<i>» Direct Admin CLI Operations</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Inspect User:</b>\n"
            f"  <code>/userstats &lt;user_id or email&gt;</code>\n\n"
            f"• <b>Grant Premium Access:</b>\n"
            f"  <code>/addpremium &lt;user_id&gt; &lt;plan&gt; [days] [logins]</code>\n"
            f"  <i>Example:</i> <code>/addpremium 5784912530 monthly 30 10</code>\n\n"
            f"• <b>Revoke Premium:</b>\n"
            f"  <code>/removepremium &lt;user_id&gt;</code>\n\n"
            f"• <b>Modify Remaining Logins:</b>\n"
            f"  <code>/setlogins &lt;user_id&gt; &lt;count&gt;</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 <b>Total In Database:</b> <code>{total_users} users</code>"
        )
        keyboard = InlineKeyboardMarkup([
            [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
        ])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_orders", "admin_orders"):
        with bot_db.lock:
            orders = list(bot_db.data.get("orders", {}).values())[-8:]
        if not orders:
            content = "<i>No orders recorded in database yet.</i>"
        else:
            lines = [
                "📦 <b>RECENT WEB TRANSACTIONS</b>",
                "<i>» Latest Portal UPI Orders</i>",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ]
            for o in reversed(orders):
                status_emoji = "✅" if o.get("status") == "completed" else "⏳"
                lines.append(
                    f"{status_emoji} <code>{o.get('order_id')}</code> · <b>₹{o.get('amount')}</b> ({o.get('plan_key').upper()})\n"
                    f"   👤 <code>{o.get('user_id')}</code> | <i>{o.get('created_at', '')}</i>"
                )
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            content = "\n".join(lines)

        keyboard = InlineKeyboardMarkup([
            [btn("🔄 Refresh Orders", callback_data="admin_orders", style="primary")],
            [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
        ])
        await query.edit_message_text(content, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_plans", "admin_premium_menu"):
        plans = bot_db.get_premium_plans()
        lines = [
            "💎 <b>PREMIUM SUBSCRIPTION TIERS</b>",
            "<i>» Active Web Store Pricing & Device Quotas</i>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ]
        for key, p in plans.items():
            lines.append(f"• <b>{p.get('name', key.capitalize())}:</b> <code>₹{p.get('price'):.2f}</code> · {p.get('days')} Days · {p.get('logins')} Logins")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📁 <i>Stored and configured locally in web/db.json</i>")

        keyboard = InlineKeyboardMarkup([
            [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
        ])
        await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_upi", "admin_upi"):
        pay_cfg = bot_db.get_payment_config()
        text = (
            f"💳 <b>PAYMENT GATEWAY CONFIGURATION</b>\n"
            f"<i>» Active UPI Receiver Details</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 <b>UPI ID:</b> <code>{pay_cfg.get('upi_id')}</code>\n"
            f"👤 <b>Payee Name:</b> <code>{pay_cfg.get('payee_name')}</code>\n"
            f"🔗 <b>Verification Engine:</b> <i>paymt-verify.vercel.app</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 <i>Matches standard payment QR verification pipeline.</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
        ])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_broadcast", "admin_broadcast_prompt"):
        text = (
            f"📢 <b>BROADCAST ANNOUNCEMENT TOOL</b>\n"
            f"<i>» Mass Notification to Registered Members</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Command Syntax:</i>\n"
            f"<code>/broadcast &lt;your message here&gt;</code>\n\n"
            f"<b>Example:</b>\n"
            f"<code>/broadcast ⚡ Vault cookies restocked for Netflix & Prime Video!</code>\n\n"
            f"<i>Supports standard Telegram HTML formatting (bold, italic, code).</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        keyboard = InlineKeyboardMarkup([
            [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
        ])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_proxies", "admin_proxies"):
        raw_proxies = get_raw_proxies()
        loaded_p = load_proxies()
        in_c = sum(1 for p in loaded_p if get_proxy_country(p) == "IN")
        other_c = len(loaded_p) - in_c

        text = (
            f"🌐 <b>PROXY INFRASTRUCTURE & ROUTING DASHBOARD</b>\n"
            f"<i>» Indian Residential & Global Proxies</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Total Loaded Proxies:</b> <code>{len(loaded_p)}</code>\n"
            f"🇮🇳 <b>India Residential (JioHotstar & Crunchyroll):</b> <code>{in_c}</code>\n"
            f"🌍 <b>Other Regions / Dedicated:</b> <code>{other_c}</code>\n"
            f"📁 <b>Proxy Storage File:</b> <code>proxy.txt</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 <i>Use <code>/addproxy &lt;proxy&gt;</code> to register proxies via bot.</i>\n"
            f"<i>Supported: host:port, host:port:user:pass, http://user:pass@host:port</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [btn("🔄 Reload Proxies", callback_data="admin_proxies", style="success")],
            [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
        ])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_upload_prompt", "admin_upload_prompt"):
        text = (
            "📥 <b>COOKIE UPLOAD WIZARD</b>\n"
            "<i>» Direct Vault Session Token Injection</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Instructions:</b> Reply to any .zip, .txt, or .json cookie file with:\n"
            "  <code>/upload [service]</code>\n\n"
            "• <b>Service Names:</b>\n"
            "  <code>netflix</code>, <code>crunchyroll</code>, <code>hbomax</code> (or <code>hbo</code>), <code>jiohotstar</code> (or <code>hotstar</code>), <code>primevideo</code> (or <code>prime</code>), <code>spotify</code>, <code>surfshark</code>\n\n"
            "🇮🇳 <i>JioHotstar & Crunchyroll are verified through Indian residential proxies automatically.</i>"
        )
        keyboard = InlineKeyboardMarkup([[btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_grant_sub", "admin_grant_sub_prompt"):
        text = (
            "🎁 <b>GRANT USER SUBSCRIPTION</b>\n"
            "<i>» Instant Premium & Device Logins Allocation</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Command Syntax:</b>\n"
            "  <code>/addpremium &lt;user_id&gt; &lt;plan&gt; [days=30] [logins=10]</code>\n\n"
            "• <b>Example:</b>\n"
            "  <code>/addpremium 5784912530 monthly 30 10</code>\n\n"
            "• <b>Revoke / Adjust:</b>\n"
            "  <code>/removepremium &lt;user_id&gt;</code>\n"
            "  <code>/setlogins &lt;user_id&gt; &lt;count&gt;</code>"
        )
        keyboard = InlineKeyboardMarkup([[btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_referral", "admin_referral_menu"):
        with bot_db.lock:
            orders = list(bot_db.data.get("orders", {}).values())
            completed = sum(1 for o in orders if o.get("status") == "completed")
            rev = sum(float(o.get("amount", 0)) for o in orders if o.get("status") == "completed")
            u_cnt = len(bot_db.data.get("users", {}))
        text = (
            "👥 <b>REFERRAL & WALLET OVERVIEW</b>\n"
            "<i>» Transaction Telemetry & Ledger</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Registered Members:</b> <code>{u_cnt}</code>\n"
            f"📦 <b>Total Orders:</b> <code>{len(orders)}</code>\n"
            f"✅ <b>Verified Completed:</b> <code>{completed}</code>\n"
            f"💰 <b>Total Platform Revenue:</b> <code>₹{rev:.2f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <i>Tap Recent Orders to view latest UPI transactions.</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [btn("📦 View Recent Orders", callback_data="admin_orders", style="primary")],
            [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
        ])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data == "toggle_ads_gate":
        new_state = bot_db.toggle_ads_gate()
        state_msg = "Ads Gate: ENABLED 🟢 (Tokens & Ads Required)" if new_state else "Ads Gate: DISABLED 🔴 (Free Access / No Tokens Needed)"
        await query.answer(state_msg, show_alert=True)
        text = get_admin_main_text(user.first_name)
        keyboard = get_admin_main_keyboard()
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_settings", "admin_settings"):
        ads_on = bot_db.is_ads_gate_enabled()
        gate_status = "ENABLED 🟢 (Tokens & Ads Required)" if ads_on else "DISABLED 🔴 (Free Access / No Tokens Needed)"
        text = (
            "🛠️ <b>SETTINGS PANEL</b>\n"
            "<i>» Runtime & Deployment Parameters</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 <b>Ads Token Gate:</b> <code>{gate_status}</code>\n"
            f"🌐 <b>Listening Port:</b> <code>{os.environ.get('PORT', 5000)}</code>\n"
            f"🔒 <b>Mandatory Channels:</b> <code>{','.join(REQUIRED_CHANNELS)}</code>\n"
            f"📁 <b>Web Database:</b> <code>web/db.json</code>\n"
            f"⏱ <b>GitHub 6-Hour Private Backup:</b> <code>Active & Running</code>"
        )
        toggle_btn_text = "Turn Ads Gate OFF 🔴" if ads_on else "Turn Ads Gate ON 🟢"
        toggle_btn_style = "danger" if ads_on else "success"
        keyboard = InlineKeyboardMarkup([
            [btn(text=toggle_btn_text, callback_data="toggle_ads_gate", style=toggle_btn_style)],
            [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
        ])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_backup_now", "admin_backup_db_now"):
        await query.edit_message_text("⏳ <i>Syncing web database & vault cookies to private GitHub repository...</i>", parse_mode=ParseMode.HTML)
        success, msg = sync_github_backup()
        status_icon = "✅" if success else "❌"
        text = (
            f"{status_icon} <b>GITHUB AUTO-BACKUP REPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Status:</b> <i>{msg}</i>\n"
            f"⏱ <b>Schedule:</b> <i>Automatic every 6 hours daemon active</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        keyboard = InlineKeyboardMarkup([[btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_restore_now", "admin_restore_db_now"):
        text = (
            "🔄 <b>DATABASE RESTORE UTILITY</b>\n"
            "<i>» Recovery & Synchronization</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Storage File:</b> <code>web/db.json</code>\n"
            "• <b>Automatic Backups:</b> <i>Pushed every 6 hours to private GitHub repo</i>\n"
            "• <b>Manual Restore:</b> <i>Replace <code>web/db.json</code> or pull latest commit from your private backup repository.</i>"
        )
        keyboard = InlineKeyboardMarkup([[btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data in ("adm_delete_vault", "admin_delete_vault_menu"):
        text = (
            "🗑️ <b>VAULT PURGE UTILITY</b>\n"
            "<i>» Cookie Cache Clearance</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "To clear cookies for a specific service or all services, run:\n"
            "<code>/clearvault &lt;service|all&gt;</code>\n\n"
            "<i>Example:</i> <code>/clearvault crunchyroll</code> or <code>/clearvault all</code>"
        )
        keyboard = InlineKeyboardMarkup([[btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]])
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


callback_query_handler = admin_callback_query_handler


async def cmd_proxies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    raw_proxies = get_raw_proxies()
    loaded_p = load_proxies()
    in_c = sum(1 for p in loaded_p if get_proxy_country(p) == "IN")
    other_c = len(loaded_p) - in_c

    text = fmt_bot(
        f"🌐 <b>PROXY INFRASTRUCTURE & ROUTING DASHBOARD</b>\n"
        f"<i>» Indian Residential & Global Proxies</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Total Loaded Proxies:</b> <code>{len(loaded_p)}</code>\n"
        f"🇮🇳 <b>India Residential (JioHotstar & Crunchyroll):</b> <code>{in_c}</code>\n"
        f"🌍 <b>Other Regions / Dedicated:</b> <code>{other_c}</code>\n"
        f"📁 <b>Proxy Storage File:</b> <code>proxy.txt</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>Use <code>/addproxy &lt;proxy&gt;</code> or reply to a proxy text list.</i>"
    )
    keyboard = InlineKeyboardMarkup([
        [btn("🔄 Reload Proxies", callback_data="admin_proxies", style="success")],
        [btn("🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
    ])
    await update.message.reply_html(text, reply_markup=keyboard)


async def cmd_addproxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    text_to_process = ""
    if context.args:
        text_to_process = " ".join(context.args)
    elif update.message.reply_to_message:
        if update.message.reply_to_message.text:
            text_to_process = update.message.reply_to_message.text
        elif update.message.reply_to_message.document:
            doc = update.message.reply_to_message.document
            if doc.file_name.lower().endswith(('.txt', '.conf')):
                f = await context.bot.get_file(doc.file_id)
                fb = await f.download_as_bytearray()
                text_to_process = fb.decode('utf-8', errors='ignore')

    if not text_to_process.strip():
        await update.message.reply_html(
            fmt_bot(
                "🌐 <b>Add Proxy Usage:</b>\n"
                "<code>/addproxy &lt;proxy_string&gt;</code>\n\n"
                "<b>Supported formats:</b>\n"
                "• <code>http://user:pass@ip:port</code>\n"
                "• <code>ip:port:user:pass</code>\n"
                "• <code>ip:port:user:pass:IN</code> (Indian Residential)\n"
                "• Or reply to a text/doc message with <code>/addproxy</code>\n\n"
                "🇮🇳 <i>Tip: Tag with ':IN' or country comment to mark as Indian residential proxy for JioHotstar & Crunchyroll.</i>"
            )
        )
        return

    status_msg = await update.message.reply_html(fmt_bot("⏳ <i>Validating and registering proxies...</i>"))

    lines = [l.strip() for l in text_to_process.splitlines() if l.strip()]
    added, in_count = 0, 0
    valid_lines = []

    for line in lines:
        p = parse_proxy_line(line)
        if p:
            valid_lines.append(line)
            added += 1
            if get_proxy_country(p) == "IN":
                in_count += 1

    if valid_lines:
        current_raw = get_raw_proxies()
        new_raw = current_raw + [l for l in valid_lines if l not in current_raw]
        save_proxies_to_file(new_raw)
        load_proxies()

        await status_msg.edit_text(
            fmt_bot(
                f"✅ <b>Proxies Registered Successfully!</b>\n\n"
                f"🌐 <b>Total Added:</b> <code>{added}</code>\n"
                f"🇮🇳 <b>Indian Residential Proxies:</b> <code>{in_count}</code>\n"
                f"📦 <b>Total Active in Vault:</b> <code>{len(get_raw_proxies())}</code>\n\n"
                f"⚡ <i>JioHotstar & Crunchyroll will prioritize Indian residential routing.</i>"
            ),
            parse_mode=ParseMode.HTML
        )
    else:
        await status_msg.edit_text(
            fmt_bot("❌ <i>No valid proxy format detected. Please verify credentials.</i>"),
            parse_mode=ParseMode.HTML
        )


async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_html(
            fmt_bot(
                "📎 <b>Usage:</b> Reply to a ZIP/TXT/JSON cookie file with <code>/upload [service]</code>\n\n"
                "Supported services: <code>netflix</code>, <code>crunchyroll</code>, <code>hbomax</code>, <code>surfshark</code>, <code>spotify</code>, <code>jiohotstar</code>, <code>primevideo</code>\n"
                "🇮🇳 <i>JioHotstar & Crunchyroll utilize Indian residential proxy routing.</i>"
            )
        )
        return

    doc = update.message.reply_to_message.document
    fn_lower = doc.file_name.lower()
    if not fn_lower.endswith(('.zip', '.txt', '.json')):
        await update.message.reply_html(fmt_bot("❌ <i>Only .zip, .txt, or .json cookie files are accepted!</i>"))
        return

    service = None
    if context.args:
        val = context.args[0].strip().lower()
        if val in SUPPORTED_SERVICES:
            service = val
        elif val in ("hbo", "hbomax", "max", "hbo_max", "hbo-max"):
            service = "hbomax"
        elif val in ("hotstar", "jio", "jiohotstar"):
            service = "jiohotstar"
        elif val in ("crunchy", "crunchyroll"):
            service = "crunchyroll"
        elif val in ("prime", "amazon", "primevideo", "pv"):
            service = "primevideo"
        elif val in ("netflix", "nf", "nftoken"):
            service = "netflix"
        elif val in ("spot", "spotify"):
            service = "spotify"
        elif val in ("surf", "surfshark"):
            service = "surfshark"

    if not service:
        if any(x in fn_lower for x in ("hbo", "max")):
            service = "hbomax"
        elif any(x in fn_lower for x in ("hotstar", "jio")):
            service = "jiohotstar"
        elif "crunchy" in fn_lower:
            service = "crunchyroll"
        elif any(x in fn_lower for x in ("prime", "amazon", "pv")):
            service = "primevideo"
        elif any(x in fn_lower for x in ("netflix", "nftoken")):
            service = "netflix"
        elif "spot" in fn_lower:
            service = "spotify"
        elif "surf" in fn_lower:
            service = "surfshark"
        else:
            service = "netflix"

    target_dir = os.path.join(COOKIES_DIR, service)
    os.makedirs(target_dir, exist_ok=True)

    status_msg = await update.message.reply_html(
        fmt_bot(f"📥 <i>Downloading and processing cookies for {service.upper()}...</i>")
    )

    try:
        file = await context.bot.get_file(doc.file_id)
        file_bytes = await file.download_as_bytearray()
        added, skipped = 0, 0
        proxies_for_svc = get_service_proxies(service)

        def _save_cookie(content, name):
            nonlocal added, skipped
            if not content or len(content.strip()) < 10:
                skipped += 1
                return
            dest = os.path.join(target_dir, os.path.basename(name))
            if os.path.exists(dest):
                suffix = secrets.token_hex(3)
                n, ext = os.path.splitext(os.path.basename(name))
                dest = os.path.join(target_dir, f"{n}_{suffix}{ext}")
            with open(dest, "w", encoding="utf-8") as f:
                f.write(content)
            added += 1

        if fn_lower.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as zf:
                for n in zf.namelist():
                    if n.lower().endswith(('.txt', '.json')) and not n.startswith('__MACOSX'):
                        c = zf.read(n).decode('utf-8', errors='ignore')
                        _save_cookie(c, n)
        else:
            c = file_bytes.decode('utf-8', errors='ignore')
            _save_cookie(c, doc.file_name)

        vault_c = count_vault_cookies(service)
        proxy_note = f"\n🇮🇳 <i>Validated with Indian residential proxy: {len(proxies_for_svc)} active</i>" if service in ("jiohotstar", "crunchyroll") and proxies_for_svc else ""
        await status_msg.edit_text(
            fmt_bot(
                f"✅ <b>Upload Complete for {service.upper()}!</b>\n\n"
                f"📥 <b>Added:</b> <code>{added}</code> cookies\n"
                f"⏭️ <b>Skipped:</b> <code>{skipped}</code>\n"
                f"🍪 <b>Total in {service.upper()} vault:</b> <code>{vault_c}</code>{proxy_note}"
            ),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await status_msg.edit_text(fmt_bot(f"❌ <i>Upload Error: {e}</i>"), parse_mode=ParseMode.HTML)


def sync_github_backup() -> tuple[bool, str]:
    """Uploads web/db.json and vault state to a private GitHub repo every 6 hours."""
    token = os.environ.get("GITHUB_BACKUP_TOKEN", "").strip()
    repo_name = os.environ.get("GITHUB_BACKUP_REPO", "sinu4uu/tv-login-db-backup").strip()
    if not token:
        return False, "GITHUB_BACKUP_TOKEN is not configured"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        import requests
        if "/" in repo_name:
            owner, rname = repo_name.split("/", 1)
        else:
            r_user = requests.get("https://api.github.com/user", headers=headers, timeout=10)
            if r_user.status_code == 200:
                owner = r_user.json().get("login")
                rname = repo_name
            else:
                return False, f"GitHub authentication failed: {r_user.status_code}"

        # Ensure repo exists as private
        check_url = f"https://api.github.com/repos/{owner}/{rname}"
        r_chk = requests.get(check_url, headers=headers, timeout=10)
        if r_chk.status_code == 404:
            r_create = requests.post("https://api.github.com/user/repos", headers=headers, json={
                "name": rname,
                "private": True,
                "description": "Automated private TV Login Database and Vault Backup"
            }, timeout=10)
            if r_create.status_code not in (200, 201):
                return False, f"Failed creating private repo: {r_create.text}"

        # Upload web/db.json
        db_file = os.path.join(WEB_DIR, "db.json")
        if os.path.exists(db_file):
            with open(db_file, "rb") as f:
                content_b64 = base64.b64encode(f.read()).decode("utf-8")

            file_url = f"https://api.github.com/repos/{owner}/{rname}/contents/db.json"
            r_get = requests.get(file_url, headers=headers, timeout=10)
            payload = {
                "message": f"Auto-backup db.json {time.strftime('%Y-%m-%d %H:%M:%S')}",
                "content": content_b64
            }
            if r_get.status_code == 200:
                payload["sha"] = r_get.json().get("sha")

            r_put = requests.put(file_url, headers=headers, json=payload, timeout=15)
            if r_put.status_code not in (200, 201):
                return False, f"Error pushing db.json: {r_put.text}"

        print(f"[*] GitHub backup succeeded to private repo: {owner}/{rname}")
        return True, f"Successfully backed up to private repo: {owner}/{rname}"
    except Exception as e:
        print(f"[!] GitHub backup failed: {e}")
        return False, str(e)


_BACKUP_THREAD_STARTED = False

def start_backup_daemon():
    """Starts the background thread that backs up to GitHub private repo every 6 hours."""
    global _BACKUP_THREAD_STARTED
    if _BACKUP_THREAD_STARTED:
        return
    _BACKUP_THREAD_STARTED = True

    def _loop():
        # Initial delay 45s after start
        time.sleep(45)
        while True:
            try:
                sync_github_backup()
            except Exception as e:
                print(f"[!] Periodic backup error: {e}")
            # Sleep 6 hours (21600 seconds)
            time.sleep(6 * 3600)

    t = threading.Thread(target=_loop, daemon=True, name="github_backup_6h")
    t.start()


_BOT_THREAD_STARTED = False


_BOT_THREAD_STARTED = False

def start_telegram_bot_daemon():
    """Starts the embedded Telegram Admin Bot in a dedicated background daemon thread."""
    global _BOT_THREAD_STARTED
    if _BOT_THREAD_STARTED:
        return
    _BOT_THREAD_STARTED = True

    if not BOT_TOKEN:
        print("[!] Warning: BOT_TOKEN not configured. Telegram Admin Bot skipped.")
        return

    def _run_bot():
        try:
            print("[*] Starting embedded Telegram Admin Bot polling...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
            bot_app.add_handler(CommandHandler("start", cmd_start))
            bot_app.add_handler(CommandHandler("admin", cmd_start))
            bot_app.add_handler(CommandHandler("help", cmd_help))
            bot_app.add_handler(CommandHandler("ads", cmd_toggleads))
            bot_app.add_handler(CommandHandler("toggleads", cmd_toggleads))
            bot_app.add_handler(CommandHandler("stats", cmd_stats))
            bot_app.add_handler(CommandHandler("vault", cmd_vault))
            bot_app.add_handler(CommandHandler("userstats", cmd_userstats))
            bot_app.add_handler(CommandHandler("addpremium", cmd_addpremium))
            bot_app.add_handler(CommandHandler("removepremium", cmd_removepremium))
            bot_app.add_handler(CommandHandler("setlogins", cmd_setlogins))
            bot_app.add_handler(CommandHandler("setplan", cmd_setplan))
            bot_app.add_handler(CommandHandler("setservice", cmd_setservice))
            bot_app.add_handler(CommandHandler("service", cmd_setservice))
            bot_app.add_handler(CommandHandler("orders", cmd_orders))
            bot_app.add_handler(CommandHandler("broadcast", cmd_broadcast))
            bot_app.add_handler(CommandHandler("proxies", cmd_proxies))
            bot_app.add_handler(CommandHandler("proxy", cmd_proxies))
            bot_app.add_handler(CommandHandler("addproxy", cmd_addproxy))
            bot_app.add_handler(CommandHandler("upload", cmd_upload))
            bot_app.add_handler(CallbackQueryHandler(admin_callback_query_handler))
            bot_app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, cmd_upload))

            bot_app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                bootstrap_retries=5,
                close_loop=False,
                stop_signals=None
            )
        except Exception as e:
            print(f"[!] Telegram Admin Bot encountered error: {e}")

    t = threading.Thread(target=_run_bot, daemon=True, name="telegram_admin_bot")
    t.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: trigger admin bot background daemon & 6-hour GitHub backup daemon
    start_telegram_bot_daemon()
    start_backup_daemon()
    yield
    # Shutdown logic if needed


app = FastAPI(
    title="XDROID TV Activation Portal",
    description="Direct Web-based Smart TV Activation Engine",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Service Metadata Configuration
SERVICE_CONFIG = {
    "netflix": {
        "name": "Netflix",
        "category": "Movies & Series",
        "brand_color": "#E50914",
        "icon": "netflix",
        "code_length": 8,
        "code_type": "numeric",
        "placeholder": "12345678",
        "description": "Enter the 8-digit code shown on your TV screen (e.g. netflix.com/tv8)",
        "is_premium_required": True,
        "is_free_trial": False
    },
    "primevideo": {
        "name": "Prime Video",
        "category": "Movies & Series",
        "brand_color": "#00A8E1",
        "icon": "primevideo",
        "code_length": 6,
        "code_type": "alphanumeric",
        "placeholder": "Y6ALSY",
        "description": "Enter the 6-character code from your TV screen (e.g. primevideo.com/mytv)",
        "is_premium_required": True,
        "is_free_trial": False
    },
    "crunchyroll": {
        "name": "Crunchyroll",
        "category": "Anime Streaming",
        "brand_color": "#F47521",
        "icon": "crunchyroll",
        "code_length": 6,
        "code_type": "alphanumeric",
        "placeholder": "CR1234",
        "description": "Enter the 6-character code from your Crunchyroll TV screen",
        "is_premium_required": True,
        "is_free_trial": False
    },
    "hbomax": {
        "name": "HBO Max",
        "category": "Premium Entertainment",
        "brand_color": "#5822B4",
        "icon": "hbomax",
        "code_length": 6,
        "code_type": "alphanumeric",
        "placeholder": "HB1234",
        "description": "Enter the 6-character activation code from your TV",
        "is_premium_required": True,
        "is_free_trial": False
    },
    "spotify": {
        "name": "Spotify",
        "category": "Music & Podcasts",
        "brand_color": "#1DB954",
        "icon": "spotify",
        "code_length": 6,
        "code_type": "alphanumeric",
        "placeholder": "SP1234",
        "description": "Enter the 6-character code shown on your Spotify TV screen",
        "is_premium_required": True,
        "is_free_trial": False
    },
    "surfshark": {
        "name": "Surfshark VPN",
        "category": "Privacy & VPN",
        "brand_color": "#1EBCB4",
        "icon": "surfshark",
        "code_length": 6,
        "code_type": "alphanumeric",
        "placeholder": "SS1234",
        "description": "Enter the 6-character pairing code from your Surfshark TV app",
        "is_premium_required": True,
        "is_free_trial": False
    },
    "jiohotstar": {
        "name": "JioHotstar",
        "category": "Live Sports & Shows",
        "brand_color": "#0C64DE",
        "icon": "jiohotstar",
        "code_length": 150,
        "code_type": "link_or_code",
        "placeholder": "Scan TV QR or paste activation link",
        "description": "Scan the QR code shown on your TV screen or paste the activation URL",
        "is_premium_required": True,
        "is_free_trial": False
    }
}

# Rate limit disabled per configuration
def is_rate_limited(ip: str) -> bool:
    return False


class ActivationRequest(BaseModel):
    service: str = Field(..., description="Service identifier (e.g. netflix, primevideo)")
    tv_code: str = Field(..., description="TV activation code from TV screen")
    chat_id: Optional[str] = Field(None, description="Optional Telegram Chat ID")
    email: Optional[str] = Field(None, description="Optional Firebase Email")
    ad_token: Optional[str] = Field(None, description="Optional Shrinkme Ad verification token")

class GenerateAdRequest(BaseModel):
    email: Optional[str] = None
    chat_id: Optional[str] = None

class VerifyTelegramRequest(BaseModel):
    chat_id: str = Field(..., description="Telegram numeric Chat ID")
    email: Optional[str] = Field(None, description="Optional Google/Firebase Email")

class UserStatusRequest(BaseModel):
    chat_id: Optional[str] = None
    email: Optional[str] = None

class CreateOrderRequest(BaseModel):
    plan_key: str
    chat_id: Optional[str] = None
    email: Optional[str] = None

class CheckOrderStatusRequest(BaseModel):
    order_id: str

class VerifyUtrRequest(BaseModel):
    order_id: str
    utr: str

class ScanQrRequest(BaseModel):
    image_base64: str



def check_telegram_member_sync(bot_token: str, channel: str, user_id: str) -> bool:
    """Verifies if user_id is a member of channel via Telegram Bot API."""
    try:
        clean_channel = channel.strip()
        url = f"https://api.telegram.org/bot{bot_token}/getChatMember?chat_id={urllib.parse.quote(clean_channel)}&user_id={user_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "XdroidTV/2.0"})
        with urllib.request.urlopen(req, timeout=6) as response:
            data = json.loads(response.read().decode())
            if data.get("ok"):
                status = data.get("result", {}).get("status")
                return status in ("creator", "administrator", "member", "restricted")
    except Exception as e:
        err_msg = str(e).lower()
        if "user not found" in err_msg or "user_id_invalid" in err_msg or "chat not found" in err_msg:
            return False
        print(f"[!] Telegram API check error for {channel}/{user_id}: {e}")
    return False


@app.get("/api/config/firebase")
async def get_firebase_config():
    """Returns public client Firebase SDK configuration from web/.env."""
    return {
        "apiKey": os.environ.get("VITE_FIREBASE_API_KEY", ""),
        "authDomain": os.environ.get("VITE_FIREBASE_AUTH_DOMAIN", ""),
        "projectId": os.environ.get("VITE_FIREBASE_PROJECT_ID", ""),
        "storageBucket": os.environ.get("VITE_FIREBASE_STORAGE_BUCKET", ""),
        "messagingSenderId": os.environ.get("VITE_FIREBASE_MESSAGING_SENDER_ID", ""),
        "appId": os.environ.get("VITE_FIREBASE_APP_ID", ""),
        "measurementId": os.environ.get("VITE_FIREBASE_MEASUREMENT_ID", "")
    }


@app.post("/api/auth/verify-telegram")
async def verify_telegram_free_trial(req: VerifyTelegramRequest):
    """
    Verifies Telegram Chat ID membership in mandatory channels (@xdro1d, @xdroidchat).
    If joined, activates 1 Free Trial device login in bot_db.
    """
    raw_id = req.chat_id.strip().replace("@", "")
    if not raw_id.isdigit():
        return {
            "success": False,
            "error": "INVALID_ID",
            "message": "Invalid Chat ID! Please enter your numeric Telegram Chat ID (e.g. 5784912530)."
        }

    user_id = int(raw_id)
    not_joined = []

    for chan in REQUIRED_CHANNELS:
        joined = await asyncio.to_thread(check_telegram_member_sync, BOT_TOKEN, chan, str(user_id))
        if not joined:
            not_joined.append(chan)

    if not_joined:
        return {
            "success": False,
            "error": "NOT_JOINED",
            "message": f"You haven't joined all required channels yet!",
            "missing_channels": not_joined,
            "channels": [
                {"name": "XDROID Updates", "url": "https://t.me/xdro1d", "handle": "@xdro1d"},
                {"name": "XDROID Community Chat", "url": "https://t.me/xdroidchat", "handle": "@xdroidchat"}
            ]
        }

    # User is confirmed member
    trial_logins = 1
    already_claimed = False

    try:
        user_key = str(user_id)
        with bot_db.lock:
            existing_user = bot_db.data.get("users", {}).get(user_key)
            if existing_user is not None:
                # User already exists in database - Do NOT give free trial again!
                already_claimed = True
                user = existing_user
                user["free_trial_claimed"] = True
                bot_db.save_unlocked()
            else:
                user = bot_db.register_user(user_id, username=None, first_name="WebUser")
                user["free_trial_claimed"] = True
                bot_db.add_user_logins(user_id, trial_logins)
                if not user.get("premium"):
                    bot_db.activate_user_premium(user_id, plan_key="trial", duration_days=3, logins=trial_logins)
                bot_db.save_unlocked()

        # Link with web_users if email provided
        if req.email:
            with bot_db.lock:
                web_users = bot_db.data.setdefault("web_users", {})
                web_users[req.email.lower().strip()] = {
                    "chat_id": str(user_id),
                    "verified_at": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                bot_db.save_unlocked()

        prem = bot_db.get_user_premium(user_id)
        remaining = prem.get("remaining_logins", 0) if prem else 0
    except Exception as e:
        print(f"[!] Error updating bot_db for free trial: {e}")
        remaining = 0

    msg = "Telegram Verified! 1 Free Trial device activation ready." if not already_claimed else "Telegram Chat ID already registered! Free trial was previously claimed."
    return {
        "success": True,
        "message": msg,
        "chat_id": str(user_id),
        "already_claimed": already_claimed,
        "free_trial_claimed": True,
        "remaining_logins": remaining
    }


@app.post("/api/auth/user-status")
async def get_user_status(req: UserStatusRequest):
    """Returns current user premium and login balances."""
    chat_id = req.chat_id
    if not chat_id and req.email:
        with bot_db.lock:
            web_users = bot_db.data.get("web_users", {})
            link = web_users.get(req.email.lower().strip())
            if link:
                chat_id = link.get("chat_id")

    # If no chat_id linked, check if user exists by email key
    user_key = chat_id or (req.email.lower().strip() if req.email else None)
    if not user_key:
        return {"is_premium": False, "remaining_logins": 0, "is_verified": False, "free_trial_claimed": False}

    try:
        prem = bot_db.get_user_premium(user_key)
        user = bot_db.get_user(user_key)
        trial_claimed = bool(user and user.get("free_trial_claimed"))
        is_prem = bool(prem and prem.get("is_premium") and prem.get("remaining_logins", 0) > 0)
        free_passes = bot_db.get_free_service_logins(user_key)
        return {
            "is_premium": is_prem,
            "remaining_logins": prem.get("remaining_logins", 0) if prem else 0,
            "plan": prem.get("plan", "None") if prem else "None",
            "is_verified": bool(chat_id),
            "free_trial_claimed": trial_claimed,
            "free_service_logins": free_passes,
            "chat_id": str(chat_id or "")
        }
    except Exception:
        return {"is_premium": False, "remaining_logins": 0, "is_verified": False, "free_service_logins": {}}


@app.get("/api/payment/plans")
async def get_plans_endpoint():
    """Returns dynamic premium plans configured in bot_db."""
    plans = bot_db.get_premium_plans()
    return {"success": True, "plans": plans}


@app.post("/api/payment/create-order")
async def create_payment_order(req: CreateOrderRequest):
    """Generates order and UPI QR code. Requires sign in on website."""
    if not req.email:
        return {
            "success": False,
            "error": "Please sign in with Google on the website before proceeding to payment."
        }

    plan_key = req.plan_key.lower().strip()

    user_id = None
    if req.chat_id and str(req.chat_id).strip().isdigit():
        user_id = int(req.chat_id.strip())
    elif req.email:
        with bot_db.lock:
            web_users = bot_db.data.get("web_users", {})
            link = web_users.get(req.email.lower().strip())
            if link and str(link.get("chat_id", "")).isdigit():
                user_id = int(link["chat_id"])
            else:
                user_id = abs(hash(req.email.lower().strip())) % (10**9)

    if not user_id:
        user_id = 999999999

    try:
        if not bot_db.get_user(user_id):
            bot_db.register_user(user_id, username=req.email, first_name="WebUser")
    except Exception:
        pass

    plans = bot_db.get_premium_plans()
    plan = plans.get(plan_key)
    if not plan:
        defaults = {
            "monthly": {"name": "Monthly", "price": 30.0, "days": 30, "logins": 10},
            "quarterly": {"name": "Quarterly", "price": 50.0, "days": 90, "logins": 35},
            "yearly": {"name": "Yearly", "price": 321.0, "days": 365, "logins": 150}
        }
        plan = defaults.get(plan_key, defaults["monthly"])

    price = float(plan.get("price", 30.0))
    days = int(plan.get("days", 30))
    logins = int(plan.get("logins", 10))
    plan_name = plan.get("name", plan_key.capitalize())

    pay_cfg = bot_db.get_payment_config()
    upi_id = pay_cfg.get("upi_id", "sudhirxd@fam")
    payee_name = pay_cfg.get("payee_name", "Sudhir kumar")

    order_id = generate_order_id()

    try:
        bot_db.create_order(order_id, user_id, req.email or str(user_id), plan_key, price, logins, days)
    except Exception as e:
        print(f"[!] Error creating order in bot_db: {e}")

    qr_data_url = ""
    upi_uri = f"upi://pay?pa={upi_id}&pn={urllib.parse.quote(payee_name)}&am={price:.2f}&cu=INR&tn={urllib.parse.quote(order_id)}"
    try:
        qr_bio = generate_payment_qr(upi_id, payee_name, price, order_id)
        qr_data_url = f"data:image/png;base64,{base64.b64encode(qr_bio.getvalue()).decode('utf-8')}"
    except Exception as e:
        print(f"[!] Warning: Could not generate QR: {e}")

    return {
        "success": True,
        "order_id": order_id,
        "plan_key": plan_key,
        "plan_name": plan_name,
        "amount": price,
        "days": days,
        "logins": logins,
        "upi_id": upi_id,
        "payee_name": payee_name,
        "upi_uri": upi_uri,
        "qr_data_url": qr_data_url
    }


@app.post("/api/payment/check-status")
async def check_payment_status(req: CheckOrderStatusRequest):
    """Verifies payment using standalone verify_fampay_order_payment."""
    order_id = req.order_id.strip()
    order = bot_db.get_order(order_id)

    if not order:
        return {"success": False, "verified": False, "error": "Order not found or expired."}

    if order.get("status") == "completed":
        prem = bot_db.get_user_premium(order["user_id"])
        return {
            "success": True,
            "verified": True,
            "status": "completed",
            "message": "Payment verified successfully!",
            "remaining_logins": prem.get("remaining_logins", order.get("logins", 10)),
            "expires_at": prem.get("expires_at", "Active")
        }

    amount = float(order.get("amount", 0))

    try:
        verified, msg, detail = await asyncio.to_thread(verify_fampay_order_payment, order_id, amount)
    except Exception as e:
        return {"success": True, "verified": False, "message": f"Verification gateway busy: {e}"}

    if verified:
        txn_id = detail.get("txn_id", "") if detail else ""
        user_id = order["user_id"]
        try:
            bot_db.update_order_status(order_id, "completed", utr=txn_id)
            if txn_id:
                bot_db.record_redeemed_utr(txn_id, user_id, order_id)
            prem = bot_db.activate_user_premium(
                user_id,
                order["plan_key"],
                int(order.get("days", 30)),
                int(order.get("logins", 10))
            )
        except Exception as e:
            print(f"[!] Error activating premium: {e}")
            prem = {"remaining_logins": order.get("logins", 10), "expires_at": "Active"}

        return {
            "success": True,
            "verified": True,
            "status": "completed",
            "message": "Payment verified! Premium activated.",
            "plan": order["plan_key"],
            "remaining_logins": prem.get("remaining_logins", 10),
            "expires_at": prem.get("expires_at", "Active")
        }

    return {"success": True, "verified": False, "message": msg}


@app.post("/api/payment/verify-utr")
async def verify_utr_endpoint(req: VerifyUtrRequest):
    """Verifies manual UTR / Txn ID using standalone verify_fampay_payment."""
    order_id = req.order_id.strip()
    utr = req.utr.strip().upper()

    if len(utr) < 6:
        return {"success": False, "error": "Invalid UTR format. Please enter a valid UPI Reference / Transaction ID."}

    order = bot_db.get_order(order_id)
    if not order:
        return {"success": False, "error": "Order not found."}

    if bot_db.is_utr_redeemed(utr):
        return {"success": False, "error": "This UTR / Transaction ID has already been redeemed."}

    amount = float(order.get("amount", 0))

    try:
        verified, msg, detail = await asyncio.to_thread(verify_fampay_payment, utr, amount)
    except Exception as e:
        return {"success": False, "error": f"Verification error: {e}"}

    if verified:
        user_id = order["user_id"]
        txn_id = utr
        try:
            bot_db.update_order_status(order_id, "completed", utr=txn_id)
            bot_db.record_redeemed_utr(txn_id, user_id, order_id)
            prem = bot_db.activate_user_premium(
                user_id,
                order["plan_key"],
                int(order.get("days", 30)),
                int(order.get("logins", 10))
            )
        except Exception as e:
            print(f"[!] Error updating premium: {e}")
            prem = {"remaining_logins": order.get("logins", 10), "expires_at": "Active"}

        return {
            "success": True,
            "verified": True,
            "message": "Payment verified! Premium activated.",
            "plan": order["plan_key"],
            "remaining_logins": prem.get("remaining_logins", 10),
            "expires_at": prem.get("expires_at", "Active")
        }

    return {
        "success": False,
        "error": msg or "Could not verify UTR. Please ensure payment is completed."
    }


# ==============================================================================
# Shrinkme.io Ad Monetization Engine (1 Ad = 1 Free Login on ALL Services)
# ==============================================================================
AD_SECRET_SALT = os.environ.get("AD_SECRET_SALT", "xdroid_token_salt").strip()
SHRINKME_API_KEY = os.environ.get("SHRINKME_API_KEY", "").strip()
ad_sessions: Dict[str, dict] = {}

def make_ad_signature(token: str) -> str:
    import hmac
    import hashlib
    return hmac.new(AD_SECRET_SALT.encode(), token.encode(), hashlib.sha256).hexdigest()[:16]

@app.post("/api/ads/generate")
async def generate_ad_pass(req: GenerateAdRequest, request: Request):
    """Generates a monetized Shrinkme.io short link to unlock 1 free login for every service."""
    if not SHRINKME_API_KEY:
        return JSONResponse(status_code=500, content={"success": False, "error": "Shrinkme API token not configured."})
    
    site_domain = os.environ.get("SITE_URL", "https://threadore.in").rstrip("/")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    
    # Shrinkme only accepts valid public domains (rejects localhost/127.0.0.1)
    if "localhost" in forwarded_host.lower() or "127.0.0.1" in forwarded_host or not forwarded_host:
        base_url = site_domain
    else:
        forwarded_proto = request.headers.get("x-forwarded-proto", "https")
        base_url = f"{forwarded_proto}://{forwarded_host}"
    
    token = secrets.token_urlsafe(16)
    sig = make_ad_signature(token)
    callback_target = f"{base_url}/api/ads/callback?token={token}&sig={sig}"
    
    api_url = f"https://shrinkme.io/api?api={SHRINKME_API_KEY}&url={urllib.parse.quote(callback_target)}"
    
    try:
        def fetch_shrinkme():
            req_obj = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req_obj, timeout=12) as response:
                return json.loads(response.read().decode())
                
        data = await asyncio.to_thread(fetch_shrinkme)
        if data.get("status") == "success" and data.get("shortenedUrl"):
            short_url = data.get("shortenedUrl")
            ad_sessions[token] = {
                "token": token,
                "email": req.email.lower().strip() if req.email else None,
                "chat_id": str(req.chat_id) if req.chat_id else None,
                "status": "PENDING",
                "created_at": time.time(),
                "expires_at": time.time() + 3600,
                "services": {}
            }
            return {"success": True, "short_url": short_url, "token": token}
        else:
            err_msg = data.get("message")
            if isinstance(err_msg, list):
                err_msg = ", ".join(err_msg)
            return JSONResponse(status_code=502, content={"success": False, "error": err_msg or "Ad gateway busy. Please try again."})
    except Exception as e:
        print(f"[!] Shrinkme API error: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": "Failed to connect to ad shortener. Please try again."})

@app.get("/api/ads/callback")
async def ad_pass_callback(token: str, sig: str, request: Request):
    """Callback triggered by Shrinkme after user completes the ad pages."""
    site_domain = os.environ.get("SITE_URL", "https://threadore.in").rstrip("/")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    if "localhost" in forwarded_host.lower() or "127.0.0.1" in forwarded_host or not forwarded_host:
        base_url = site_domain
    else:
        forwarded_proto = request.headers.get("x-forwarded-proto", "https")
        base_url = f"{forwarded_proto}://{forwarded_host}"
    
    if make_ad_signature(token) != sig:
        return RedirectResponse(f"{base_url}/?ad_error=tampered")
        
    sess = ad_sessions.get(token)
    if not sess:
        return RedirectResponse(f"{base_url}/?ad_error=invalid_token")
        
    if time.time() > sess["expires_at"]:
        return RedirectResponse(f"{base_url}/?ad_error=expired")
        
    # Mark verified & unlock 1 Free Login for EVERY service
    sess["status"] = "VERIFIED"
    all_services = list(SERVICE_CONFIG.keys())
    if "services" not in sess or not isinstance(sess["services"], dict):
        sess["services"] = {}
    for s in all_services:
        sess["services"][s] = sess["services"].get(s, 0) + 1
    
    # Save token in persistent database
    bot_db.save_ad_token(token, sess)
    
    # If user provided email or chat_id, grant directly to profile in db.json
    user_email = sess.get("email")
    user_chat = sess.get("chat_id")
    target_user = user_email or user_chat
    if target_user:
        bot_db.grant_free_service_pass(target_user, all_services)
        
    return RedirectResponse(f"{base_url}/?ad_unlocked=true&token={token}")

@app.get("/api/ads/status")
async def get_ad_pass_status(token: Optional[str] = None, email: Optional[str] = None):
    """Returns active free service logins for user/token."""
    services_balance = {}
    is_active = False
    
    if email:
        db_passes = bot_db.get_free_service_logins(email.lower().strip())
        if db_passes:
            services_balance.update(db_passes)
            if any(v > 0 for v in db_passes.values()):
                is_active = True
                
    if token:
        sess = ad_sessions.get(token) or bot_db.get_ad_token(token)
        if sess and sess.get("status") == "VERIFIED":
            sess_passes = sess.get("services", {})
            for k, v in sess_passes.items():
                services_balance[k] = max(services_balance.get(k, 0), v)
            if any(v > 0 for v in sess_passes.values()):
                is_active = True
                
    return {
        "success": True,
        "is_unlocked": is_active,
        "services": services_balance
    }


@app.post("/api/scan-qr")
async def scan_qr_api(req: ScanQrRequest):
    """
    Decodes QR code from base64 image data using qrserver API
    (same reliable engine used by Telegram bot in main.py).
    """
    try:
        raw_b64 = req.image_base64
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(raw_b64)
        
        def _request_qr(img_b):
            files = {'file': ('qr_image.jpg', io.BytesIO(img_b), 'image/jpeg')}
            return requests.post('https://api.qrserver.com/v1/read-qr-code/', files=files, timeout=12)
            
        res = await asyncio.to_thread(_request_qr, img_bytes)
        if res.status_code == 200:
            data = res.json()
            if data and isinstance(data, list) and data[0].get('symbol'):
                symbols = data[0]['symbol']
                if symbols and symbols[0].get('data'):
                    return {"success": True, "qr_data": symbols[0]['data']}
                
        return {"success": False, "error": "No QR code detected in the image."}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/config")
@app.get("/api/services")
async def get_config():
    """Returns portal configuration, live operational status and cookie availability."""
    services_data = []
    available_gateways = 0

    for svc_key, meta in SERVICE_CONFIG.items():
        count = count_vault_cookies(svc_key)
        is_avail = count > 0
        if is_avail:
            available_gateways += 1

        ads_gate_active = bot_db.is_ads_gate_enabled()
        is_premium_required = ads_gate_active
        is_free = not ads_gate_active

        services_data.append({
            "id": svc_key,
            "name": meta["name"],
            "category": meta["category"],
            "brand_color": meta["brand_color"],
            "icon": meta["icon"],
            "code_length": meta["code_length"],
            "code_type": meta["code_type"],
            "placeholder": meta["placeholder"],
            "description": meta["description"],
            "status_label": "Operational" if is_avail else "High Demand",
            "is_available": is_avail,
            "is_premium_required": is_premium_required,
            "is_free": is_free
        })

    return {
        "status": "success",
        "active_services": f"{available_gateways}/{len(SERVICE_CONFIG)} Services Active",
        "services": services_data
    }


@app.post("/api/activate")
async def activate_tv(req: ActivationRequest, request: Request):
    """Processes TV activation using local tv_engine."""
    service = req.service.lower().strip()
    raw_code = req.tv_code.strip()

    if service not in SERVICE_CONFIG:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": f"Unsupported streaming service: '{service}'"}
        )

    meta = SERVICE_CONFIG[service]

    # Identify user in bot_db if provided
    user_id = None
    if req.chat_id and str(req.chat_id).isdigit():
        user_id = str(req.chat_id)
    elif req.email:
        with bot_db.lock:
            web_users = bot_db.data.get("web_users", {})
            link = web_users.get(req.email.lower().strip())
            if link and str(link.get("chat_id", "")).isdigit():
                user_id = str(link["chat_id"])
            else:
                user_id = req.email.lower().strip()

    # Dynamic Ads Gate Check:
    # If Ads Gate is OFF -> Any user can login freely without any access token!
    # If Ads Gate is ON  -> Strict Access Token (Paid or Ad Pass) required!
    ads_gate_active = bot_db.is_ads_gate_enabled()
    authorized = False
    auth_method = "free"
    ad_sess_to_deduct = None

    if not ads_gate_active:
        # Ads Gate is OFF: Free Access / No Token Needed
        authorized = True
        auth_method = "free"
    else:
        # Ads Gate is ON: Strict Token Enforcement
        # 1. Check Paid Premium Logins
        if user_id:
            prem = bot_db.get_user_premium(user_id)
            if prem and int(prem.get("remaining_logins", 0) or 0) > 0:
                authorized = True
                auth_method = "premium"
                
        # 2. Check Free Service Pass on user profile (db.json)
        if not authorized and user_id:
            user_passes = bot_db.get_free_service_logins(user_id)
            if int(user_passes.get(service, 0) or 0) > 0:
                authorized = True
                auth_method = "ad_pass_db"
                
        # 3. Check Free Service Pass via Ad Token (session/guest/persistent)
        if not authorized and req.ad_token:
            sess = ad_sessions.get(req.ad_token) or bot_db.get_ad_token(req.ad_token)
            if sess and sess.get("status") == "VERIFIED":
                svc_passes = sess.get("services", {})
                if int(svc_passes.get(service, 0) or 0) > 0:
                    authorized = True
                    auth_method = "ad_pass_token"
                    ad_sess_to_deduct = sess
                
        if not authorized:
            return {
                "success": False,
                "need_ad": True,
                "error": f"Access Denied: An active Access Token is required to authorize {meta['name']}. Watch a quick ad to unlock 1 free login on all platforms."
            }

    # Sanitize and validate code
    if service == "jiohotstar":
        cleaned_code = raw_code.strip()
        if len(cleaned_code) < 4:
            return {
                "success": False,
                "error": "Invalid format! Please enter the JioHotstar QR activation link or code."
            }
    elif meta["code_type"] == "numeric":
        cleaned_code = re.sub(r"\D", "", raw_code)
        if len(cleaned_code) != meta["code_length"]:
            return {
                "success": False,
                "error": f"Invalid code format! {meta['name']} requires exactly {meta['code_length']} digits."
            }
    else:
        cleaned_code = re.sub(r"[\s\-_.:,]", "", raw_code).upper()
        if len(cleaned_code) != meta["code_length"]:
            return {
                "success": False,
                "error": f"Invalid code format! {meta['name']} requires exactly {meta['code_length']} alphanumeric characters."
            }

    # Availability check
    if count_vault_cookies(service) == 0:
        return {
            "success": False,
            "error": f"Service is currently at maximum capacity for {meta['name']}. Please try again shortly."
        }

    # Call process_tv_login_service in threadpool to avoid blocking async loop
    try:
        result = await asyncio.to_thread(process_tv_login_service, service, cleaned_code)
    except Exception as e:
        return {
            "success": False,
            "error": "Activation service temporarily unavailable. Please try again."
        }

    if result.get("success"):
        # ONLY DEDUCT WHEN LOGIN IS SUCCESSFUL!
        if auth_method == "premium" and user_id:
            try:
                bot_db.deduct_user_login(user_id, service)
            except Exception as e:
                print(f"[!] Error deducting premium login in bot_db: {e}")
        elif auth_method == "ad_pass_db" and user_id:
            try:
                bot_db.use_free_service_login(user_id, service)
            except Exception as e:
                print(f"[!] Error deducting free service login in bot_db: {e}")
        elif auth_method == "ad_pass_token" and ad_sess_to_deduct:
            try:
                cur = int(ad_sess_to_deduct["services"].get(service, 0) or 0)
                ad_sess_to_deduct["services"][service] = max(0, cur - 1)
                if req.ad_token:
                    bot_db.use_ad_token_login(req.ad_token, service)
            except Exception as e:
                print(f"[!] Error deducting ad token service login: {e}")

        if user_id:
            try:
                bot_db.record_attempt(user_id, True)
            except Exception as e:
                pass

        return {
            "success": True,
            "service": service,
            "service_name": meta["name"],
            "device": result.get("device") or result.get("tv_name") or "Smart TV",
            "plan": result.get("plan", "Premium Plan"),
            "country": result.get("country", "Global"),
            "email": result.get("email", "Protected"),
            "message": f"{meta['name']} successfully paired with your TV device!"
        }
    else:
        if user_id:
            bot_db.record_attempt(user_id, False)
        err = result.get("error") or "TV authorization failed. Your code may have expired or is incorrect."
        if err in ("no_cookies", "all_dead"):
            err = f"{meta['name']} activation gateway is currently busy. Please try again in a few minutes."
        elif err == "Invalid or expired TV code":
            err = "The TV screen code has expired or is incorrect. Please refresh the code on your TV."
        return {
            "success": False,
            "error": err
        }


# Mount static assets
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/favicon.ico")
async def favicon():
    fav_path = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(fav_path):
        return FileResponse(fav_path)
    return JSONResponse(status_code=404, content={"detail": "Not Found"})

@app.get("/health")
async def health_check():
    """Health check endpoint for Render / Uptime monitors."""
    return {
        "status": "healthy",
        "service": "xdroidtv",
        "timestamp": int(time.time())
    }

@app.get("/")
async def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"status": "XDROID TV API Online", "docs": "/docs"})


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"[*] Starting Unified XDROID Server & Telegram Admin Bot on port {port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
