import sys
import os
import re
import io
import json
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
raw_admin_ids = os.environ.get("ADMIN_ID", "").strip()
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
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


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
    if not user or not is_admin(user.id):
        return  # Strictly Admin Only - Non-admins receive zero response

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

def start_telegram_bot_daemon():
    """Starts the embedded Telegram Admin Bot in a dedicated background daemon thread."""
    global _BOT_THREAD_STARTED
    if _BOT_THREAD_STARTED:
        return
    _BOT_THREAD_STARTED = True

    def _run_bot():
        try:
            print("[*] Starting embedded Telegram Admin Bot polling...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            bot_app = ApplicationBuilder().token(BOT_TOKEN).build()
            bot_app.add_handler(CommandHandler("start", cmd_start))
            bot_app.add_handler(CommandHandler("admin", cmd_start))
            bot_app.add_handler(CommandHandler("help", cmd_help))
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
            bot_app.add_handler(CallbackQueryHandler(callback_query_handler))

            bot_app.run_polling(close_loop=False)
        except Exception as e:
            print(f"[!] Telegram Admin Bot encountered error: {e}")

    t = threading.Thread(target=_run_bot, daemon=True, name="telegram_admin_bot")
    t.start()


# ==============================================================================
# FastAPI Lifespan & Application Setup
# ==============================================================================
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




# ==============================================================================
# Telegram Bot UI & Button Helpers
# ==============================================================================
def btn(text: str, callback_data: str = None, url: str = None, style: str = "primary", icon_custom_emoji_id: str = None) -> InlineKeyboardButton:
    kw = {"style": style}
    if icon_custom_emoji_id:
        kw["icon_custom_emoji_id"] = icon_custom_emoji_id
    if url:
        return InlineKeyboardButton(text=text, url=url, api_kwargs=kw)
    return InlineKeyboardButton(text=text, callback_data=callback_data, api_kwargs=kw)



# Bot Global Compatibility Definitions
BASE_DIR = WEB_DIR
PROXY_FILE = os.getenv("PROXY_FILE", os.path.join(WEB_DIR, "proxy.txt") if os.path.exists(os.path.join(WEB_DIR, "proxy.txt")) else os.path.join(ROOT_DIR, "proxy.txt"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
try:
    proxies_list = load_proxies()
except Exception:
    proxies_list = []

def is_admin(user_id):
    if user_id is None:
        return False
    try:
        if int(user_id) in ADMIN_IDS:
            return True
    except:
        pass
    try:
        if str(user_id) in [str(x) for x in ADMIN_IDS]:
            return True
    except:
        pass
    try:
        if 'db' in globals() and db is not None:
            db_admins = db.get_setting("admin_ids", [])
            if int(user_id) in [int(x) for x in db_admins]:
                return True
    except:
        pass
    return False


# ==============================================================================
# Telegram Bot Core Handlers & Logic (Embedded from Bot Core)
# ==============================================================================
# ==========================================
class BotDB:
    def __init__(self, filepath=None):
        self.filepath = filepath or os.path.join(BASE_DIR, "db.json")
        self.lock = threading.Lock()
        self.data = {"users": {}, "settings": {}}
        self.load()

    def restore_from_github(self):
        token = os.getenv("GITHUB_BACKUP_TOKEN", "").strip()
        repo = os.getenv("GITHUB_BACKUP_REPO", "").strip()
        if not token or not repo:
            return False, None

        try:
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "TVLoginBot-AutoRestore"
            }
            url = f"https://api.github.com/repos/{repo}/contents/db.json"
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                raw_b64 = resp.json().get("content", "")
                raw_json = base64.b64decode(raw_b64).decode("utf-8")
                github_data = json.loads(raw_json)
                if isinstance(github_data, dict) and "users" in github_data:
                    return True, github_data
            return False, None
        except Exception as e:
            return False, None

    def load(self):
        with self.lock:
            loaded = False
            local_users_count = 0

            # 1. Try loading from local primary db.json
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "users" in data:
                            self.data = data
                            loaded = True
                            local_users_count = len(self.data.get("users", {}))
                except Exception as e:
                    print(f"Error loading primary database {self.filepath}: {e}")

            # 2. Try loading from local db.json.bak if primary failed or had 0 users
            bak_file = self.filepath + ".bak"
            if (not loaded or local_users_count == 0) and os.path.exists(bak_file):
                try:
                    with open(bak_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "users" in data:
                            bak_users = len(data.get("users", {}))
                            if bak_users > local_users_count:
                                self.data = data
                                loaded = True
                                local_users_count = bak_users
                                print(f"Successfully restored database from backup: {bak_file} ({bak_users} users)")
                except Exception as e:
                    print(f"Error loading backup database {bak_file}: {e}")

            # 3. Automatic Recovery & Sync from GitHub on Restart
            try:
                ok, github_data = self.restore_from_github()
                if ok and isinstance(github_data, dict) and "users" in github_data:
                    gh_users = github_data.get("users", {})
                    gh_count = len(gh_users)
                    # If local is missing, empty, or has fewer users than GitHub, full restore!
                    if not loaded or local_users_count == 0 or gh_count > local_users_count:
                        self.data = github_data
                        loaded = True
                        local_users_count = gh_count
                        print(f"[*] [GitHub DB Auto-Restore] Successfully restored {gh_count} users from GitHub backup on restart!")
                        self.save_unlocked()
                    else:
                        # Smart merge: ensure any user in GitHub exists locally and merge quotas
                        local_users = self.data.setdefault("users", {})
                        merged_any = False
                        for uid, udata in gh_users.items():
                            if uid not in local_users:
                                local_users[uid] = udata
                                merged_any = True
                            else:
                                for svc in ("netflix", "prime", "crunchyroll", "spotify"):
                                    gh_rem = udata.get(f"{svc}_logins_remaining", 0)
                                    loc_rem = local_users[uid].get(f"{svc}_logins_remaining", 0)
                                    if gh_rem > loc_rem:
                                        local_users[uid][f"{svc}_logins_remaining"] = gh_rem
                                        merged_any = True
                                if udata.get("is_premium") and not local_users[uid].get("is_premium"):
                                    local_users[uid]["is_premium"] = True
                                    local_users[uid]["expiry_date"] = udata.get("expiry_date")
                                    local_users[uid]["plan"] = udata.get("plan")
                                    merged_any = True
                        if merged_any:
                            print(f"[*] [GitHub DB Auto-Restore] Synced & merged user premium/quotas from GitHub backup on restart!")
                            self.save_unlocked()
            except Exception as e:
                print(f"[!] [GitHub DB Auto-Restore Exception] {e}")

            if not loaded:
                if not hasattr(self, 'data') or not isinstance(self.data, dict):
                    self.data = {"users": {}, "settings": {}}
                self.save_unlocked()
            elif not os.path.exists(self.filepath):
                self.save_unlocked()

            if "users" not in self.data:
                self.data["users"] = {}
            if "settings" not in self.data:
                self.data["settings"] = {}
            if "sent_messages" not in self.data:
                self.data["sent_messages"] = []
                
            settings = self.data["settings"]
            if "force_join_channels" not in settings:
                settings["force_join_channels"] = []
            if "usage_mode" not in settings:
                settings["usage_mode"] = "both"
            if "premium_plans" not in settings:
                settings["premium_plans"] = {
                    "monthly": {"name": "Monthly", "price": 49.0, "days": 30, "logins": 10, "enabled": True},
                    "quarterly": {"name": "Quarterly", "price": 129.0, "days": 90, "logins": 35, "enabled": True},
                    "yearly": {"name": "Yearly", "price": 399.0, "days": 365, "logins": 150, "enabled": True}
                }
            if "payment_config" not in settings:
                settings["payment_config"] = {
                    "upi_id": "sudhirxd@fam",
                    "payee_name": "Sudhir kumar",
                    "verify_api_url": "https://paymt-verify.vercel.app/api/fampay",
                    "is_enabled": True
                }
            if "paypal_config" not in settings:
                settings["paypal_config"] = {
                    "client_id": os.getenv("PAYPAL_CLIENT_ID", "").strip(),
                    "client_secret": os.getenv("PAYPAL_CLIENT_SECRET", "").strip(),
                    "mode": os.getenv("PAYPAL_MODE", "sandbox"),
                    "currency": "USD",
                    "is_enabled": True,
                    "usd_prices": {
                        "monthly": 1.00,
                        "quarterly": 2.49,
                        "yearly": 5.99
                    }
                }
            if "premium_required" not in settings:
                settings["premium_required"] = True
            if "referral_reward" not in settings:
                settings["referral_reward"] = 25.0
            if "welcome_bonus" not in settings:
                settings["welcome_bonus"] = 0.0
            if "redeemed_utrs" not in self.data:
                self.data["redeemed_utrs"] = {}
            if "orders" not in self.data:
                self.data["orders"] = {}

    def save(self):
        with self.lock:
            self.save_unlocked()

    def save_unlocked(self):
        try:
            tmp_filepath = self.filepath + ".tmp"
            bak_filepath = self.filepath + ".bak"

            with open(tmp_filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4)

            if os.path.exists(self.filepath):
                try:
                    os.replace(self.filepath, bak_filepath)
                except Exception:
                    pass

            os.replace(tmp_filepath, self.filepath)
        except Exception as e:
            print(f"Error saving database: {e}")

    def record_sent_message(self, chat_id, message_id):
        with self.lock:
            if "sent_messages" not in self.data:
                self.data["sent_messages"] = []
            msg_key = f"{chat_id}:{message_id}"
            if not any(f"{m.get('chat_id')}:{m.get('message_id')}" == msg_key for m in self.data["sent_messages"]):
                self.data["sent_messages"].append({
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "sent_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                self.save_unlocked()

    def register_user(self, user_id, username, first_name):
        user_id_str = str(user_id)
        with self.lock:
            welcome = float(self.data.get("settings", {}).get("welcome_bonus", 0.0))
            if user_id_str not in self.data["users"]:
                # New user gets 1-Day Free Trial with 10 logins (usable on any service)
                trial_exp = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                user_record = {
                    "username": username,
                    "first_name": first_name,
                    "verified": False,
                    "balance": welcome,
                    "referred_by": None,
                    "referral_count": 0,
                    "wallet_history": [],
                    "premium": {
                        "is_premium": True,
                        "plan": "trial",
                        "is_trial": True,
                        "expires_at": trial_exp,
                        "remaining_logins": 10,
                        "total_logins_granted": 10,
                        "activated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    },
                    "last_success_dates": {},
                    "stats": {
                        "total_attempts": 0,
                        "successful_logins": 0,
                        "failed_logins": 0,
                        "last_active": None,
                        "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                }
                if welcome > 0:
                    user_record["wallet_history"].append({
                        "type": "welcome_bonus",
                        "amount": welcome,
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "desc": "🎁 Welcome Signup Bonus"
                    })
                self.data["users"][user_id_str] = user_record
                self.save_unlocked()
            else:
                user = self.data["users"][user_id_str]
                if "balance" not in user:
                    user["balance"] = 0.0
                    user["referred_by"] = user.get("referred_by", None)
                    user["referral_count"] = user.get("referral_count", 0)
                    user["wallet_history"] = []
                if "premium" not in user and "subscription" not in user:
                    # Grant 1-day free trial if user never had a subscription
                    trial_exp = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                    user["premium"] = {
                        "is_premium": True,
                        "plan": "trial",
                        "is_trial": True,
                        "expires_at": trial_exp,
                        "remaining_logins": 10,
                        "total_logins_granted": 10,
                        "activated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                if "verified" not in user:
                    user["verified"] = False
                if "last_success_dates" not in user:
                    user["last_success_dates"] = {}
                if "stats" not in user:
                    user["stats"] = {
                        "total_attempts": 0,
                        "successful_logins": 0,
                        "failed_logins": 0,
                        "last_active": None,
                        "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                if user.get("username") != username or user.get("first_name") != first_name:
                    user["username"] = username
                    user["first_name"] = first_name
                self.save_unlocked()

    def get_user_balance(self, user_id):
        user_id_str = str(user_id)
        with self.lock:
            user = self.data.get("users", {}).get(user_id_str)
            if not user:
                return 0.0
            if "balance" not in user:
                user["balance"] = 0.0
                self.save_unlocked()
            return float(user.get("balance", 0.0))

    def add_user_balance(self, user_id, amount, desc="Bonus Added", tx_type="credit"):
        user_id_str = str(user_id)
        with self.lock:
            user = self.data.get("users", {}).get(user_id_str)
            if not user:
                return 0.0
            cur = float(user.get("balance", 0.0))
            new_bal = round(cur + float(amount), 2)
            user["balance"] = new_bal
            hist = user.setdefault("wallet_history", [])
            hist.append({
                "type": tx_type,
                "amount": float(amount),
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "desc": desc
            })
            self.save_unlocked()
            return new_bal

    def deduct_user_balance(self, user_id, amount, desc="Purchase", tx_type="debit"):
        user_id_str = str(user_id)
        with self.lock:
            user = self.data.get("users", {}).get(user_id_str)
            if not user:
                return False, 0.0
            cur = float(user.get("balance", 0.0))
            amt = float(amount)
            if cur < amt:
                return False, cur
            new_bal = round(cur - amt, 2)
            user["balance"] = new_bal
            hist = user.setdefault("wallet_history", [])
            hist.append({
                "type": tx_type,
                "amount": -amt,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "desc": desc
            })
            self.save_unlocked()
            return True, new_bal

    def get_referral_settings(self):
        with self.lock:
            settings = self.data.setdefault("settings", {})
            return {
                "referral_reward": float(settings.get("referral_reward", 25.0)),
                "welcome_bonus": float(settings.get("welcome_bonus", 0.0))
            }

    def set_referral_settings(self, reward=None, welcome=None):
        with self.lock:
            settings = self.data.setdefault("settings", {})
            if reward is not None:
                settings["referral_reward"] = float(reward)
            if welcome is not None:
                settings["welcome_bonus"] = float(welcome)
            self.save_unlocked()

    def process_referral(self, new_user_id, referrer_id):
        new_id_str = str(new_user_id)
        ref_id_str = str(referrer_id)
        with self.lock:
            if new_id_str == ref_id_str:
                return False, 0.0, "self_referral"

            if ref_id_str not in self.data.get("users", {}):
                return False, 0.0, "referrer_not_found"

            new_user = self.data.get("users", {}).get(new_id_str)
            if new_user and new_user.get("referred_by"):
                return False, 0.0, "already_referred"

            reward = float(self.data.get("settings", {}).get("referral_reward", 25.0))
            ref_user = self.data["users"][ref_id_str]
            ref_user["referral_count"] = int(ref_user.get("referral_count", 0)) + 1
            cur_bal = float(ref_user.get("balance", 0.0))
            new_bal = round(cur_bal + reward, 2)
            ref_user["balance"] = new_bal
            hist = ref_user.setdefault("wallet_history", [])
            new_user_name = new_user.get("first_name", f"User {new_id_str}") if new_user else f"User {new_id_str}"
            hist.append({
                "type": "referral_reward",
                "amount": reward,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "desc": f"👥 Referral reward for inviting {new_user_name}"
            })

            if new_user:
                new_user["referred_by"] = ref_id_str

            self.save_unlocked()
            return True, reward, ref_id_str

    def is_user_verified(self, user_id, ttl_seconds=86400):
        user_id_str = str(user_id)
        with self.lock:
            user = self.data["users"].get(user_id_str)
            if not user or not user.get("verified", False):
                return False
            verified_at = user.get("verified_at")
            if not verified_at:
                return False
            try:
                ver_dt = datetime.strptime(verified_at, "%Y-%m-%d %H:%M:%S")
                return (datetime.now() - ver_dt).total_seconds() < ttl_seconds
            except Exception:
                return False

    def set_user_verified(self, user_id, verified=True):
        user_id_str = str(user_id)
        with self.lock:
            if user_id_str in self.data["users"]:
                self.data["users"][user_id_str]["verified"] = verified
                self.data["users"][user_id_str]["verified_at"] = (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S") if verified else None
                )
                self.save_unlocked()

    def record_attempt(self, user_id, success):
        user_id_str = str(user_id)
        with self.lock:
            if user_id_str in self.data["users"]:
                user = self.data["users"][user_id_str]
                stats = user["stats"]
                stats["total_attempts"] += 1
                stats["last_active"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if success:
                    stats["successful_logins"] += 1
                else:
                    stats["failed_logins"] += 1
                self.save_unlocked()

    def get_user(self, user_id):
        user_id_str = str(user_id)
        with self.lock:
            return self.data["users"].get(user_id_str)

    def get_cookie_usage(self, filename):
        with self.lock:
            if "cookie_usage" not in self.data:
                self.data["cookie_usage"] = {}
            return self.data["cookie_usage"].get(filename, 0)

    def increment_cookie_usage(self, filename):
        with self.lock:
            if "cookie_usage" not in self.data:
                self.data["cookie_usage"] = {}
            self.data["cookie_usage"][filename] = self.data["cookie_usage"].get(filename, 0) + 1
            self.save_unlocked()
            return self.data["cookie_usage"][filename]

    def get_stats_summary(self):
        with self.lock:
            total_users = len(self.data["users"])
            return {
                "total_users": total_users
            }

    def get_setting(self, key, default=None):
        with self.lock:
            settings = self.data.get("settings", {})
            return settings.get(key, default)

    def set_setting(self, key, value):
        with self.lock:
            if "settings" not in self.data:
                self.data["settings"] = {}
            self.data["settings"][key] = value
            self.save_unlocked()

    def add_force_join_channel(self, channel):
        with self.lock:
            if "settings" not in self.data:
                self.data["settings"] = {}
            settings = self.data["settings"]
            if "force_join_channels" not in settings:
                settings["force_join_channels"] = []
            
            chan_str = str(channel)
            if chan_str not in [str(c) for c in settings["force_join_channels"]]:
                settings["force_join_channels"].append(channel)
                # Reset verifications
                for user in self.data["users"].values():
                    user["verified"] = False
                self.save_unlocked()
                return True
            return False

    def remove_force_join_channel(self, channel):
        with self.lock:
            if "settings" not in self.data:
                return False
            settings = self.data["settings"]
            if "force_join_channels" not in settings:
                return False
            
            chan_str = str(channel)
            new_list = []
            removed = False
            for c in settings["force_join_channels"]:
                if str(c) == chan_str:
                    removed = True
                else:
                    new_list.append(c)
            
            if removed:
                settings["force_join_channels"] = new_list
                # Reset verifications
                for user in self.data["users"].values():
                    user["verified"] = False
                self.save_unlocked()
                return True
            return False

    def get_user_daily_token_count(self, user_id):
        with self.lock:
            today_str = datetime.now().strftime("%Y-%m-%d")
            user_limits = self.data.setdefault("user_daily_tokens", {})
            user_record = user_limits.get(str(user_id), {})
            if user_record.get("date") != today_str:
                return 0
            return user_record.get("count", 0)

    def increment_user_daily_token_count(self, user_id):
        with self.lock:
            today_str = datetime.now().strftime("%Y-%m-%d")
            user_limits = self.data.setdefault("user_daily_tokens", {})
            user_record = user_limits.get(str(user_id), {})
            if user_record.get("date") != today_str:
                user_limits[str(user_id)] = {"count": 1, "date": today_str}
            else:
                user_record["count"] = user_record.get("count", 0) + 1
                user_limits[str(user_id)] = user_record
            self.save_unlocked()
            return user_limits[str(user_id)]["count"]

    def is_premium_required(self):
        with self.lock:
            return self.data.get("settings", {}).get("premium_required", True)

    def set_premium_required(self, required: bool):
        with self.lock:
            self.data.setdefault("settings", {})["premium_required"] = bool(required)
            self.save_unlocked()

    def get_payment_config(self):
        with self.lock:
            cfg = self.data.get("settings", {}).get("payment_config", {})
            return {
                "upi_id": cfg.get("upi_id", "sudhirxd@fam"),
                "payee_name": cfg.get("payee_name", "Sudhir kumar"),
                "verify_api_url": cfg.get("verify_api_url", "https://paymt-verify.vercel.app/api/fampay"),
                "is_enabled": cfg.get("is_enabled", True)
            }

    def set_payment_config(self, upi_id=None, payee_name=None):
        with self.lock:
            cfg = self.data.setdefault("settings", {}).setdefault("payment_config", {})
            if upi_id is not None:
                cfg["upi_id"] = upi_id.strip()
            if payee_name is not None:
                cfg["payee_name"] = payee_name.strip()
            self.save_unlocked()

    def get_paypal_config(self):
        with self.lock:
            cfg = self.data.get("settings", {}).get("paypal_config", {})
            env_cid = os.getenv("PAYPAL_CLIENT_ID", "").strip()
            env_sec = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()

            # Prioritize .env if variable is present; otherwise check DB setting
            if "PAYPAL_CLIENT_ID" in os.environ and env_cid:
                client_id = env_cid
            else:
                client_id = (cfg.get("client_id") or "").strip()

            if "PAYPAL_CLIENT_SECRET" in os.environ and env_sec:
                client_secret = env_sec
            else:
                client_secret = (cfg.get("client_secret") or "").strip()

            is_configured = bool(client_id and client_secret)
            is_enabled = bool(is_configured and cfg.get("is_enabled", True))

            return {
                "client_id": client_id,
                "client_secret": client_secret,
                "is_configured": is_configured,
                "mode": os.getenv("PAYPAL_MODE", "").strip() or cfg.get("mode") or "sandbox",
                "currency": cfg.get("currency", "USD"),
                "is_enabled": is_enabled,
                "usd_prices": cfg.get("usd_prices", {
                    "monthly": 1.00,
                    "quarterly": 2.49,
                    "yearly": 5.99
                })
            }

    def set_paypal_config(self, client_id=None, client_secret=None, mode=None, currency=None, is_enabled=None, usd_prices=None):
        with self.lock:
            cfg = self.data.setdefault("settings", {}).setdefault("paypal_config", {})
            if client_id is not None:
                cfg["client_id"] = client_id.strip()
            if client_secret is not None:
                cfg["client_secret"] = client_secret.strip()
            if mode is not None:
                cfg["mode"] = mode.strip().lower()
            if currency is not None:
                cfg["currency"] = currency.strip().upper()
            if is_enabled is not None:
                cfg["is_enabled"] = bool(is_enabled)
            if usd_prices is not None and isinstance(usd_prices, dict):
                cfg["usd_prices"] = usd_prices
            self.save_unlocked()

    def get_premium_plans(self):
        with self.lock:
            default_plans = {
                "monthly": {"name": "Monthly", "price": 49.0, "days": 30, "logins": 10, "enabled": True},
                "quarterly": {"name": "Quarterly", "price": 129.0, "days": 90, "logins": 35, "enabled": True},
                "yearly": {"name": "Yearly", "price": 399.0, "days": 365, "logins": 150, "enabled": True}
            }
            plans = self.data.get("settings", {}).get("premium_plans")
            if not plans:
                self.data.setdefault("settings", {})["premium_plans"] = default_plans
                self.save_unlocked()
                return default_plans
            return plans

    def update_premium_plan(self, plan_key, price=None, logins=None, days=None, enabled=None, name=None):
        with self.lock:
            plans = self.data.setdefault("settings", {}).setdefault("premium_plans", {})
            if plan_key not in plans:
                return False
            if price is not None:
                plans[plan_key]["price"] = float(price)
            if logins is not None:
                plans[plan_key]["logins"] = int(logins)
            if days is not None:
                plans[plan_key]["days"] = int(days)
            if enabled is not None:
                plans[plan_key]["enabled"] = bool(enabled)
            if name is not None:
                plans[plan_key]["name"] = str(name)
            self.save_unlocked()
            return True

    def get_user_premium(self, user_id):
        user_id_str = str(user_id)
        with self.lock:
            user = self.data.get("users", {}).get(user_id_str, {})
            premium = user.get("premium")
            if not premium and "subscription" in user:
                sub = user["subscription"]
                if sub.get("expiry"):
                    legacy_rem = max(0, int(sub.get("max_logins", 0)) - int(sub.get("logins_used", 0)))
                    premium = {
                        "is_premium": True,
                        "plan": "custom",
                        "expires_at": sub.get("expiry"),
                        "remaining_logins": legacy_rem * len(SUPPORTED_SERVICES),
                        "service_logins": {s: legacy_rem for s in SUPPORTED_SERVICES},
                        "total_logins_granted": int(sub.get("max_logins", 0))
                    }
                    user["premium"] = premium
                    self.save_unlocked()

            if not premium:
                return {
                    "is_active": False,
                    "plan": None,
                    "expires_at": None,
                    "remaining_logins": 0,
                    "service_logins": {s: 0 for s in SUPPORTED_SERVICES},
                    "total_logins_granted": 0,
                    "is_trial": False
                }

            # 1-Day Free Trial handling (shared pool of 10 logins across any service)
            if premium.get("is_trial") or premium.get("plan") == "trial":
                expires_at_str = premium.get("expires_at")
                remaining_logins = int(premium.get("remaining_logins", 0))
                is_active = False
                if expires_at_str:
                    try:
                        exp_dt = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
                        if exp_dt > datetime.now() and remaining_logins > 0:
                            is_active = True
                    except Exception:
                        pass
                return {
                    "is_active": is_active,
                    "plan": "trial",
                    "is_trial": True,
                    "expires_at": expires_at_str,
                    "remaining_logins": remaining_logins,
                    "service_logins": {s: remaining_logins for s in SUPPORTED_SERVICES},
                    "total_logins_granted": int(premium.get("total_logins_granted", 10))
                }

            expires_at_str = premium.get("expires_at")
            
            # Ensure service_logins is populated for each supported service for full plans
            svc_dict = premium.get("service_logins")
            if not isinstance(svc_dict, dict):
                rem_val = int(premium.get("remaining_logins", 0))
                svc_dict = {s: rem_val for s in SUPPORTED_SERVICES}
                premium["service_logins"] = svc_dict
                self.save_unlocked()
            else:
                updated = False
                fallback_quota = max(0, max(svc_dict.values())) if svc_dict else int(premium.get("remaining_logins", 0))
                for s in SUPPORTED_SERVICES:
                    if s not in svc_dict:
                        svc_dict[s] = fallback_quota
                        updated = True
                if updated:
                    premium["service_logins"] = svc_dict
                    self.save_unlocked()

            total_remaining = sum(svc_dict.values())
            is_active = False
            if expires_at_str:
                try:
                    exp_dt = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
                    if exp_dt > datetime.now() and total_remaining > 0:
                        is_active = True
                except Exception:
                    pass

            return {
                "is_active": is_active,
                "plan": premium.get("plan"),
                "is_trial": False,
                "expires_at": expires_at_str,
                "remaining_logins": total_remaining,
                "service_logins": dict(svc_dict),
                "total_logins_granted": int(premium.get("total_logins_granted", 0))
            }

    def get_user_service_logins(self, user_id, service: str):
        prem = self.get_user_premium(user_id)
        if not prem.get("is_active"):
            return 0
        service_clean = (service or "").lower().strip()
        svc_logins = prem.get("service_logins", {})
        return int(svc_logins.get(service_clean, 0))

    def activate_user_premium(self, user_id, plan_key, days, logins):
        user_id_str = str(user_id)
        with self.lock:
            if user_id_str not in self.data.get("users", {}):
                self.data.setdefault("users", {})[user_id_str] = {
                    "username": "",
                    "first_name": "",
                    "verified": False,
                    "stats": {
                        "total_attempts": 0,
                        "successful_logins": 0,
                        "failed_logins": 0,
                        "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                }
            user = self.data["users"][user_id_str]
            curr_prem = user.get("premium", {})
            curr_exp = curr_prem.get("expires_at")
            is_prev_trial = curr_prem.get("is_trial") or curr_prem.get("plan") == "trial"

            base_dt = datetime.now()
            if curr_exp and not is_prev_trial:
                try:
                    dt = datetime.strptime(curr_exp, "%Y-%m-%d %H:%M:%S")
                    if dt > base_dt:
                        base_dt = dt
                except Exception:
                    pass
            new_exp_dt = base_dt + timedelta(days=int(days))
            new_exp_str = new_exp_dt.strftime("%Y-%m-%d %H:%M:%S")

            # Per-service logins for paid plan (ignore old trial logins if upgrading)
            curr_svc = curr_prem.get("service_logins") if not is_prev_trial else None
            new_svc_logins = {}
            if isinstance(curr_svc, dict):
                for s in SUPPORTED_SERVICES:
                    new_svc_logins[s] = int(curr_svc.get(s, 0)) + int(logins)
            else:
                prev_rem = int(curr_prem.get("remaining_logins", 0)) if (not is_prev_trial and curr_prem.get("remaining_logins", 0) > 0) else 0
                for s in SUPPORTED_SERVICES:
                    new_svc_logins[s] = prev_rem + int(logins)

            total_rem = sum(new_svc_logins.values())
            user["premium"] = {
                "is_premium": True,
                "plan": plan_key,
                "is_trial": False,
                "expires_at": new_exp_str,
                "remaining_logins": total_rem,
                "service_logins": new_svc_logins,
                "total_logins_granted": int(curr_prem.get("total_logins_granted", 0)) + int(logins),
                "activated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self.save_unlocked()
            return user["premium"]

    def add_user_logins(self, user_id, logins, days=30):
        user_id_str = str(user_id)
        with self.lock:
            if user_id_str not in self.data.get("users", {}):
                self.data.setdefault("users", {})[user_id_str] = {
                    "username": "",
                    "first_name": "",
                    "verified": False,
                    "stats": {
                        "total_attempts": 0,
                        "successful_logins": 0,
                        "failed_logins": 0,
                        "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                }
            user = self.data["users"][user_id_str]
            curr_prem = user.get("premium", {})
            curr_exp = curr_prem.get("expires_at")
            is_prev_trial = curr_prem.get("is_trial") or curr_prem.get("plan") == "trial"

            base_dt = datetime.now()
            if curr_exp and not is_prev_trial:
                try:
                    dt = datetime.strptime(curr_exp, "%Y-%m-%d %H:%M:%S")
                    if dt > base_dt:
                        base_dt = dt
                except Exception:
                    pass
            new_exp_dt = base_dt + timedelta(days=int(days))
            new_exp_str = new_exp_dt.strftime("%Y-%m-%d %H:%M:%S")

            curr_svc = curr_prem.get("service_logins") if not is_prev_trial else None
            new_svc_logins = {}
            if isinstance(curr_svc, dict):
                for s in SUPPORTED_SERVICES:
                    new_svc_logins[s] = int(curr_svc.get(s, 0)) + int(logins)
            else:
                prev_rem = int(curr_prem.get("remaining_logins", 0)) if (not is_prev_trial and curr_prem.get("remaining_logins", 0) > 0) else 0
                for s in SUPPORTED_SERVICES:
                    new_svc_logins[s] = prev_rem + int(logins)

            total_rem = sum(new_svc_logins.values())
            plan_name = curr_prem.get("plan") if (curr_prem.get("plan") and not is_prev_trial) else "custom"
            user["premium"] = {
                "is_premium": True,
                "plan": plan_name,
                "is_trial": False,
                "expires_at": new_exp_str,
                "remaining_logins": total_rem,
                "service_logins": new_svc_logins,
                "total_logins_granted": int(curr_prem.get("total_logins_granted", 0)) + int(logins),
                "activated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self.save_unlocked()
            return user["premium"]

    def remove_user_premium(self, user_id):
        user_id_str = str(user_id)
        with self.lock:
            if user_id_str in self.data.get("users", {}):
                self.data["users"][user_id_str]["premium"] = {
                    "is_premium": False,
                    "plan": None,
                    "is_trial": False,
                    "expires_at": None,
                    "remaining_logins": 0,
                    "service_logins": {s: 0 for s in SUPPORTED_SERVICES},
                    "total_logins_granted": 0
                }
                self.save_unlocked()
                return True
            return False

    def deduct_user_login(self, user_id, service: str = None):
        user_id_str = str(user_id)
        with self.lock:
            user = self.data.get("users", {}).get(user_id_str)
            if not user or "premium" not in user:
                return 0
            prem = user["premium"]

            # Trial deduction (shared across any service)
            if prem.get("is_trial") or prem.get("plan") == "trial":
                rem = int(prem.get("remaining_logins", 0))
                if rem > 0:
                    rem -= 1
                    prem["remaining_logins"] = rem
                    self.save_unlocked()
                return rem

            svc_dict = prem.get("service_logins")
            if not isinstance(svc_dict, dict):
                rem_all = int(prem.get("remaining_logins", 0))
                svc_dict = {s: rem_all for s in SUPPORTED_SERVICES}
                prem["service_logins"] = svc_dict

            service_key = (service or "").lower().strip()
            rem = 0
            if service_key and service_key in svc_dict:
                if svc_dict[service_key] > 0:
                    svc_dict[service_key] -= 1
                rem = svc_dict[service_key]
            else:
                # Fallback: deduct from first service with logins remaining
                for s in SUPPORTED_SERVICES:
                    if svc_dict.get(s, 0) > 0:
                        svc_dict[s] -= 1
                        rem = svc_dict[s]
                        break

            prem["remaining_logins"] = sum(svc_dict.values())
            self.save_unlocked()
            return rem


    def is_utr_redeemed(self, utr: str) -> bool:
        with self.lock:
            redeemed = self.data.get("redeemed_utrs", {})
            return str(utr).strip().upper() in redeemed

    def record_redeemed_utr(self, utr: str, user_id, order_id):
        with self.lock:
            redeemed = self.data.setdefault("redeemed_utrs", {})
            redeemed[str(utr).strip().upper()] = {
                "user_id": str(user_id),
                "order_id": order_id,
                "redeemed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self.save_unlocked()

    def create_order(self, order_id, user_id, username, plan_key, amount, logins, days, payment_method="upi", paypal_order_id=None, currency="INR"):
        with self.lock:
            orders = self.data.setdefault("orders", {})
            orders[order_id] = {
                "order_id": order_id,
                "user_id": str(user_id),
                "username": username or "",
                "plan_key": plan_key,
                "amount": float(amount),
                "logins": int(logins),
                "days": int(days),
                "status": "pending",
                "payment_method": payment_method,
                "paypal_order_id": paypal_order_id,
                "currency": currency,
                "utr": None,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "processed_at": None
            }
            self.save_unlocked()
            return orders[order_id]

    def get_order(self, order_id):
        with self.lock:
            return self.data.get("orders", {}).get(order_id)

    def update_order_status(self, order_id, status, utr=None):
        with self.lock:
            order = self.data.get("orders", {}).get(order_id)
            if order:
                order["status"] = status
                if utr:
                    order["utr"] = utr
                order["processed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.save_unlocked()
                return order
            return None


def generate_order_id() -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    chars = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(6))
    return f"SX-{date_str}-{suffix}"


def generate_payment_qr(upi_id: str, payee_name: str, amount: float, order_id: str) -> io.BytesIO:
    if not qrcode:
        raise RuntimeError("qrcode library is not installed")
    encoded_pn = urllib.parse.quote(payee_name)
    encoded_tn = urllib.parse.quote(order_id)
    upi_uri = f"upi://pay?pa={upi_id}&pn={encoded_pn}&am={amount:.2f}&cu=INR&tn={encoded_tn}"

    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=3
    )
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
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            return False, f"Server HTTP {e.code}", None
    except Exception as e:
        return False, f"Connection error: {e}", None

    if data.get("verified") is True:
        results = data.get("results", [])
        detail = results[0] if results else {}
        return True, "Payment verified successfully.", detail

    msg = data.get("verification_message") or "Payment not found or not verified yet."
    return False, msg, data


def verify_fampay_payment(utr: str, expected_amount: float) -> tuple[bool, str, dict]:
    cleaned_utr = utr.strip().upper()
    url = f"https://paymt-verify.vercel.app/api/fampay?utr={urllib.parse.quote(cleaned_utr)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            return False, f"Verification service HTTP {e.code}", None
    except Exception as e:
        return False, f"Verification service error: {e}", None

    if data.get("verified") is True or data.get("found") is True:
        results = data.get("results", [])
        for r in results:
            amount_num = float(r.get("amount_num", 0) or 0)
            status = str(r.get("payment", "")).strip().lower()
            if (status == "success" or r.get("verified") is True) and amount_num >= expected_amount:
                return True, f"Payment verified! (₹{amount_num:.2f})", r
        if results and data.get("verified") is True:
            return True, "Payment verified!", results[0]
        return False, f"Payment found but amount or status did not match (Expected: ₹{expected_amount:.2f}).", data

    return False, data.get("verification_message") or "Payment not found yet. Please ensure payment is successful and try again.", data


ACTIVE_PAYMENT_TASKS: dict[str, asyncio.Task] = {}


async def auto_check_payment_loop(bot, chat_id: int, message_id: int, order_id: str, user_id: int, first_name: str, username: str):
    # Check every 5 seconds for 5 minutes (60 iterations = 300 seconds)
    max_checks = 60
    check_count = 0
    is_verified = False
    try:
        while check_count < max_checks:
            await asyncio.sleep(5)
            check_count += 1
            order = db.get_order(order_id)
            if not order:
                break
            if order.get("status") != "pending":
                if order.get("status") == "completed":
                    is_verified = True
                break

            amount = float(order.get("amount", 0))
            verified, msg, detail = await asyncio.to_thread(verify_fampay_order_payment, order_id, amount)
            if verified:
                is_verified = True
                txn_id = detail.get("txn_id", "")
                db.update_order_status(order_id, "completed", utr=txn_id)
                if txn_id:
                    db.record_redeemed_utr(txn_id, user_id, order_id)
                prem = db.activate_user_premium(
                    user_id,
                    order["plan_key"],
                    int(order.get("days", 30)),
                    int(order.get("logins", 10))
                )

                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception:
                    pass

                success_text = (
                    "<b><i>🎉 Payment Verified Successfully!\n\n"
                    "💎 Your Premium Subscription is now ACTIVE! ⚡\n\n"
                    f"• Plan: {order['plan_key'].capitalize()}\n"
                    f"• Validity Added: +{order['days']} Days (Until: <code>{prem['expires_at']}</code>)\n"
                    f"• Logins Added: +{order['logins']} Logins / Service 🎟️ (Total: {prem['remaining_logins']} across all services)\n"
                    f"• Order ID: <code>{order_id}</code>\n"
                    f"• Txn ID: <code>{txn_id}</code>\n\n"
                    "🚀 You can now activate any TV service or generate Netflix login links instantly!</i></b>"
                )
                keyboard = [
                    [btn(text="📺 Activate TV Now", callback_data="menu_activate", style="success")],
                    [btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")]
                ]
                await bot.send_message(
                    chat_id=chat_id,
                    text=success_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

                user_handle = f"@{username}" if username else "No username"
                admin_notif = (
                    "🔔 <b>[Auto-Payment Verified]</b>\n\n"
                    f"👤 <b>User:</b> {first_name} ({user_handle} | <code>{user_id}</code>)\n"
                    f"📦 <b>Plan:</b> {order['plan_key'].capitalize()} (₹{amount:.2f})\n"
                    f"🆔 <b>Order:</b> <code>{order_id}</code>\n"
                    f"🧾 <b>Txn ID:</b> <code>{txn_id}</code>\n"
                    "⚡ <b>Status:</b> Auto-Activated ✅"
                )
                for adm_id in ADMIN_IDS:
                    try:
                        await bot.send_message(chat_id=adm_id, text=admin_notif, parse_mode=ParseMode.HTML)
                    except Exception:
                        pass
                break

        # If 5-minute window expired without payment verification
        if not is_verified:
            order = db.get_order(order_id)
            if order and order.get("status") == "pending":
                db.update_order_status(order_id, "failed")
                failed_text = (
                    "<b><i>❌ Payment Failed / QR Expired\n\n"
                    f"⚠️ Order ID: <code>{order_id}</code>\n"
                    f"⏳ The 5-minute validity window for this QR code has expired.\n\n"
                    f"📌 <b>Agar aapne payment kar diya hai:</b>\n"
                    f"👉 Neeche '✍️ Submit UTR / Txn ID' button par click karke apna UTR submit karein.\n\n"
                    f"🔄 Naya QR generate karne ke liye plans menu par jayein.</i></b>"
                )
                failed_keyboard = [
                    [btn(text="✍️ Submit UTR / Txn ID", callback_data=f"submit_utr:{order_id}", style="success")],
                    [btn(text="🔄 Generate New QR", callback_data="menu_premium", style="primary")],
                    [btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")]
                ]
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                    await bot.send_message(
                        chat_id=chat_id,
                        text=failed_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(failed_keyboard)
                    )
                except Exception:
                    try:
                        await bot.edit_message_caption(
                            chat_id=chat_id,
                            message_id=message_id,
                            caption=failed_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup(failed_keyboard)
                        )
                    except Exception:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=failed_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup(failed_keyboard)
                        )
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[!] Error in auto_check_payment_loop for {order_id}: {e}")
    finally:
        ACTIVE_PAYMENT_TASKS.pop(order_id, None)


# ==========================================
# PayPal REST API Client & Automation
# ==========================================
class PayPalClient:
    def __init__(self, client_id: str = None, client_secret: str = None, sandbox: bool = None):
        cfg = db.get_paypal_config() if 'db' in globals() and db is not None else {}
        self.client_id = client_id or cfg.get("client_id", "")
        self.client_secret = client_secret or cfg.get("client_secret", "")
        if sandbox is not None:
            self.sandbox = sandbox
        else:
            self.sandbox = (cfg.get("mode", "sandbox").lower() == "sandbox")
        self.base_url = "https://api-m.sandbox.paypal.com" if self.sandbox else "https://api-m.paypal.com"
        self._access_token = None
        self._token_expires_at = 0
        self._lock = threading.Lock()

    def get_access_token(self) -> str:
        with self._lock:
            now = time.time()
            if self._access_token and now < (self._token_expires_at - 60):
                return self._access_token

            url = f"{self.base_url}/v1/oauth2/token"
            try:
                resp = requests.post(
                    url,
                    auth=(self.client_id, self.client_secret),
                    data={"grant_type": "client_credentials"},
                    headers={"Accept": "application/json", "Accept-Language": "en_US"},
                    timeout=15
                )
                if resp.status_code != 200:
                    print(f"[PayPal] Failed to fetch access token: {resp.status_code} {resp.text}")
                    return None
                data = resp.json()
                self._access_token = data.get("access_token")
                expires_in = int(data.get("expires_in", 3600))
                self._token_expires_at = now + expires_in
                return self._access_token
            except Exception as e:
                print(f"[PayPal] Exception in get_access_token: {e}")
                return None

    def create_order(self, amount: float, currency: str = "USD", description: str = "", custom_id: str = "") -> dict:
        token = self.get_access_token()
        if not token:
            return {"success": False, "error": "Failed to authenticate with PayPal API"}

        url = f"{self.base_url}/v2/checkout/orders"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "description": description or "TV Login Premium Plan",
                    "custom_id": custom_id,
                    "amount": {
                        "currency_code": currency.upper(),
                        "value": f"{amount:.2f}"
                    }
                }
            ],
            "application_context": {
                "brand_name": os.getenv("PAYPAL_BRAND_NAME", "TV Login Bot").strip(),
                "landing_page": "NO_PREFERENCE",
                "user_action": "PAY_NOW",
                "return_url": os.getenv("PAYPAL_RETURN_URL", "https://t.me").strip(),
                "cancel_url": os.getenv("PAYPAL_CANCEL_URL", "https://t.me").strip()
            }
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            data = resp.json()
            if resp.status_code in (200, 201):
                order_id = data.get("id")
                checkout_url = None
                for link in data.get("links", []):
                    if link.get("rel") == "approve":
                        checkout_url = link.get("href")
                        break
                return {
                    "success": True,
                    "order_id": order_id,
                    "checkout_url": checkout_url,
                    "raw": data
                }
            else:
                return {"success": False, "error": data.get("message", f"HTTP {resp.status_code}"), "raw": data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def capture_order(self, paypal_order_id: str) -> tuple[bool, str, dict]:
        token = self.get_access_token()
        if not token:
            return False, "Failed to authenticate with PayPal", None

        url = f"{self.base_url}/v2/checkout/orders/{paypal_order_id}/capture"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        try:
            resp = requests.post(url, headers=headers, timeout=15)
            data = resp.json()
            if resp.status_code in (200, 201):
                status = data.get("status", "").upper()
                if status == "COMPLETED":
                    capture_id = ""
                    try:
                        captures = data.get("purchase_units", [{}])[0].get("payments", {}).get("captures", [])
                        if captures:
                            capture_id = captures[0].get("id", "")
                    except Exception:
                        pass
                    return True, "Payment completed successfully", {"capture_id": capture_id, "data": data}
                return False, f"Order status is {status}", data

            if resp.status_code == 422:
                issue = ""
                try:
                    details = data.get("details", [])
                    if details:
                        issue = details[0].get("issue", "")
                except Exception:
                    pass
                if issue == "ORDER_ALREADY_CAPTURED":
                    status_ok, status_val, order_data = self.get_order_status(paypal_order_id)
                    if status_ok and status_val == "COMPLETED":
                        return True, "Order already captured and completed", {"data": order_data}
                return False, f"Order issue: {issue or data.get('message')}", data

            return False, data.get("message", f"HTTP {resp.status_code}"), data
        except Exception as e:
            return False, str(e), None

    def get_order_status(self, paypal_order_id: str) -> tuple[bool, str, dict]:
        token = self.get_access_token()
        if not token:
            return False, "Failed to authenticate with PayPal", None

        url = f"{self.base_url}/v2/checkout/orders/{paypal_order_id}"
        headers = {
            "Authorization": f"Bearer {token}"
        }
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            data = resp.json()
            if resp.status_code == 200:
                return True, data.get("status", "").upper(), data
            return False, data.get("message", f"HTTP {resp.status_code}"), data
        except Exception as e:
            return False, str(e), None


async def auto_check_paypal_payment_loop(
    bot,
    chat_id: int,
    message_id: int,
    bot_order_id: str,
    paypal_order_id: str,
    user_id: int,
    first_name: str,
    username: str,
    plan_key: str,
    usd_amount: float
):
    # Check every 6 seconds for 10 minutes (100 iterations = 600 seconds)
    max_checks = 100
    check_count = 0
    is_verified = False
    paypal_client = PayPalClient()

    try:
        while check_count < max_checks:
            await asyncio.sleep(6)
            check_count += 1
            order = db.get_order(bot_order_id)
            if not order:
                break
            if order.get("status") != "pending":
                if order.get("status") == "completed":
                    is_verified = True
                break

            # 1. Check order status on PayPal
            success, status, data = await asyncio.to_thread(paypal_client.get_order_status, paypal_order_id)
            if success:
                if status == "APPROVED":
                    # Buyer approved the order on PayPal! Capture payment now.
                    captured, cap_msg, cap_data = await asyncio.to_thread(paypal_client.capture_order, paypal_order_id)
                    if captured:
                        status = "COMPLETED"
                        data = cap_data

                if status == "COMPLETED":
                    is_verified = True
                    capture_id = paypal_order_id
                    try:
                        captures = data.get("purchase_units", [{}])[0].get("payments", {}).get("captures", [])
                        if captures:
                            capture_id = captures[0].get("id", paypal_order_id)
                    except Exception:
                        pass

                    db.update_order_status(bot_order_id, "completed", utr=capture_id)
                    prem = db.activate_user_premium(
                        user_id,
                        order["plan_key"],
                        int(order.get("days", 30)),
                        int(order.get("logins", 10))
                    )

                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=message_id)
                    except Exception:
                        pass

                    success_text = (
                        "<b><i>🎉 PayPal Payment Verified Successfully!\n\n"
                        "💎 Your Premium Subscription is now ACTIVE! ⚡\n\n"
                        f"• Plan: {order['plan_key'].capitalize()}\n"
                        f"• Validity Added: +{order['days']} Days (Until: <code>{prem['expires_at']}</code>)\n"
                        f"• Logins Added: +{order['logins']} Logins 🎟️ (Total: {prem['remaining_logins']})\n"
                        f"• Order ID: <code>{bot_order_id}</code>\n"
                        f"• PayPal Txn ID: <code>{capture_id}</code>\n\n"
                        "🚀 You can now activate any TV service or generate Netflix login links instantly!</i></b>"
                    )
                    keyboard = [
                        [btn(text="📺 Activate TV Now", callback_data="menu_activate", style="success")],
                        [btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")]
                    ]
                    await bot.send_message(
                        chat_id=chat_id,
                        text=success_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )

                    user_handle = f"@{username}" if username else "No username"
                    admin_notif = (
                        "🔔 <b>[PayPal Auto-Payment Verified]</b>\n\n"
                        f"👤 <b>User:</b> {first_name} ({user_handle} | <code>{user_id}</code>)\n"
                        f"📦 <b>Plan:</b> {order['plan_key'].capitalize()} (${usd_amount:.2f} USD)\n"
                        f"🆔 <b>Order:</b> <code>{bot_order_id}</code>\n"
                        f"🧾 <b>PayPal Txn ID:</b> <code>{capture_id}</code>\n"
                        "⚡ <b>Status:</b> Auto-Activated ✅"
                    )
                    for adm_id in ADMIN_IDS:
                        try:
                            await bot.send_message(chat_id=adm_id, text=admin_notif, parse_mode=ParseMode.HTML)
                        except Exception:
                            pass
                    break

        if not is_verified:
            order = db.get_order(bot_order_id)
            if order and order.get("status") == "pending":
                db.update_order_status(bot_order_id, "expired")
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=(
                            f"⌛ <b>PayPal Payment Session Expired</b>\n\n"
                            f"Order ID <code>{bot_order_id}</code> was not completed within 10 minutes.\n"
                            f"If you still wish to upgrade, please initiate a new checkout from the Premium Plans menu."
                        ),
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([
                            [btn(text="💎 View Premium Plans", callback_data="menu_premium", style="primary")],
                            [btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")]
                        ])
                    )
                except Exception:
                    pass

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[!] PayPal loop error for order {bot_order_id}: {e}")
    finally:
        ACTIVE_PAYMENT_TASKS.pop(bot_order_id, None)



def user_has_access(user_id: int, service: str = None) -> tuple[bool, str]:
    if is_admin(user_id):
        return True, ""
    if not db.is_premium_required():
        return True, ""
    prem = db.get_user_premium(user_id)

    expires_at_str = prem.get("expires_at")
    if not expires_at_str:
        return False, "not_premium"
    
    try:
        exp_dt = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
        if exp_dt <= datetime.now():
            return False, "expired"
    except Exception:
        return False, "expired"
    
    # Trial check: shared pool across any service
    if prem.get("is_trial"):
        if prem.get("remaining_logins", 0) <= 0:
            return False, "trial_exhausted"
        return True, ""

    if service:
        service_clean = str(service).lower().strip()
        svc_rem = db.get_user_service_logins(user_id, service_clean)
        if svc_rem <= 0:
            return False, "quota_exhausted"
    else:
        # General check: allow if ANY service still has logins available
        if prem["remaining_logins"] <= 0:
            return False, "quota_exhausted"
    return True, ""


async def send_premium_required_message(query_or_msg, context, reason="not_premium", service: str = None):
    svc_label = (service or "").upper()
    if service == "hbomax":
        svc_label = "HBO MAX"
    elif service == "nftoken":
        svc_label = "NETFLIX DIRECT LINK"

    if reason == "trial_exhausted":
        title = "⏳ <b>Free Trial 10 Logins Used!</b>"
        desc = (
            "Aapne apne <b>1-Day Free Trial ke 10 Logins</b> poore use kar liye hain.\n\n"
            "👥 <b>Free me TV Logins continue karne ke liye:</b>\n"
            "👉 Apne dosto ko bot refer karein aur wallet cash kamayein!\n"
            "👉 Wallet balance se aap instant full Premium plan activate kar sakte hain!"
        )
    elif reason == "quota_exhausted":
        if svc_label:
            title = f"⚠️ <b>{svc_label} Login Quota Exhausted!</b>"
            desc = (
                f"You have used all login tokens for <b>{svc_label}</b> in your current subscription.\n\n"
                "<i>(Tip: Other services in your plan may still have available logins!)</i>\n\n"
                "Please recharge or upgrade your plan to get more logins for this service."
            )
        else:
            title = "⚠️ <b>Login Quota Exhausted!</b>"
            desc = (
                "You have used all login tokens included in your current subscription.\n\n"
                "Please recharge or upgrade your plan to continue activating TV services."
            )
    elif reason == "expired":
        title = "⏳ <b>Subscription Expired!</b>"
        desc = (
            "Your premium subscription has expired.\n\n"
            "Renew your subscription to regain instant access to all streaming TV logins."
        )
    else:
        title = "🔒 <b>Premium Access Required!</b>"
        desc = (
            "This bot is exclusive to <b>Premium Members</b>.\n\n"
            "⚡ <b>What you get with Premium:</b>\n"
            "• Instant TV Activation (Netflix, Crunchyroll, HBO Max, Surfshark, Spotify, JioHotstar)\n"
            "• High-speed working vault cookies\n"
            "• NFToken login link generator\n"
            "• Priority support & instant activation\n\n"
            "Select a plan below to activate your access instantly:"
        )

    keyboard = [
        [btn(text="💎 View Premium Plans", callback_data="menu_premium", style="success")],
        [btn(text="👥 Refer & Earn Free Logins", callback_data="menu_referral", style="primary")],
        [btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"{title}\n\n{desc}"

    if hasattr(query_or_msg, "callback_query") and query_or_msg.callback_query is not None:
        await safe_edit_message(query_or_msg.callback_query, context, text, reply_markup)
    elif hasattr(query_or_msg, "edit_message_text"):
        await query_or_msg.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    else:
        msg = getattr(query_or_msg, "message", query_or_msg)
        await msg.reply_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


db = BotDB()

import telegram

# Monkey patch class telegram.Bot.send_message
_original_send_message = telegram.Bot.send_message
async def _wrapped_send_message(self, *args, **kwargs):
    args = list(args)
    text = None
    text_idx = None
    if len(args) > 1:
        text = args[1]
        text_idx = 1
    elif 'text' in kwargs:
        text = kwargs['text']
        
    if text and isinstance(text, str):
        t = text.strip()
        wrapped_text = text if (t.startswith("<b><i>") and t.endswith("</i></b>")) else f"<b><i>{text}</i></b>"
        if text_idx is not None:
            args[text_idx] = wrapped_text
        else:
            kwargs['text'] = wrapped_text
        kwargs['parse_mode'] = 'HTML'
        
    try:
        msg = await _original_send_message(self, *args, **kwargs)
    except telegram.error.BadRequest as e:
        if "Message to be replied not found" in str(e) and 'reply_to_message_id' in kwargs:
            kwargs.pop('reply_to_message_id', None)
            msg = await _original_send_message(self, *args, **kwargs)
        else:
            raise e

    try:
        db.record_sent_message(msg.chat.id, msg.message_id)
    except Exception:
        pass
    return msg
telegram.Bot.send_message = _wrapped_send_message

# Monkey patch class telegram.Bot.edit_message_text
_original_edit_message_text = telegram.Bot.edit_message_text
async def _wrapped_edit_message_text(self, *args, **kwargs):
    args = list(args)
    text = None
    text_idx = None
    if len(args) > 0:
        text = args[0]
        text_idx = 0
    elif 'text' in kwargs:
        text = kwargs['text']
        
    if text and isinstance(text, str):
        t = text.strip()
        wrapped_text = text if (t.startswith("<b><i>") and t.endswith("</i></b>")) else f"<b><i>{text}</i></b>"
        if text_idx is not None:
            args[text_idx] = wrapped_text
        else:
            kwargs['text'] = wrapped_text
        kwargs['parse_mode'] = 'HTML'
        
    try:
        res = await _original_edit_message_text(self, *args, **kwargs)
        try:
            from telegram import Message
            if isinstance(res, Message):
                db.record_sent_message(res.chat.id, res.message_id)
            elif "chat_id" in kwargs and "message_id" in kwargs:
                db.record_sent_message(kwargs["chat_id"], kwargs["message_id"])
        except Exception:
            pass
        return res
    except telegram.error.BadRequest as e:
        if "message is not modified" in str(e).lower():
            return True
        raise e
telegram.Bot.edit_message_text = _wrapped_edit_message_text

cookie_lock = threading.Lock()
stats_lock = threading.Lock()

_DEFAULT_STATS = {
    "total_logins": 0,
    "successful": 0,
    "failed": 0,
    "codes_rejected": 0,
    "nftoken": {"successful": 0, "failed": 0},
    "nftoken_generated": 0,
    "nftoken_failed": 0,
    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}

# Load stats from db so they survive restarts — no more data vanishing acts
def _load_persisted_stats():
    saved = db.get_setting("bot_stats", None)
    if saved and isinstance(saved, dict):
        merged = dict(_DEFAULT_STATS)
        merged.update(saved)
        if "nftoken" not in merged or not isinstance(merged["nftoken"], dict):
            merged["nftoken"] = {
                "successful": merged.get("nftoken_generated", 0),
                "failed": merged.get("nftoken_failed", 0)
            }
        else:
            gen_val = merged.get("nftoken_generated", 0)
            fail_val = merged.get("nftoken_failed", 0)
            if gen_val > merged["nftoken"].get("successful", 0):
                merged["nftoken"]["successful"] = gen_val
            if fail_val > merged["nftoken"].get("failed", 0):
                merged["nftoken"]["failed"] = fail_val
        return merged
    res = dict(_DEFAULT_STATS)
    res["nftoken"] = {"successful": 0, "failed": 0}
    return res

stats = _load_persisted_stats()

def _save_stats():
    """Persist current stats to db.json — called after every stat mutation."""
    try:
        db.set_setting("bot_stats", dict(stats))
    except Exception:
        pass

def clear_stats():
    """Nuclear option — wipe all stats back to zero."""
    with stats_lock:
        for key in list(stats.keys()):
            if key == "started_at":
                stats[key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(stats[key], dict):
                stats[key] = {"successful": 0, "failed": 0}
            else:
                stats[key] = 0
        stats["nftoken"] = {"successful": 0, "failed": 0}
        _save_stats()


async def check_force_join_single(bot, user_id, channel):
    try:
        chat_id = channel
        if isinstance(chat_id, str) and (chat_id.startswith('-') and chat_id[1:].isdigit() or chat_id.isdigit()):
            chat_id = int(chat_id)
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        if member.status in ('creator', 'administrator', 'member', 'restricted'):
            return True
        return False
    except Exception as e:
        err_msg = str(e).lower()
        if "user not found" in err_msg or "user_id_invalid" in err_msg:
            return False
        print(f"Error checking channel membership for {channel}: {e}")
        return True


async def get_channel_invite_link(bot, channel):
    try:
        chat_id = channel
        if isinstance(chat_id, str) and (chat_id.startswith('-') and chat_id[1:].isdigit() or chat_id.isdigit()):
            chat_id = int(chat_id)
        chat = await bot.get_chat(chat_id=chat_id)
        if chat.invite_link:
            return chat.invite_link
        if chat.username:
            return f"https://t.me/{chat.username}"
        # Fallback for private channel without public username
        try:
            link = await bot.export_chat_invite_link(chat_id=chat_id)
            if link:
                return link
        except Exception:
            pass
    except Exception:
        pass
    if isinstance(channel, str) and channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"
    return None


async def check_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_admin(user.id):
        return True

    channels = db.get_setting("force_join_channels", [])
    if not channels:
        return True

    # Check if verified within last 24 hours (1-day TTL)
    if db.is_user_verified(user.id, ttl_seconds=86400):
        return True

    not_joined = []
    for chan in channels:
        joined = await check_force_join_single(context.bot, user.id, chan)
        if not joined:
            not_joined.append(chan)

    if not_joined:
        keyboard = []
        for chan in not_joined:
            link = await get_channel_invite_link(context.bot, chan)
            name = chan if isinstance(chan, str) else f"Channel ({chan})"
            if link:
                keyboard.append([InlineKeyboardButton(text=f"📢 Join {name}", url=link)])

        keyboard.append([btn(text="🔄 Verify Membership", callback_data="verify_force_join", style="success")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        message = update.message if update.message else update.callback_query.message
        await message.reply_text(
            "⚠️ <b>Access Denied!</b>\n\n"
            "You must join our channel(s) before you can use this bot. Click the button(s) below to join, then click <b>Verify Membership</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
        return False

    # Save 24h verification status to DB
    db.set_user_verified(user.id, True)
    return True


def check_chat_mode(update: Update):
    user_id = update.effective_user.id
    if is_admin(user_id):
        return True, None
        
    chat = update.effective_chat
    if not chat:
        return True, None
    chat_type = chat.type
    mode = db.get_setting("usage_mode", "both")
    
    if mode == "private_only" and chat_type != "private":
        return False, None
    elif mode == "group_only" and chat_type not in ("group", "supergroup"):
        group_link = db.get_setting("group_link")
        group_link_str = ""
        if group_link:
            group_link_str = f"\n\n💬 Join and use the bot in our group here:\n👉 <a href='{group_link}'>Our Official Group Link</a>"
        else:
            group_link_str = "\n\n💬 Please use the bot inside our official group."
            
        return False, f"❌ This bot can only be used inside <b>group chats</b>.{group_link_str}"
        
    return True, None


class ProxyEntry(dict):
    """Proxy dictionary compatible with requests/cloudscraper, with metadata for country and raw line."""
    def __init__(self, http_url, https_url=None, country=None, raw_line=None):
        https_url = https_url or http_url
        super().__init__({"http": http_url, "https": https_url})
        self.country = (country or "").upper()
        self.raw_line = raw_line or ""


PROXY_COUNTRY_CACHE = {}


def extract_country_tag(line):
    """Extracts country code from line comment/tag or proxy credentials."""
    # 1. Trailing or tag e.g. #IN, [IN], |IN, :IN
    tag_m = re.search(r'(?:[#|\[\s]|:(?=[A-Za-z]{2}$))([A-Za-z]{2,5})[\]\s]*$', line)
    if tag_m:
        tag = tag_m.group(1).upper()
        if tag in ("IN", "INDIA"):
            return "IN"
        if tag in ("US", "USA"):
            return "US"
        if tag in ("GB", "UK"):
            return "GB"
        if len(tag) == 2:
            return tag
    # 2. Check credentials or host for country markers e.g. -country-in, region-in, cc-in
    m = re.search(r'[-_](?:country|region|zone|cc)[-_=]([a-zA-Z]{2})\b', line, re.I)
    if m:
        return m.group(1).upper()
    return None


def parse_proxy_line(line):
    orig_line = line.strip()
    if not orig_line or orig_line.startswith("#"):
        return None

    country_tag = extract_country_tag(orig_line)

    # Clean trailing tags
    clean_line = re.sub(r'\s*[#|\[].*$', '', orig_line).strip()

    # Handle 5-part colon format: host:port:user:pass:IN
    parts = clean_line.split(":")
    if len(parts) == 5 and len(parts[4]) == 2 and not parts[4].isdigit():
        country_tag = country_tag or parts[4].upper()
        clean_line = ":".join(parts[:4])

    clean_line = re.sub(r"^([a-zA-Z][a-zA-Z0-9+.-]*):/+", r"\1://", clean_line)
    clean_line = re.sub(r"\s+", " ", clean_line).strip()

    res = None
    m = re.match(
        r"^(?P<scheme>https?|socks5h?|socks4a?)://"
        r"(?:(?P<user>[^:@\s]+):(?P<password>[^@\s]+)@)?"
        r"(?P<host>\[[^\]]+\]|[^:\s]+):(?P<port>\d+)$", clean_line, re.IGNORECASE)
    if m:
        d = m.groupdict()
        host = d["host"].strip().strip("[]")
        url = f"{d['scheme']}://{d['user']}:{d['password']}@{host}:{d['port']}" if d.get("user") else f"{d['scheme']}://{host}:{d['port']}"
        res = (url, url)
    if not res:
        m = re.match(r"^(?P<user>[^:@\s]+):(?P<password>[^@\s]+)@(?P<host>[^:\s]+):(?P<port>\d+)$", clean_line)
        if m:
            d = m.groupdict()
            u = f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}"
            res = (u, u)
    if not res:
        m = re.match(r"^(?P<host>[^:\s]+):(?P<port>\d+)@(?P<user>[^:@\s]+):(?P<password>[^@\s]+)$", clean_line)
        if m:
            d = m.groupdict()
            u = f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}"
            res = (u, u)
    if not res:
        m = re.match(r"^(?P<host>[^:\s]+):(?P<port>\d+)$", clean_line)
        if m:
            d = m.groupdict()
            u = f"http://{d['host']}:{d['port']}"
            res = (u, u)
    if not res:
        parts = clean_line.split(":")
        if len(parts) == 4:
            a, b, c, d = parts
            if b.isdigit() and not d.isdigit():
                u = f"http://{c}:{d}@{a}:{b}"
                res = (u, u)
            elif d.isdigit() and not b.isdigit():
                u = f"http://{a}:{b}@{c}:{d}"
                res = (u, u)
    if not res:
        for sep in (r"\s+", r"\|", r";", r","):
            m = re.match(rf"^(?P<host>[^:\s]+):(?P<port>\d+){sep}(?P<user>[^:\s]+):(?P<password>\S+)$", clean_line)
            if m:
                d = m.groupdict()
                u = f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}"
                res = (u, u)
                break

    if res:
        return ProxyEntry(res[0], res[1], country=country_tag, raw_line=orig_line)
    return None


def get_proxy_country(p):
    """Returns detected country code ('IN', 'US', etc.) or 'UNKNOWN'."""
    if hasattr(p, 'country') and p.country:
        return p.country
    url_key = p.get("http", "")
    if url_key in PROXY_COUNTRY_CACHE:
        return PROXY_COUNTRY_CACHE[url_key]
    return "UNKNOWN"


def resolve_proxy_country(p):
    """Actively queries ip-api to resolve proxy location and saves to cache."""
    global PROXY_COUNTRY_CACHE
    url_key = p.get("http", "")
    if url_key in PROXY_COUNTRY_CACHE and PROXY_COUNTRY_CACHE[url_key] != "UNKNOWN":
        return PROXY_COUNTRY_CACHE[url_key]
    try:
        r = requests.get("http://ip-api.com/json", proxies=p, timeout=5, verify=False)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                cc = (data.get("countryCode") or "").upper()
                if cc:
                    if hasattr(p, 'country'):
                        p.country = cc
                    PROXY_COUNTRY_CACHE[url_key] = cc
                    if 'db' in globals() and db is not None:
                        try:
                            db.set_setting("proxy_country_cache", PROXY_COUNTRY_CACHE)
                        except Exception:
                            pass
                    return cc
    except Exception:
        pass
    return "UNKNOWN"


def load_proxies():
    global PROXY_COUNTRY_CACHE
    if 'db' in globals() and db is not None:
        try:
            cached = db.get_setting("proxy_country_cache", {})
            if isinstance(cached, dict):
                PROXY_COUNTRY_CACHE.update(cached)
        except Exception:
            pass
    proxies = []
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                p = parse_proxy_line(line)
                if p:
                    if not p.country:
                        url_key = p.get("http", "")
                        if url_key in PROXY_COUNTRY_CACHE:
                            p.country = PROXY_COUNTRY_CACHE[url_key]
                    proxies.append(p)
    return proxies


def get_service_proxies(service):
    """
    Dedicated Proxy Routing:
    - 'jiohotstar': ONLY Indian (IN) residential proxies saved & reserved.
    - 'crunchyroll': Other/foreign proxies dedicated for Crunchyroll.
    - All other services (netflix, hbomax, surfshark, spotify, etc.): Leave on SAME server IP (no proxy).
    """
    global proxies_list
    if not proxies_list:
        return []

    if service == "jiohotstar":
        # Save & reserve Indian proxies specifically for JioHotstar
        in_proxies = [p for p in proxies_list if get_proxy_country(p) == "IN"]
        if not in_proxies:
            for p in proxies_list:
                if get_proxy_country(p) == "UNKNOWN":
                    cc = resolve_proxy_country(p)
                    if cc == "IN":
                        in_proxies.append(p)
        return in_proxies

    elif service == "crunchyroll":
        # Other proxies are dedicated for Crunchyroll (prefer non-IN so IN is preserved for JioHotstar)
        other_proxies = [p for p in proxies_list if get_proxy_country(p) not in ("IN", "UNKNOWN")]
        if not other_proxies:
            for p in proxies_list:
                if get_proxy_country(p) == "UNKNOWN":
                    cc = resolve_proxy_country(p)
                    if cc != "IN":
                        other_proxies.append(p)
        if other_proxies:
            return other_proxies
        # Fallback if no non-IN proxy exists
        return proxies_list

    else:
        # Leave all other services on SAME server IP (no proxy used)
        return []


def get_raw_proxies():
    if not os.path.exists(PROXY_FILE):
        return []
    try:
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
    except:
        return []

def save_proxies_to_file(raw_lines):
    try:
        with open(PROXY_FILE, "w", encoding="utf-8") as f:
            for line in raw_lines:
                f.write(line + "\n")
        return True
    except:
        return False

def check_single_proxy(proxy_dict):
    try:
        r = requests.get("https://httpbin.org/ip", proxies=proxy_dict, timeout=5, verify=False)
        return r.status_code == 200
    except:
        return False

def check_proxy_with_region(proxy_dict):
    try:
        r = requests.get("http://ip-api.com/json", proxies=proxy_dict, timeout=6, verify=False)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                cc = data.get("countryCode", "").upper()
                cname = data.get("country", "")
                url_key = proxy_dict.get("http", "")
                if url_key and cc:
                    PROXY_COUNTRY_CACHE[url_key] = cc
                return True, cc, cname
    except:
        pass
    if check_single_proxy(proxy_dict):
        return True, "UNKNOWN", "Unknown Region"
    return False, None, None

proxies_list = load_proxies()


def _auto_extract_vault_zips(service_dir):
    """Auto-extract any .zip files sitting in a vault dir — prevents the 'exhausted' ghost bug
    where valid cookies are trapped inside an unextracted zip. Extracts then nukes the zip."""
    try:
        zips = [f for f in os.listdir(service_dir) if f.lower().endswith(".zip")]
        for zname in zips:
            zpath = os.path.join(service_dir, zname)
            try:
                with zipfile.ZipFile(zpath, 'r') as zf:
                    for name in zf.namelist():
                        if name.endswith('/') or name.startswith('__MACOSX') or name.startswith('.'):
                            continue
                        if not name.lower().endswith(('.txt', '.json')):
                            continue
                        content = zf.read(name).decode('utf-8', errors='ignore')
                        if not content.strip():
                            continue
                        base = os.path.basename(name)
                        safe_name = re.sub(r'[<>:"/\\|?*]', '_', base)
                        dest = os.path.join(service_dir, safe_name)
                        if os.path.exists(dest):
                            suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
                            name_part, ext = os.path.splitext(safe_name)
                            dest = os.path.join(service_dir, f"{name_part}_{suffix}{ext}")
                        with open(dest, 'w', encoding='utf-8') as f:
                            f.write(content)
                # Zip extracted successfully — yeet it so we don't re-extract next time
                os.remove(zpath)
            except Exception:
                pass  # Corrupt zip? skip it, don't crash the whole flow
    except Exception:
        pass


def get_vault_cookies(service):
    service_dir = os.path.join(COOKIES_DIR, service)
    if not os.path.exists(service_dir):
        return []
    # Auto-extract any zip files that got dropped in here without /upload
    _auto_extract_vault_zips(service_dir)
    return [f for f in os.listdir(service_dir) if f.lower().endswith((".txt", ".json"))]


def count_vault_cookies(service):
    return len(get_vault_cookies(service))


def clear_vault_cookies(service=None):
    deleted_count = 0
    if service == "all":
        services_to_clear = SUPPORTED_SERVICES
    elif service in SUPPORTED_SERVICES:
        services_to_clear = [service]
    else:
        services_to_clear = []

    with cookie_lock:
        for s in services_to_clear:
            service_dir = os.path.join(COOKIES_DIR, s)
            if os.path.exists(service_dir):
                for f in os.listdir(service_dir):
                    if f.lower().endswith((".txt", ".json")):
                        try:
                            os.remove(os.path.join(service_dir, f))
                            deleted_count += 1
                        except:
                            pass
    return deleted_count


def make_delete_vault_keyboard():
    keyboard = []
    service_emojis = {
        "netflix": "🔴",
        "crunchyroll": "🟠",
        "hbomax": "🟣",
        "surfshark": "🦈",
        "spotify": "🟢",
        "jiohotstar": "🔵",
        "primevideo": "🎬"
    }
    for s in SUPPORTED_SERVICES:
        c_count = count_vault_cookies(s)
        emoji = service_emojis.get(s, "🍪")
        keyboard.append([
            btn(
                text=f"{emoji} {s.upper()} ({c_count} cookies)",
                callback_data=f"admin_delvault_prompt:{s}",
                style="primary"
            )
        ])
    
    total_cookies = sum(count_vault_cookies(s) for s in SUPPORTED_SERVICES)
    keyboard.append([
        btn(
            text=f"⚠️ Delete ALL Vaults ({total_cookies} cookies)",
            callback_data="admin_delvault_prompt:all",
            style="danger"
        )
    ])
    keyboard.append([
        btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_random_cookie_file(service):
    service_dir = os.path.join(COOKIES_DIR, service)
    with cookie_lock:
        files = get_vault_cookies(service)
        if not files:
            return None, None, None
        filename = random.choice(files)
        filepath = os.path.join(service_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return filename, filepath, content
        except:
            return None, None, None



BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
DOTS_FRAMES = ["", ".", "..", "..."]


async def animate_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, stop_event: asyncio.Event):
    frame_idx = 0
    while not stop_event.is_set():
        frame = BRAILLE_FRAMES[frame_idx % len(BRAILLE_FRAMES)]
        dots = DOTS_FRAMES[(frame_idx // len(BRAILLE_FRAMES)) % len(DOTS_FRAMES)]
        if stop_event.is_set():
            break
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"{frame} Processing cookie verification{dots}\n\nPlease wait...",
            )
        except Exception:
            pass
        frame_idx += 1
        try:
            await asyncio.sleep(1.2)
        except asyncio.CancelledError:
            break


# ==========================================
# Message Cleanup & UX Auto-delete Helpers
# ==========================================
async def auto_clean_user_message(update: Update):
    if update and update.message:
        try:
            await update.message.delete()
        except Exception:
            pass

async def auto_clean_previous_bot_message(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    if not context or not context.user_data:
        return
    chat_id = update_or_query.effective_chat.id if update_or_query and update_or_query.effective_chat else None
    old_msg_id = context.user_data.get("last_bot_msg_id")
    if chat_id and old_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception:
            pass
        context.user_data["last_bot_msg_id"] = None

def track_last_bot_message(context: ContextTypes.DEFAULT_TYPE, message):
    if context and context.user_data and message and hasattr(message, "message_id"):
        context.user_data["last_bot_msg_id"] = message.message_id


# ==========================================
# Menu Keyboard Helpers
# ==========================================
def make_main_menu_keyboard(user_id):
    keyboard = [
        [btn(text="📺 Activate TV", callback_data="menu_activate", style="success")],
        [
            btn(text="👥 Refer & Earn (Free)", callback_data="menu_referral", style="success"),
            btn(text="💎 Premium Plans", callback_data="menu_premium", style="primary")
        ],
        [
            btn(text="📖 Instructions", callback_data="menu_help", style="primary")
        ]
    ]
    if is_admin(user_id):
        keyboard.append([btn(text="👑 Admin Panel", callback_data="menu_admin", style="primary")])
    return InlineKeyboardMarkup(keyboard)


def make_admin_keyboard():
    keyboard = [
        [
            btn(text="📥 Upload Cookies", callback_data="admin_upload_prompt", style="success"),
            btn(text="📊 Bot Statistics", callback_data="admin_stats", style="primary")
        ],
        [
            btn(text="🎁 Grant Subscription", callback_data="admin_grant_sub_prompt", style="success"),
            btn(text="👑 Premium Users List", callback_data="admin_premium_users_list", style="primary")
        ],
        [
            btn(text="💎 Premium Settings", callback_data="admin_premium_menu", style="primary"),
            btn(text="👥 Refer & Wallet", callback_data="admin_referral_menu", style="success")
        ],
        [
            btn(text="📢 Broadcast Message", callback_data="admin_broadcast_prompt", style="primary"),
            btn(text="🔌 Manage Proxies", callback_data="admin_proxies", style="success")
        ],
        [
            btn(text="🛠️ Settings Panel", callback_data="admin_settings", style="primary"),
            btn(text="💾 Backup DB", callback_data="admin_backup_db_now", style="success")
        ],
        [
            btn(text="🔄 Restore DB", callback_data="admin_restore_db_now", style="primary"),
            btn(text="🗑️ Delete Vault", callback_data="admin_delete_vault_menu", style="danger")
        ],
        [
            btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def safe_edit_message(query, context, text, reply_markup):
    if query.message and query.message.photo:
        try:
            await query.message.delete()
        except:
            pass
        return await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    else:
        return await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )


def build_main_menu_text(user):
    user_id = user.id
    first_name = html.escape(user.first_name or "User")
    full_name = html.escape(user.full_name or user.first_name or "User")
    bal = db.get_user_balance(user_id)
    prem = db.get_user_premium(user_id)

    # Status
    if is_admin(user_id):
        status_str = "👑 Admin"
    elif prem.get("is_active"):
        if prem.get("is_trial"):
            status_str = "🎁 Free Trial Active ⚡"
        else:
            status_str = "⭐ Premium Member 💎"
    elif prem.get("expires_at"):
        status_str = "⏳ Plan Expired"
    else:
        status_str = "👤 Free User"

    # Plan details & Logins Left
    if prem.get("is_active"):
        if prem.get("is_trial"):
            plan_name = "1-Day Free Trial"
            rem = prem.get("remaining_logins", 0)
            exp = prem.get("expires_at", "N/A")
            plan_section = (
                f"📋 Active Plan: {plan_name}\n"
                f"🎟️ Logins Left: {rem}/10 (Use on Any Service!)\n"
                f"⏳ Expires At: <code>{exp}</code>\n"
            )
        else:
            raw_plan = str(prem.get("plan", "Premium")).capitalize()
            plan_name = f"{raw_plan} Plan"
            svc_dict = prem.get("service_logins", {})
            total_rem = sum(svc_dict.values()) if svc_dict else prem.get("remaining_logins", 0)
            exp = prem.get("expires_at", "Lifetime / Never")
            plan_section = (
                f"📋 Active Plan: {plan_name} 💎\n"
                f"🎟️ Logins Left: {total_rem} Total Logins\n"
                f"⏳ Expires At: <code>{exp}</code>\n"
            )
    elif prem.get("expires_at"):
        plan_section = (
            f"📋 Active Plan: Expired (Upgrade to renew)\n"
            f"🎟️ Logins Left: 0\n"
            f"⏳ Expired On: <code>{prem.get('expires_at')}</code>\n"
        )
    else:
        plan_section = (
            f"📋 Active Plan: None (Free User)\n"
            f"🎟️ Logins Left: 0\n"
        )

    raw_text = (
        f"👋 Hey {first_name}!\n\n"
        f"🎬 𝗫𝗗𝗥𝗢𝗜𝗗 𝗧𝗩 𝗔𝗖𝗧𝗜𝗩𝗔𝗧𝗜𝗢𝗡 𝗕𝗢𝗧 > ⚡\n\n"
        f"👤 Name: {full_name}\n"
        f"🆔 Chat ID: <code>{user_id}</code>\n"
        f"🔰 Status: {status_str}\n"
        f"{plan_section}"
        f"💰 Wallet Balance: ₹{bal:.2f}\n\n"
        f"👇 Select an option from the menu below to proceed:"
    )
    return f"<b><i>{raw_text}</i></b>"


async def send_main_menu(update_or_query, context, edit=False):
    is_query = hasattr(update_or_query, "callback_query") and update_or_query.callback_query is not None
    user = update_or_query.effective_user
    
    text = build_main_menu_text(user)
    reply_markup = make_main_menu_keyboard(user.id)
    
    if edit and is_query:
        await safe_edit_message(update_or_query.callback_query, context, text, reply_markup)
    else:
        message = update_or_query.callback_query.message if is_query else update_or_query.message
        await message.reply_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def send_referral_menu(update_or_query, context: ContextTypes.DEFAULT_TYPE, edit=False):
    is_query = hasattr(update_or_query, "callback_query") and update_or_query.callback_query is not None
    user = update_or_query.effective_user
    
    bal = db.get_user_balance(user.id)
    ref_cfg = db.get_referral_settings()
    reward = ref_cfg["referral_reward"]
    user_info = db.data.get("users", {}).get(str(user.id), {})
    user_ref_count = int(user_info.get("referral_count", 0))

    # Calculate total referral earnings for this user
    user_history = user_info.get("wallet_history", [])
    user_ref_earnings = sum(float(item.get("amount", 0.0)) for item in user_history if item.get("type") == "referral_reward")
    if user_ref_earnings <= 0.0 and user_ref_count > 0:
        user_ref_earnings = user_ref_count * reward

    # Calculate global total referrals across all users on the bot
    with db.lock:
        total_platform_referrals = sum(int(u.get("referral_count", 0)) for u in db.data.get("users", {}).values())

    try:
        bot_info = await context.bot.get_me()
        bot_username = bot_info.username
    except Exception:
        bot_username = "TVLoginBot"

    import html as html_mod

    ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    share_msg = (
        "🔥 **Watch Premium OTT on TV for 100% FREE!** 📺✨\n\n"
        "🎁 *Get Instant 1-Day VIP Trial:*\n"
        "⚡ **10 Free TV Logins** *(Use on ANY Service you want!)*\n\n"
        "🍿 **Supported Streaming Platforms:**\n"
        "• 🔴 **Netflix TV** *(4K Ultra HD)*\n"
        "• 🟣 **HBO Max VIP**\n"
        "• 🟠 **Crunchyroll Mega Fan**\n"
        "• 🔵 **Disney+ / JioHotstar**\n"
        "• 🟢 **Spotify Premium**\n"
        "• 🛡️ **Surfshark VPN**\n\n"
        "✨ *Instant 1-Click TV Login • 100% Safe & Working!*\n"
        "👇 *Claim your Free 10 Logins now before offer expires:*"
    )
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote(share_msg)}"

    copy_promo = (
        f"🔥 Watch Premium OTT on TV for 100% FREE! 📺✨\n\n"
        f"🎁 Claim Instant 1-Day VIP Trial:\n"
        f"⚡ 10 Free TV Logins (Use on ANY Service!)\n\n"
        f"🍿 Supported Streaming Platforms:\n"
        f"• 🔴 Netflix TV (4K UHD)\n"
        f"• 🟣 HBO Max VIP\n"
        f"• 🟠 Crunchyroll Mega Fan\n"
        f"• 🔵 Disney+ / JioHotstar\n"
        f"• 🟢 Spotify Premium\n"
        f"• 🛡️ Surfshark VPN\n\n"
        f"✨ Instant 1-Click Login • 100% Safe & Working!\n"
        f"👇 Claim your Free 10 Logins now before offer ends:\n"
        f"{ref_link}"
    )

    text = (
        f"👥 <b>REFER & EARN — FREE TV LOGINS 🎁</b>\n\n"
        f"Invite friends and earn instant wallet cash! Use your wallet balance to activate Premium plans for <b>100% FREE</b>.\n\n"
        f"💰 <b>Your Wallet Balance:</b> <b>₹{bal:.2f}</b>\n"
        f"🎯 <b>Your Total Referrals:</b> <b>{user_ref_count} Users</b>\n"
        f"💵 <b>Total Referral Earnings:</b> <b>₹{user_ref_earnings:.2f}</b>\n"
        f"🎁 <b>Reward per Friend:</b> <b>₹{reward:.2f}</b>\n"
        f"⚡ <b>New User Gift:</b> <b>1-Day Free Trial (10 Logins)</b>\n"
        f"🌐 <b>Platform Total Referrals:</b> <b>{total_platform_referrals} Users</b>\n\n"
        f"🔗 <b>Your Personal Referral Link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"📋 <b>Promotional Share Message (Tap to Copy):</b>\n"
        f"<code>{html_mod.escape(copy_promo)}</code>\n\n"
        f"<i>💡 Tap <b>Share Invite Link</b> below to share directly with friends, groups & channels!</i>"
    )

    keyboard = [
        [InlineKeyboardButton(text="📤 Share Invite Link", url=share_url)],
        [btn(text="💎 View Premium Plans", callback_data="menu_premium", style="success")],
        [btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit and is_query:
        await safe_edit_message(update_or_query.callback_query, context, text, reply_markup)
    else:
        message = update_or_query.callback_query.message if is_query else update_or_query.message
        await message.reply_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, err_msg = check_chat_mode(update)
    if not allowed:
        if err_msg:
            await update.message.reply_text(err_msg, parse_mode=ParseMode.HTML)
        return

    if not await check_force_join(update, context):
        return

    await auto_clean_previous_bot_message(update, context)
    await auto_clean_user_message(update)

    user = update.effective_user
    is_new = str(user.id) not in db.data.get("users", {})
    db.register_user(user.id, user.username, user.first_name)

    # Process referral if user is new and joined with a ref argument
    if is_new and context.args and len(context.args) > 0:
        arg_val = context.args[0].strip()
        referrer_id = None
        if arg_val.startswith("ref_"):
            referrer_id = arg_val.replace("ref_", "")
        elif arg_val.isdigit():
            referrer_id = arg_val
            
        if referrer_id:
            success, reward_amt, ref_id = db.process_referral(user.id, referrer_id)
            if success:
                try:
                    ref_new_bal = db.get_user_balance(ref_id)
                    await context.bot.send_message(
                        chat_id=int(ref_id),
                        text=(
                            f"🎉 <b>New Referral Received!</b>\n\n"
                            f"👤 <b>{user.first_name}</b> joined using your invite link!\n"
                            f"💰 <b>+₹{reward_amt:.2f}</b> has been credited to your wallet.\n"
                            f"💳 Current Balance: <b>₹{ref_new_bal:.2f}</b>\n\n"
                            f"<i>Use your wallet funds to activate TV streaming for free!</i>"
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass

    text = build_main_menu_text(user)
    reply_markup = make_main_menu_keyboard(user.id)
    img_url = "https://i.ibb.co/S2rFDP4/Chat-GPT-Image-Jul-19-2026-10-45-29-PM.png"
    
    msg = await update.message.reply_photo(
        photo=img_url,
        caption=text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    track_last_bot_message(context, msg)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, err_msg = check_chat_mode(update)
    if not allowed:
        if err_msg:
            await update.message.reply_text(err_msg, parse_mode=ParseMode.HTML)
        return

    if not await check_force_join(update, context):
        return

    await auto_clean_previous_bot_message(update, context)
    await auto_clean_user_message(update)

    help_text = (
        "📖 <b>How to Use TV Activation Bot</b>\n\n"
        "This bot helps you activate TV apps using working cookies from our vault.\n\n"
        "🎁 <b>New User Free Trial:</b>\n"
        "• Har new user ko milta hai <b>1-Day Free Trial with 10 Logins</b>.\n"
        "• Aap ye 10 logins apni marzi se <b>kisi bhi service</b> me use kar sakte ho (Netflix, Crunchyroll, HBO Max, Spotify, Surfshark, JioHotstar)!\n\n"
        "👥 <b>Refer & Earn Free TV Logins:</b>\n"
        "• Apne dosto ko referral link share karein.\n"
        "• Har referral par aapke wallet me cash reward add hoga!\n"
        "• Wallet cash se aap direct Premium plans <b>100% FREE</b> buy kar sakte hain!\n\n"
        "💡 <b>Steps to Activate:</b>\n"
        "1️⃣ Open the app on your TV (Netflix, Crunchyroll, HBO Max, Spotify, Surfshark, etc.).\n"
        "2️⃣ Get the activation/login code from the TV screen.\n"
        "3️⃣ Open the Main Menu using <code>/start</code> and click the <b>📺 Activate TV</b> button.\n"
        "4️⃣ Enter the code when prompted via reply.\n"
        "5️⃣ Select the service from the buttons that appear.\n\n"
        "👑 <b>Support:</b>\n"
        "• For support, contact admin: @Sudhirxd"
    )
    keyboard = [[InlineKeyboardButton(text="🔙 Main Menu", callback_data="menu_main")]]
    msg = await update.message.reply_text(help_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    track_last_bot_message(context, msg)


async def tv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, err_msg = check_chat_mode(update)
    if not allowed:
        if err_msg:
            await update.message.reply_text(err_msg, parse_mode=ParseMode.HTML)
        return

    if not await check_force_join(update, context):
        return

    await auto_clean_previous_bot_message(update, context)
    await auto_clean_user_message(update)

    user = update.effective_user
    args = context.args
    if not args:
        keyboard = [
            [
                btn(text="🔴 Netflix", callback_data="btn_activate:netflix", style="success"),
                btn(text="🟠 Crunchyroll", callback_data="btn_activate:crunchyroll", style="success")
            ],
            [
                btn(text="🟣 HBO Max", callback_data="btn_activate:hbomax", style="success"),
                btn(text="🔵 Surfshark", callback_data="btn_activate:surfshark", style="success")
            ],
            [
                btn(text="🟢 Spotify", callback_data="btn_activate:spotify", style="success"),
                btn(text="🔵 JioHotstar", callback_data="btn_activate:jiohotstar", style="success")
            ],
            [
                btn(text="🎬 Prime Video", callback_data="btn_activate:primevideo", style="success")
            ],
            [
                btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await update.message.reply_text(
            "📺 <b>Select the Service you want to activate:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        track_last_bot_message(context, msg)
        return

    raw_code = args[0].strip()
    db.register_user(user.id, user.username, user.first_name)

    keyboard = [
        [
            btn(text="🔴 Netflix", callback_data=f"activate:netflix:{raw_code}", style="success"),
            btn(text="🟠 Crunchyroll", callback_data=f"activate:crunchyroll:{raw_code}", style="success")
        ],
        [
            btn(text="🟣 HBO Max", callback_data=f"activate:hbomax:{raw_code}", style="success"),
            btn(text="🔵 Surfshark", callback_data=f"activate:surfshark:{raw_code}", style="success")
        ],
        [
            btn(text="🟢 Spotify", callback_data=f"activate:spotify:{raw_code}", style="success"),
            btn(text="🔵 JioHotstar", callback_data=f"activate:jiohotstar:{raw_code}", style="success")
        ],
        [
            btn(text="🎬 Prime Video", callback_data=f"activate:primevideo:{raw_code}", style="success")
        ],
        [
            btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = await update.message.reply_text(
        f"📺 <b>Select Service for Code:</b> <code>{raw_code.upper()}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    track_last_bot_message(context, msg)


async def handle_netflix_login_link_request(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    is_query = hasattr(update_or_query, "callback_query") and update_or_query.callback_query is not None
    user = update_or_query.effective_user
    chat_id = update_or_query.effective_chat.id
    
    # Check daily limit per user
    daily_limit = int(db.get_setting("daily_token_limit", 3)) # 0 = unlimited
    if daily_limit > 0 and not is_admin(user.id):
        used_today = db.get_user_daily_token_count(user.id)
        if used_today >= daily_limit:
            limit_text = (
                f"⚠️ <b>Daily Token Limit Reached!</b>\n\n"
                f"You have reached your daily limit of <b>{daily_limit}/{daily_limit}</b> Netflix Login Links for today.\n\n"
                f"⏳ <i>Your limit will automatically reset tomorrow.</i>"
            )
            keyboard = [[btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            if is_query:
                await update_or_query.callback_query.message.reply_text(limit_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
            else:
                await update_or_query.message.reply_text(limit_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
            return

    status_msg = None
    prompt_text = "⏳ <b>Generating Netflix Direct Login Link...</b>\n<i>Creating secure NFToken session, please wait...</i>"
    
    if is_query:
        query = update_or_query.callback_query
        try:
            status_msg = await query.message.reply_text(prompt_text, parse_mode=ParseMode.HTML)
        except Exception:
            status_msg = await context.bot.send_message(chat_id=chat_id, text=prompt_text, parse_mode=ParseMode.HTML)
    else:
        status_msg = await update_or_query.message.reply_text(prompt_text, parse_mode=ParseMode.HTML)
        
    db.register_user(user.id, user.username, user.first_name)
    
    has_access, reason = user_has_access(user.id, "nftoken")
    if not has_access:
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
        await send_premium_required_message(update_or_query, context, reason, service="nftoken")
        return

    link_res = await asyncio.to_thread(generate_netflix_login_link)
    
    # Track NFToken generation stats
    with stats_lock:
        if "nftoken" not in stats or not isinstance(stats["nftoken"], dict):
            stats["nftoken"] = {"successful": 0, "failed": 0}
        if link_res.get("success"):
            stats["nftoken_generated"] = stats.get("nftoken_generated", 0) + 1
            stats["nftoken"]["successful"] = stats["nftoken"].get("successful", 0) + 1
        else:
            stats["nftoken_failed"] = stats.get("nftoken_failed", 0) + 1
            stats["nftoken"]["failed"] = stats["nftoken"].get("failed", 0) + 1
        _save_stats()
    
    if link_res.get("success"):
        pc_link = link_res["pc_link"]
        phone_link = link_res["phone_link"]
        country = link_res.get("country", "Global 🌐")
        plan = link_res.get("plan", "Netflix Premium 💎")
        email = link_res.get("email", "Netflix Account")
        
        usage_line = ""
        if not is_admin(user.id):
            used_cnt = db.increment_user_daily_token_count(user.id)
            rem_logins = db.deduct_user_login(user.id, "nftoken")
            if daily_limit > 0:
                usage_line = f"📊 <b>Daily Usage:</b> <b>{used_cnt}/{daily_limit}</b> | 🎟️ <b>NFToken Left:</b> <b>{rem_logins}</b>\n"
            else:
                usage_line = f"🎟️ <b>NFToken Left:</b> <b>{rem_logins}</b>\n"
        else:
            usage_line = "👑 <b>Admin:</b> <i>Unlimited</i>\n"
        
        reply_text = (
            f"🎉 <b>Netflix Direct Login Link Generated!</b>\n\n"
            f"👤 <b>Account:</b> <code>{email}</code>\n"
            f"🌍 <b>Country:</b> <b>{country}</b>\n"
            f"💎 <b>Plan:</b> <b>{plan}</b>\n"
            f"{usage_line}\n"
            f"👇 <b>Choose your device to log in:</b>"
        )
        
        keyboard = [
            [
                btn(text="🖥️ PC Login", url=pc_link, style="success"),
                btn(text="📱 Phone Login", url=phone_link, style="success")
            ],
            [
                btn(text="🔄 Generate Another Link", callback_data="gen_netflix_link", style="primary"),
                btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await status_msg.edit_text(reply_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text=reply_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    else:
        err = link_res.get("error", "No working Netflix cookies found in vault")
        err_text = (
            f"❌ <b>Failed to Generate Login Link</b>\n\n"
            f"⚠️ <b>Reason:</b> {err}\n\n"
            f"<i>Please make sure valid Netflix cookies are available in the vault.</i>"
        )
        keyboard = [[btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")]]
        try:
            await status_msg.edit_text(err_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text=err_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))


async def netflix_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, err_msg = check_chat_mode(update)
    if not allowed:
        if err_msg:
            await update.message.reply_text(err_msg, parse_mode=ParseMode.HTML)
        return

    if not await check_force_join(update, context):
        return

    await auto_clean_previous_bot_message(update, context)
    await auto_clean_user_message(update)
    await handle_netflix_login_link_request(update, context)


async def set_token_limit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 <b>Admin only!</b>", parse_mode=ParseMode.HTML)
        return
        
    args = context.args
    if not args:
        current_limit = db.get_setting("daily_token_limit", 3)
        limit_display = "Unlimited ♾️" if int(current_limit) == 0 else f"{current_limit} per day"
        await update.message.reply_text(
            f"🎟️ <b>Current Daily NFToken Limit:</b> <b>{limit_display}</b>\n\n"
            f"💡 <b>Usage:</b> <code>/settokenlimit &lt;number&gt;</code>\n"
            f"<i>Example: <code>/settokenlimit 5</code> (or <code>0</code> for unlimited)</i>",
            parse_mode=ParseMode.HTML
        )
        return
        
    try:
        new_limit = int(args[0].strip())
        if new_limit < 0:
            raise ValueError()
        db.set_setting("daily_token_limit", new_limit)
        limit_display = "Unlimited ♾️" if new_limit == 0 else f"{new_limit} per day"
        await update.message.reply_text(
            f"✅ <b>Daily NFToken Limit Updated!</b>\n\n"
            f"🎯 New limit: <b>{limit_display}</b> per user.",
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number (e.g. <code>/settokenlimit 3</code> or <code>0</code> for unlimited).", parse_mode=ParseMode.HTML)


async def netflix_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, err_msg = check_chat_mode(update)
    if not allowed:
        if err_msg:
            await update.message.reply_text(err_msg, parse_mode=ParseMode.HTML)
        return

    args = context.args
    if not args:
        keyboard = [
            [
                btn(text="📺 TV Login", callback_data="netflix_mode:tv", style="success"),
                btn(text="💻/📱 PC/Phone Login", callback_data="gen_netflix_link", style="primary")
            ],
            [
                btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await update.message.reply_text(
            "🔴 <b>Netflix Login Options:</b>\n\nPlease select your preferred login method:",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        track_last_bot_message(context, msg)
        return
    raw_input = " ".join(args)
    code = re.sub(r'\D', '', raw_input)
    if len(code) != 8:
        await update.message.reply_text("❌ Netflix code must be exactly <b>8 digits</b> (e.g. <code>6576-9865</code>, <code>6576 9865</code>, or <code>65769865</code>).", parse_mode=ParseMode.HTML)
        return
    await start_tv_activation(update, context, "netflix", code)


async def crunchy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or len(args[0].strip()) != 6:
        await update.message.reply_text("❌ Usage: <code>/crunchy XXXXXX</code> (6 characters)", parse_mode=ParseMode.HTML)
        return
    await start_tv_activation(update, context, "crunchyroll", args[0].strip().upper())


async def hbomax_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or len(args[0].strip()) != 6:
        await update.message.reply_text("❌ Usage: <code>/hbomax XXXXXX</code> (6 characters)", parse_mode=ParseMode.HTML)
        return
    await start_tv_activation(update, context, "hbomax", args[0].strip().upper())


async def surfshark_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or len(args[0].strip()) != 6:
        await update.message.reply_text("❌ Usage: <code>/surfshark XXXXXX</code> (6 characters)", parse_mode=ParseMode.HTML)
        return
    await start_tv_activation(update, context, "surfshark", args[0].strip().upper())


async def spotify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        keyboard = [
            [
                btn(text="📺 TV Login", callback_data="spotify_mode:tv", style="success"),
                btn(text="📸 PC/QR Login", callback_data="spotify_mode:qr", style="primary")
            ],
            [
                btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await update.message.reply_text(
            "🟢 <b>Spotify Login Options:</b>\n\nPlease select your preferred login method:",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        track_last_bot_message(context, msg)
        return
    code = args[0].strip()
    await start_tv_activation(update, context, "spotify", code)


async def jiohotstar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        if context.user_data is not None:
            context.user_data["selected_service"] = "jiohotstar"
        msg = await update.message.reply_text(
            "📸 <b>Please reply to this message with your JioHotstar QR code image:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(selective=True)
        )
        track_last_bot_message(context, msg)
        return
    code = args[0].strip()
    await start_tv_activation(update, context, "jiohotstar", code)


async def prime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or len(args[0].strip()) != 6:
        await update.message.reply_text("❌ Usage: <code>/prime XXXXXX</code> (6 characters)", parse_mode=ParseMode.HTML)
        return
    await start_tv_activation(update, context, "primevideo", args[0].strip().upper())


async def start_tv_activation(update_or_query, context: ContextTypes.DEFAULT_TYPE, service: str, tv_code: str):
    # Sanitize TV Code format (remove dashes, spaces, etc.)
    if service == "netflix":
        cleaned = re.sub(r'\D', '', str(tv_code or ''))
        if len(cleaned) == 8:
            tv_code = cleaned
    elif service in ("crunchyroll", "hbomax", "surfshark", "spotify", "primevideo"):
        cleaned = re.sub(r'[\s\-_.:,]', '', str(tv_code or '')).upper()
        if len(cleaned) == 6:
            tv_code = cleaned

    is_query = hasattr(update_or_query, "callback_query") and update_or_query.callback_query is not None
    user = update_or_query.effective_user
    chat_id = update_or_query.effective_chat.id
    message_id = update_or_query.effective_message.message_id

    allowed, err_msg = check_chat_mode(update_or_query)
    if not allowed:
        if err_msg:
            if is_query:
                await update_or_query.callback_query.edit_message_text(text=err_msg, parse_mode=ParseMode.HTML)
            else:
                await update_or_query.message.reply_text(err_msg, parse_mode=ParseMode.HTML)
        return

    if not await check_force_join(update_or_query, context):
        return

    if not is_query:
        await auto_clean_previous_bot_message(update_or_query, context)
        await auto_clean_user_message(update_or_query)

    db.register_user(user.id, user.username, user.first_name)

    has_access, reason = user_has_access(user.id, service)
    if not has_access:
        await send_premium_required_message(update_or_query, context, reason, service=service)
        return

    if db.get_setting("kito_mode", False) and not is_admin(user.id):
        user_data = db.get_user(user.id)
        if user_data:
            success_dates = user_data.get("last_success_dates", {})
            today = datetime.now().strftime("%Y-%m-%d")
            if success_dates.get(service) == today:
                err_msg = f"❌ <b>Activation Limit Exceeded!</b>\n\nKito Mode is active. You can only successfully activate <b>{service.upper()}</b> once per day."
                keyboard = [[btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                if is_query:
                    await update_or_query.callback_query.edit_message_text(err_msg, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
                else:
                    msg = await update_or_query.message.reply_text(err_msg, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
                    track_last_bot_message(context, msg)
                return

    if count_vault_cookies(service) == 0:
        msg_text = f"😔 <b>No cookies left in {service.upper()} vault!</b>\n\nWait for admin to upload more."
        if is_query:
            await update_or_query.callback_query.edit_message_text(msg_text, parse_mode=ParseMode.HTML)
        else:
            msg = await update_or_query.message.reply_text(msg_text, parse_mode=ParseMode.HTML)
            track_last_bot_message(context, msg)
        return

    start_text = (
        f"🔍 <b>Starting {service.upper()} TV login...</b>\n\n"
        f"📺 Code: <code>{tv_code}</code>\n"
        f"🍪 Searching vault for a working cookie..."
    )

    if is_query:
        status_msg = await update_or_query.callback_query.edit_message_text(start_text, parse_mode=ParseMode.HTML)
    else:
        status_msg = await update_or_query.message.reply_text(
            start_text,
            parse_mode=ParseMode.HTML,
        )
    track_last_bot_message(context, status_msg)

    # Start animation
    stop_anim = asyncio.Event()
    anim_task = asyncio.create_task(animate_message(context, chat_id, status_msg.message_id, stop_anim))

    result = await asyncio.to_thread(process_tv_login_service, service, tv_code)

    stop_anim.set()
    anim_task.cancel()
    try:
        await anim_task
    except (asyncio.CancelledError, Exception):
        pass
    await asyncio.sleep(0.3)

    # Record attempt in DB
    db.record_attempt(user.id, result["success"])
    if result["success"]:
        if not is_admin(user.id):
            db.deduct_user_login(user.id, service)
        user_id_str = str(user.id)
        with db.lock:
            if user_id_str in db.data["users"]:
                u = db.data["users"][user_id_str]
                if "last_success_dates" not in u:
                    u["last_success_dates"] = {}
                u["last_success_dates"][service] = datetime.now().strftime("%Y-%m-%d")
                db.save_unlocked()

    with stats_lock:
        stats["total_logins"] += 1
        if service not in stats:
            stats[service] = {"successful": 0, "failed": 0}

        if result["success"]:
            stats["successful"] += 1
            stats[service]["successful"] = stats[service].get("successful", 0) + 1
        else:
            stats[service]["failed"] = stats[service].get("failed", 0) + 1
            if result.get("error") in ("Invalid or expired TV code", "TV code not found or expired"):
                stats["codes_rejected"] += 1
            else:
                stats["failed"] += 1
        _save_stats()

    if result["success"]:
        if service == "surfshark":
            response = (
                f"✅ <b>SURFSHARK DEVICE ACTIVATED SUCCESSFULLY!</b>\n\n"
                f"🔑 Your Code: <code>{tv_code}</code>\n"
                f"🌍 Country/Region: <b>{result.get('country', 'N/A')}</b>\n"
                f"📦 Plan/Details: <b>{result.get('plan', 'N/A')}</b>\n"
                f"📧 Account: <b>{result.get('email', 'N/A')}</b>\n\n"
                f"<i>Your Device is now ready! Enjoy!</i> 🍿"
            )
        elif service == "spotify":
            tv_name = result.get('tv_name', 'Unknown TV')
            response = (
                f"✅ <b>SPOTIFY TV ACTIVATED SUCCESSFULLY!</b>\n\n"
                f"📺 TV Name: <b>{tv_name}</b>\n"
                f"🔑 Your Code: <code>{tv_code}</code>\n"
                f"🌍 Country/Region: <b>{result.get('country', 'N/A')}</b>\n"
                f"📦 Plan/Details: <b>{result.get('plan', 'N/A')}</b>\n"
                f"📧 Account: <b>{result.get('email', 'N/A')}</b>\n\n"
                f"<i>Your TV is now ready! Enjoy!</i> 🍿"
            )
        elif service == "jiohotstar":
            response = (
                f"✅ <b>JIOHOTSTAR TV ACTIVATED SUCCESSFULLY!</b>\n\n"
                f"📺 Your Code: <code>{tv_code}</code>\n"
                f"🌍 Country/Region: <b>{result.get('country', 'India 🇮🇳')}</b>\n"
                f"📦 Plan/Details: <b>{result.get('plan', 'Hotstar VIP')}</b>\n"
                f"📱 Account/Phone: <b>{result.get('email', 'N/A')}</b>\n\n"
                f"<i>Your TV is now ready! Enjoy JioHotstar!</i> 🍿"
            )
        elif service == "primevideo":
            extra_lines = []
            if result.get("payment_method"):
                extra_lines.append(f"💳 Payment: <b>{result.get('payment_method')}</b>")
            if result.get("profiles"):
                extra_lines.append(f"👥 Profiles: <b>{result.get('profiles')}</b>")
            extra_str = ("\n" + "\n".join(extra_lines)) if extra_lines else ""
            device_str = result.get('device', 'Smart TV')
            response = (
                f"✅ <b>PRIME VIDEO TV ACTIVATED SUCCESSFULLY!</b> 🎬\n\n"
                f"🖥️ Device: <b>{device_str}</b>\n"
                f"📺 Your Code: <code>{tv_code}</code>\n"
                f"🌍 Country/Region: <b>{result.get('country', 'US')}</b>\n"
                f"📦 Plan: <b>{result.get('plan', 'Prime Video')}</b>\n"
                f"👤 Account: <b>{result.get('email', 'N/A')}</b>"
                f"{extra_str}\n\n"
                f"<i>Your TV is now signed into Prime Video! Enjoy streaming!</i> 🍿"
            )
        else:
            response = (
                f"✅ <b>{service.upper()} TV ACTIVATED SUCCESSFULLY!</b>\n\n"
                f"📺 Your Code: <code>{tv_code}</code>\n"
                f"🌍 Country/Region: <b>{result.get('country', 'N/A')}</b>\n"
                f"📦 Plan/Details: <b>{result.get('plan', 'N/A')}</b>\n"
                f"📧 Account: <b>{result.get('email', 'N/A')}</b>\n\n"
                f"<i>Your TV is now ready! Enjoy!</i> 🍿"
            )
    elif result.get("error") == "no_cookies":
        response = f"😔 <b>All cookies exhausted!</b>\n\nNo working cookies left in {service.upper()} vault.\nWait for admin to upload more."
    elif result.get("error") == "all_dead":
        response = f"❌ <b>No working cookies found!</b>\n\nAll available cookies for {service.upper()} are dead.\nVault is now empty."
    elif result.get("error") in ("Invalid or expired TV code", "TV code not found or expired"):
        response = (
            f"❌ <b>Invalid or Expired TV Code</b>\n\n"
            f"📺 Code: <code>{tv_code}</code>\n\n"
            f"<i>The code you entered is wrong or expired.\n"
            f"Please check your TV screen and try again with a fresh code.</i>"
        )
    else:
        response = (
            f"❌ <b>Activation Failed</b>\n\n"
            f"📺 Code: <code>{tv_code}</code>\n"
            f"⚠️ Error: {result.get('error', 'Unknown')}\n\n"
            f"<i>Please try again with a fresh code.</i>"
        )

    # Let user return to main menu
    keyboard = [[btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await status_msg.edit_text(response, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except Exception:
        try:
            await context.bot.send_message(chat_id=chat_id, text=response, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        except Exception:
            pass


def process_tv_login_service(service, tv_code):
    proxies = get_service_proxies(service)

    max_attempts = min(50, max(count_vault_cookies(service), 50))
    attempts = 0

    while attempts < max_attempts:
        attempts += 1

        filename, filepath, content = get_random_cookie_file(service)
        if not filename or not content:
            return {"success": False, "error": "no_cookies"}

        proxy = random.choice(proxies) if proxies else None

        if service == "crunchyroll":
            cookies = crunchyroll_parse_cookie_content(content)
            if not cookies:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            status, country, plan, auth_data = crunchyroll_validate_cookie(cookies, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
            working_proxy = auth_data.get("working_proxy", proxy) if auth_data else proxy
            result = crunchyroll_submit_tv_code(auth_data, tv_code, working_proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            result["country"] = country
            result["plan"] = plan
            result["email"] = auth_data.get("email", "N/A")
            result["cookie_file"] = filename

            if result.get("success") and db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except:
                    pass

            return result

        elif service == "netflix":
            cookies = netflix_extract_cookie_dict(content)
            if not cookies:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            status, country, plan, email = netflix_validate_cookie(cookies, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
            result = netflix_submit_tv_code(cookies, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue

            if (not email or email == "N/A") and filename:
                fn_match = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', filename)
                if fn_match:
                    email = fn_match.group(1)

            result["country"] = country
            result["plan"] = plan
            result["email"] = email
            result["cookie_file"] = filename

            if result.get("success") and db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except:
                    pass

            return result

        elif service == "hbomax":
            st_token = hbomax_extract_st_token(content)
            if not st_token:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            status, country, plan, auth_data = hbomax_validate_cookie(st_token, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
            result = hbomax_submit_tv_code(auth_data, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            result["country"] = country
            result["plan"] = plan
            result["email"] = auth_data.get("email", "N/A")
            result["cookie_file"] = filename

            if result.get("success") and db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except:
                    pass

            return result

        elif service == "surfshark":
            cookies = surfshark_parse_cookie_content(content)
            if not cookies:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            status, country, plan, auth_data = surfshark_validate_cookie(cookies, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
            result = surfshark_submit_tv_code(auth_data, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            result["country"] = country
            result["plan"] = plan
            result["email"] = auth_data.get("email", "N/A")
            result["cookie_file"] = filename

            if result.get("success") and db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except:
                    pass

            return result

        elif service == "spotify":
            cookies = spotify_parse_cookie_content(content)
            if not cookies:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            status, country, plan, auth_data = spotify_validate_cookie(cookies, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
            result = spotify_submit_tv_code(auth_data, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            result["country"] = country
            result["plan"] = plan
            result["email"] = auth_data.get("email", "N/A")
            result["cookie_file"] = filename
            if "tv_name" in result:
                result["tv_name"] = result["tv_name"]

            if result.get("success") and db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except:
                    pass

            return result

        elif service == "jiohotstar":
            parsed = jiohotstar_parse_cookie_content(content)
            if not parsed:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            
            usage_count = db.get_cookie_usage(filename)
            if usage_count >= 3:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
                
            status, country, plan, auth_data = jiohotstar_validate_cookie(parsed, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
                
            result = jiohotstar_submit_tv_code(auth_data, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
                
            if result.get("success"):
                new_usage = db.increment_cookie_usage(filename)
                if new_usage >= 3 or db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                    try:
                        os.remove(filepath)
                    except:
                        pass
            else:
                return result
                
            result["country"] = country or "India 🇮🇳"
            result["plan"] = plan or "Hotstar VIP"
            result["email"] = auth_data.get("email") or auth_data.get("phone") or filename.replace('.txt', '')
            result["cookie_file"] = filename
            return result

        elif service == "primevideo":
            cookies = primevideo_parse_cookie_content(content)
            if not cookies:
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                continue

            status, country, plan, auth_data = primevideo_validate_cookie(cookies, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                continue
            elif status == "network_error":
                continue

            result = primevideo_submit_tv_code(cookies, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                continue

            if not result.get("success"):
                return result

            # Fetch account subscription metadata on successful pairing
            det = primevideo_fetch_subscription_details(cookies, proxy)
            result["country"] = det.get("country") or country or "US"
            result["plan"] = det.get("plan") or plan or "Prime Video"
            result["email"] = det.get("account_name") or det.get("ubid") or filename.replace('.txt', '')
            result["cookie_file"] = filename
            if det.get("payment_method"):
                result["payment_method"] = det.get("payment_method")
            if det.get("profiles"):
                result["profiles"] = det.get("profiles")

            if db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except Exception:
                    pass

            return result

    return {"success": False, "error": "all_dead"}


async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message_id = update.message.message_id

    if not is_admin(user_id):
        await update.message.reply_text(
            "🚫 <b>Admin only!</b>",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text(
            "📎 <b>Usage:</b> Reply to a ZIP file with <code>/upload [service]</code>\n\n"
            "Supported services: <code>netflix</code>, <code>nftoken</code>, <code>crunchyroll</code>, <code>hbomax</code>, <code>surfshark</code>, <code>spotify</code>, <code>jiohotstar</code>, <code>primevideo</code>\n"
            "ZIP should contain .txt or .json cookie files.",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    doc = update.message.reply_to_message.document
    fn_lower = doc.file_name.lower()
    if not fn_lower.endswith(('.zip', '.txt', '.json')):
        await update.message.reply_text(
            "❌ Only <b>.zip</b>, <b>.txt</b>, or <b>.json</b> cookie files are accepted!",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    # Determine service
    args = context.args
    service = None
    if args:
        val = args[0].strip().lower()
        if val in SUPPORTED_SERVICES:
            service = val
        elif val in ("nftoken", "nf", "token"):
            service = "nftoken"
        elif val in ("crunchy", "crunchyroll"):
            service = "crunchyroll"
        elif val in ("hbo", "hbomax", "max", "hbo-max", "hbo_max"):
            service = "hbomax"
        elif val in ("hotstar", "jio", "jiohotstar"):
            service = "jiohotstar"
        elif val in ("surf", "surfshark"):
            service = "surfshark"
        elif val in ("spot", "spotify"):
            service = "spotify"
        elif val in ("prime", "primevideo", "amazon", "amazonprime"):
            service = "primevideo"
            
    if not service:
        # Detect from filename
        if any(k in fn_lower for k in ("nftoken", "nf_token", "nf link", "token")):
            service = "nftoken"
        elif "netflix" in fn_lower:
            service = "netflix"
        elif "crunchy" in fn_lower:
            service = "crunchyroll"
        elif "hbo" in fn_lower or "max" in fn_lower:
            service = "hbomax"
        elif "surfshark" in fn_lower or "surf" in fn_lower:
            service = "surfshark"
        elif "spotify" in fn_lower or "spot" in fn_lower:
            service = "spotify"
        elif "hotstar" in fn_lower or "jio" in fn_lower:
            service = "jiohotstar"
        elif any(k in fn_lower for k in ("prime", "amazon", "pv")):
            service = "primevideo"
        else:
            service = "netflix" # Default fallback

    target_dir = os.path.join(COOKIES_DIR, service)
    os.makedirs(target_dir, exist_ok=True)

    status_msg = await update.message.reply_text(
        f"📥 <b>Downloading for {service.upper()}...</b>",
        parse_mode=ParseMode.HTML,
        reply_to_message_id=message_id,
    )

    try:
        file = await context.bot.get_file(doc.file_id)
        file_bytes = await file.download_as_bytearray()

        added = 0
        skipped = 0

        def _validate_and_save(file_content, original_name):
            nonlocal added, skipped
            try:
                is_ok = False
                if service in ("netflix", "nftoken"):
                    is_ok = (netflix_extract_cookie_dict(file_content) is not None) or ('netflixid' in file_content.lower())
                elif service == "crunchyroll":
                    cookies = crunchyroll_parse_cookie_content(file_content)
                    is_ok = cookies is not None and 'etp_rt' in cookies
                elif service == "hbomax":
                    is_ok = hbomax_extract_st_token(file_content) is not None
                elif service == "surfshark":
                    cookies = surfshark_parse_cookie_content(file_content)
                    is_ok = '_sstk' in cookies
                elif service == "spotify":
                    cookies = spotify_parse_cookie_content(file_content)
                    is_ok = cookies is not None and len(cookies) > 0
                elif service == "jiohotstar":
                    parsed = jiohotstar_parse_cookie_content(file_content)
                    is_ok = parsed is not None and parsed.get("token") is not None
                elif service == "primevideo":
                    cookies = primevideo_parse_cookie_content(file_content)
                    is_ok = bool(cookies) or any(
                        term in file_content.lower()
                        for term in ("at-main", "session-token", "ubid-main", "session-id", "amazon", "primevideo", "prime")
                    )
                    
                if not is_ok:
                    skipped += 1
                    return

                base = os.path.basename(original_name)
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', base)
                dest = os.path.join(target_dir, safe_name)
                if os.path.exists(dest):
                    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
                    name_part, ext = os.path.splitext(safe_name)
                    dest = os.path.join(target_dir, f"{name_part}_{suffix}{ext}")
                with open(dest, 'w', encoding='utf-8') as f:
                    f.write(file_content)
                added += 1
            except Exception:
                skipped += 1

        if fn_lower.endswith('.zip'):
            await status_msg.edit_text("📂 <b>Extracting ZIP...</b>", parse_mode=ParseMode.HTML)
            with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as zf:
                for name in zf.namelist():
                    if name.endswith('/') or name.startswith('__MACOSX') or name.startswith('.'):
                        continue
                    if not name.lower().endswith(('.txt', '.json')):
                        skipped += 1
                        continue
                    try:
                        content = zf.read(name).decode('utf-8', errors='ignore')
                        _validate_and_save(content, name)
                    except Exception:
                        skipped += 1
        else:
            await status_msg.edit_text("📂 <b>Processing cookie file...</b>", parse_mode=ParseMode.HTML)
            content = file_bytes.decode('utf-8', errors='ignore')
            _validate_and_save(content, doc.file_name)

        vault_count = count_vault_cookies(service)
        await status_msg.edit_text(
            f"✅ <b>Upload complete for {service.upper()}!</b>\n\n"
            f"📥 Added: <b>{added}</b> cookies\n"
            f"⏭️ Skipped: <b>{skipped}</b>\n"
            f"🍪 Total in {service.upper()} vault: <b>{vault_count}</b>",
            parse_mode=ParseMode.HTML,
        )

        if added > 0:
            asyncio.create_task(sync_github_db_backup())

    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Error:</b> {str(e)}", parse_mode=ParseMode.HTML)


async def clearvault_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message_id = update.message.message_id

    if not is_admin(user_id):
        await update.message.reply_text(
            "🚫 <b>Admin only!</b>",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ <b>Usage:</b> <code>/clearvault &lt;service|all&gt;</code>\n\n"
            "Supported services: <code>netflix</code>, <code>crunchyroll</code>, <code>hbomax</code>, <code>surfshark</code>, <code>spotify</code>, <code>jiohotstar</code>, <code>all</code>",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    svc = args[0].strip().lower()
    if svc in ("crunchy", "crunchyroll"):
        svc = "crunchyroll"
    elif svc in ("hbo", "hbomax", "max", "hbo-max", "hbo_max"):
        svc = "hbomax"
    elif svc in ("hotstar", "jio", "jiohotstar"):
        svc = "jiohotstar"
    elif svc in ("surf", "surfshark"):
        svc = "surfshark"
    elif svc in ("spot", "spotify"):
        svc = "spotify"

    if svc != "all" and svc not in SUPPORTED_SERVICES:
        await update.message.reply_text(
            f"❌ Unknown service: <code>{svc}</code>",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    deleted_count = clear_vault_cookies(svc)
    svc_display = "ALL SERVICES" if svc == "all" else svc.upper()
    await update.message.reply_text(
        f"✅ <b>Cleared {deleted_count} cookies from {svc_display} vault!</b>",
        parse_mode=ParseMode.HTML,
        reply_to_message_id=message_id,
    )


async def userstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message_id = update.message.message_id

    if not is_admin(user_id):
        await update.message.reply_text(
            "🚫 <b>Admin only!</b>",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ <b>Usage:</b> <code>/userstats &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    target_id = args[0].strip()
    user_data = db.get_user(target_id)
    if not user_data:
        await update.message.reply_text(
            f"❌ User with ID <code>{target_id}</code> not found in database.",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    sub = user_data.get("subscription", {})
    ustats = user_data.get("stats", {})
    
    prem = db.get_user_premium(target_id)
    expiry_str = prem.get("expires_at") or sub.get("expiry") or "N/A"
    
    svc_lines = ""
    if prem.get("is_active"):
        svc_logins = prem.get("service_logins", {})
        svc_lines = (
            f"• Plan: <b>{prem.get('plan', 'Premium').capitalize()}</b>\n"
            f"• Total Logins Left: <b>{prem.get('remaining_logins', 0)}</b> across all services\n"
            f"• Per-Service Logins:\n"
            f"   ├ 🔴 Netflix: <b>{svc_logins.get('netflix', 0)}</b> | 🟠 Crunchy: <b>{svc_logins.get('crunchyroll', 0)}</b>\n"
            f"   ├ 🟣 HBO Max: <b>{svc_logins.get('hbomax', 0)}</b> | 🔵 Surfshark: <b>{svc_logins.get('surfshark', 0)}</b>\n"
            f"   └ 🟢 Spotify: <b>{svc_logins.get('spotify', 0)}</b> | 🔵 Hotstar: <b>{svc_logins.get('jiohotstar', 0)}</b>\n"
        )
    else:
        logins_used = sub.get("logins_used", 0)
        max_logins = sub.get("max_logins", 0)
        logins_left = max(0, max_logins - logins_used)
        svc_lines = f"Logins Left: <b>{logins_left}</b> ({logins_used}/{max_logins} used)\n"
    
    msg = (
        f"👤 <b>User Stats for ID:</b> <code>{target_id}</code>\n"
        f"Name: <b>{user_data.get('first_name', 'N/A')}</b>\n"
        f"Username: @{user_data.get('username', 'N/A')}\n\n"
        f"🔑 <b>Subscription:</b>\n"
        f"Expiry: <b>{expiry_str}</b>\n"
        f"{svc_lines}\n"
        f"📊 <b>Usage Statistics:</b>\n"
        f"Total attempts: <b>{ustats.get('total_attempts', 0)}</b>\n"
        f"Successful logins: <b>{ustats.get('successful_logins', 0)}</b>\n"
        f"Failed logins: <b>{ustats.get('failed_logins', 0)}</b>\n"
        f"Last Active: <b>{ustats.get('last_active') or 'Never'}</b>\n"
        f"Joined: <b>{ustats.get('joined_at', 'N/A')}</b>\n"
    )

    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.HTML,
        reply_to_message_id=message_id,
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 <b>Admin only!</b>", parse_mode=ParseMode.HTML)
        return
    await send_admin_settings(update, context)


async def send_admin_settings(update_or_query, context: ContextTypes.DEFAULT_TYPE, edit=False):
    is_query = hasattr(update_or_query, "callback_query") and update_or_query.callback_query is not None
    
    mode = db.get_setting("usage_mode", "both")
    channels = db.get_setting("force_join_channels", [])
    group_link = db.get_setting("group_link", "<i>None configured</i>")
    
    mode_labels = {
        "both": "🔄 Both (Private & Group)",
        "private_only": "👤 Private Only",
        "group_only": "👥 Group Only"
    }
    current_mode_label = mode_labels.get(mode, mode)
    
    kito_mode = db.get_setting("kito_mode", False)
    kito_mode_label = "✅ Enabled" if kito_mode else "❌ Disabled"
    
    removal_mode = db.get_setting("cookie_removal_mode", "dead_only")
    removal_mode_labels = {
        "dead_only": "💀 Delete Dead Cookies Only (Keep Working)",
        "delete_on_success": "⚡ Delete Cookies on Successful Login"
    }
    current_removal_label = removal_mode_labels.get(removal_mode, removal_mode)

    channels_text = ""
    if channels:
        for i, chan in enumerate(channels, 1):
            channels_text += f"{i}. <code>{chan}</code>\n"
    else:
        channels_text = "<i>None configured</i>\n"
        
    admins = db.get_setting("admin_ids", [])
    admins_text = ""
    if admins:
        for i, adm in enumerate(admins, 1):
            admins_text += f"{i}. <code>{adm}</code>\n"
    else:
        admins_text = "<i>No extra admins configured</i>\n"
        
    daily_token_limit = int(db.get_setting("daily_token_limit", 3))
    token_limit_display = "Unlimited ♾️" if daily_token_limit == 0 else f"{daily_token_limit} / day"

    text = (
        f"🛠️ <b>Admin Settings Panel</b>\n\n"
        f"⚙️ <b>Usage Mode:</b> {current_mode_label}\n"
        f"🗑️ <b>Cookie Removal Policy:</b>\n   └ {current_removal_label}\n"
        f"🎟️ <b>Daily NFToken Limit / User:</b> <b>{token_limit_display}</b>\n"
        f"🔗 <b>Group Link:</b> {group_link}\n"
        f"🐱 <b>Kito Mode:</b> {kito_mode_label}\n\n"
        f"📢 <b>Force Join Channels:</b>\n"
        f"{channels_text}\n"
        f"👑 <b>Extra Admin IDs:</b>\n"
        f"{admins_text}\n"
        f"Click the buttons below to change configurations."
    )
    
    keyboard = [
        [btn(text=f"⚙️ Usage Mode ({mode.upper()})", callback_data="toggle_mode", style="primary")],
        [btn(text=f"🗑️ Removal Policy ({'DEAD ONLY' if removal_mode == 'dead_only' else 'ON SUCCESS DELETE'})", callback_data="toggle_cookie_removal_mode", style="primary")],
        [btn(text=f"🎟️ Daily NFToken Limit ({token_limit_display})", callback_data="btn_set_token_limit", style="primary")],
        [btn(text="🔗 Set Group Link", callback_data="btn_set_group_link", style="primary")],
        [
            btn(text="➕ Add Channel", callback_data="btn_add_channel", style="success"),
            btn(text="➖ Remove Channel", callback_data="btn_remove_channel", style="danger")
        ],
        [
            btn(text="➕ Add Admin ID", callback_data="btn_add_admin", style="success"),
            btn(text="➖ Remove Admin ID", callback_data="btn_remove_admin", style="danger")
        ],
        [btn(text=f"🐱 Toggle Kito Mode ({'ON' if kito_mode else 'OFF'})", callback_data="toggle_kito_mode", style="primary")],
        [btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if edit and is_query:
        await update_or_query.callback_query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    else:
        message = update_or_query.callback_query.message if is_query else update_or_query.message
        await message.reply_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def send_admin_premium_settings(update_or_query, context: ContextTypes.DEFAULT_TYPE, edit=False):
    is_query = hasattr(update_or_query, "callback_query") and update_or_query.callback_query is not None
    user = update_or_query.effective_user

    plans = db.get_premium_plans()
    pay_cfg = db.get_payment_config()
    is_required = db.is_premium_required()

    req_status = "🔒 Locked (Premium Required)" if is_required else "🔓 Unlocked (Free for All)"

    m_p = plans.get("monthly", {})
    q_p = plans.get("quarterly", {})
    y_p = plans.get("yearly", {})

    paypal_cfg = db.get_paypal_config()
    pp_status = "✅ Enabled" if paypal_cfg.get("is_enabled") else "❌ Disabled"
    pp_mode = paypal_cfg.get("mode", "sandbox").upper()
    pp_usd_prices = paypal_cfg.get("usd_prices", {})

    text = (
        "💎 <b>Premium & Pricing Control Panel</b>\n\n"
        f"🛡️ <b>Access Gate:</b> <b>{req_status}</b>\n"
        f"🏦 <b>UPI ID:</b> <code>{pay_cfg.get('upi_id')}</code>\n"
        f"👤 <b>Payee Name:</b> <b>{pay_cfg.get('payee_name')}</b>\n"
        f"🔗 <b>Verify API:</b> <code>{pay_cfg.get('verify_api_url')}</code>\n"
        f"🅿️ <b>PayPal Gateway:</b> <b>{pp_status}</b> ({pp_mode})\n"
        f"   └ 💵 USD: ${pp_usd_prices.get('monthly', 1.0):.2f} / ${pp_usd_prices.get('quarterly', 2.49):.2f} / ${pp_usd_prices.get('yearly', 5.99):.2f}\n\n"
        "📦 <b>Subscription Plans Configured:</b>\n"
        f"• <b>Monthly:</b> ₹{m_p.get('price', 49):.0f} | <b>{m_p.get('logins', 10)} Logins</b> | <b>{m_p.get('days', 30)} Days</b>\n"
        f"• <b>Quarterly:</b> ₹{q_p.get('price', 129):.0f} | <b>{q_p.get('logins', 35)} Logins</b> | <b>{q_p.get('days', 90)} Days</b>\n"
        f"• <b>Yearly:</b> ₹{y_p.get('price', 399):.0f} | <b>{y_p.get('logins', 150)} Logins</b> | <b>{y_p.get('days', 365)} Days</b>\n\n"
        "Tap a button below to customize plans or payment details:"
    )

    req_btn_text = "🔓 Make Bot Free" if is_required else "🔒 Require Premium Access"
    pp_toggle_text = "🅿️ Disable PayPal" if paypal_cfg.get("is_enabled") else "🅿️ Enable PayPal"
    keyboard = [
        [btn(text=req_btn_text, callback_data="toggle_premium_gate", style="primary")],
        [
            btn(text="🎁 Grant Subscription", callback_data="admin_grant_sub_prompt", style="success"),
            btn(text="👑 Premium Users List", callback_data="admin_premium_users_list", style="primary")
        ],
        [
            btn(text=pp_toggle_text, callback_data="toggle_paypal_status", style="primary"),
            btn(text=f"🌐 Mode: {pp_mode}", callback_data="toggle_paypal_mode", style="primary")
        ],
        [
            btn(text="✏️ Edit Monthly", callback_data="edit_plan:monthly", style="success"),
            btn(text="✏️ Edit Quarterly", callback_data="edit_plan:quarterly", style="success")
        ],
        [
            btn(text="✏️ Edit Yearly", callback_data="edit_plan:yearly", style="success"),
            btn(text="🏦 Edit UPI / Payee", callback_data="edit_payment_info", style="primary")
        ],
        [
            btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit and is_query:
        await safe_edit_message(update_or_query.callback_query, context, text, reply_markup)
    else:
        message = update_or_query.callback_query.message if is_query else update_or_query.message
        await message.reply_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def send_admin_premium_users_list(update_or_query, context: ContextTypes.DEFAULT_TYPE, page: int = 1, edit: bool = True):
    is_query = hasattr(update_or_query, "callback_query") and update_or_query.callback_query is not None
    user = update_or_query.effective_user
    if not is_admin(user.id):
        return

    now = datetime.now()
    active_users = []

    with db.lock:
        all_users = db.data.get("users", {})
        for uid_str, udata in all_users.items():
            if not isinstance(udata, dict):
                continue
            prem = udata.get("premium")
            if not isinstance(prem, dict):
                continue
            if not prem.get("is_premium"):
                continue

            exp_str = prem.get("expires_at")
            is_valid = True
            if exp_str:
                try:
                    exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                    if exp_dt < now:
                        is_valid = False
                except Exception:
                    pass

            if is_valid:
                active_users.append({
                    "user_id": uid_str,
                    "first_name": udata.get("first_name") or f"User {uid_str}",
                    "username": udata.get("username") or "",
                    "plan": str(prem.get("plan", "custom")),
                    "is_trial": bool(prem.get("is_trial") or prem.get("plan") == "trial"),
                    "expires_at": exp_str or "Lifetime",
                    "remaining_logins": prem.get("remaining_logins", 0)
                })

    # Sort users: paid plans first, then by remaining logins desc
    active_users.sort(key=lambda u: (u["is_trial"], -int(u.get("remaining_logins", 0) or 0)))

    total_users = len(active_users)
    per_page = 8
    total_pages = max(1, (total_users + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_users = active_users[start_idx:end_idx]

    lines = [
        "👑 <b>All Active Premium Users</b>\n",
        f"📊 <b>Total Active Subscriptions:</b> <b>{total_users}</b>",
        f"📄 <b>Page:</b> <code>{page}/{total_pages}</code>\n",
        "────────────────────────"
    ]

    if not page_users:
        lines.append("<i>No active premium users found at this time.</i>")
    else:
        for idx, u in enumerate(page_users, start=start_idx + 1):
            uname_str = f" (@{u['username']})" if u['username'] else ""
            badge = "🎁 TRIAL" if u['is_trial'] else f"⭐ {u['plan'].upper()}"
            lines.append(
                f"{idx}. <b>{u['first_name']}</b>{uname_str}\n"
                f"   🆔 <code>{u['user_id']}</code> | <b>{badge}</b>\n"
                f"   🎟️ <b>Logins:</b> <b>{u['remaining_logins']}</b> | ⏳ <b>Exp:</b> <code>{u['expires_at']}</code>\n"
            )

    text = "\n".join(lines)

    keyboard = []
    nav_row = []
    if page > 1:
        nav_row.append(btn(text="⬅️ Prev", callback_data=f"admin_prem_page:{page - 1}", style="primary"))
    if page < total_pages:
        nav_row.append(btn(text="Next ➡️", callback_data=f"admin_prem_page:{page + 1}", style="primary"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([
        btn(text="🎁 Grant Subscription", callback_data="admin_grant_sub_prompt", style="success"),
        btn(text="🔄 Refresh", callback_data=f"admin_prem_page:{page}", style="primary")
    ])
    keyboard.append([
        btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit and is_query:
        await safe_edit_message(update_or_query.callback_query, context, text, reply_markup)
    else:
        message = update_or_query.callback_query.message if is_query else update_or_query.message
        await message.reply_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def send_admin_referral_stats(update_or_query, context: ContextTypes.DEFAULT_TYPE, page: int = 1, edit: bool = True):
    is_query = hasattr(update_or_query, "callback_query") and update_or_query.callback_query is not None
    user = update_or_query.effective_user
    if not is_admin(user.id):
        return

    ref_cfg = db.get_referral_settings()
    reward = float(ref_cfg.get("referral_reward", 25.0))

    referrers = []
    total_refs = 0
    total_distributed_reward = 0.0

    with db.lock:
        all_users = db.data.get("users", {})
        for uid_str, udata in all_users.items():
            if not isinstance(udata, dict):
                continue
            rcount = int(udata.get("referral_count", 0) or 0)
            if rcount > 0:
                total_refs += rcount
                hist = udata.get("wallet_history", [])
                earned = sum(float(x.get("amount", 0.0)) for x in hist if x.get("type") == "referral_reward")
                if earned <= 0.0:
                    earned = rcount * reward
                total_distributed_reward += earned
                referrers.append({
                    "user_id": uid_str,
                    "first_name": udata.get("first_name") or f"User {uid_str}",
                    "username": udata.get("username") or "",
                    "count": rcount,
                    "earned": earned,
                    "balance": float(udata.get("balance", 0.0))
                })

    # Sort referrers descending by total invited
    referrers.sort(key=lambda x: -x["count"])

    total_active_referrers = len(referrers)
    per_page = 8
    total_pages = max(1, (total_active_referrers + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_items = referrers[start_idx:end_idx]

    lines = [
        "📊 <b> Referral Statistics </b>\n",
        f"🎯 <b>Total Platform Referrals:</b> <b>{total_refs}</b>",
        f"👥 <b>Active Referrers:</b> <b>{total_active_referrers} Users</b>",
        f"💰 <b>Total Referral Rewards Paid:</b> <b>₹{total_distributed_reward:.2f}</b>",
        f"📄 <b>Page:</b> <code>{page}/{total_pages}</code>\n",
        "────────────────────────"
    ]

    if not page_items:
        lines.append("<i>No users have made referrals yet.</i>")
    else:
        rank_emojis = ["🥇", "🥈", "🥉"]
        for idx, r in enumerate(page_items, start=start_idx + 1):
            rank_str = rank_emojis[idx - 1] if idx <= 3 else f"<b>#{idx}</b>"
            uname_str = f" (@{r['username']})" if r['username'] else ""
            lines.append(
                f"{rank_str} <b>{r['first_name']}</b>{uname_str}\n"
                f"   🆔 <code>{r['user_id']}</code>\n"
                f"   👥 <b>Invited:</b> <b>{r['count']} users</b> | 💵 <b>Earned:</b> <b>₹{r['earned']:.2f}</b> | 👛 <b>Bal:</b> ₹{r['balance']:.2f}\n"
            )

    text = "\n".join(lines)

    keyboard = []
    nav_row = []
    if page > 1:
        nav_row.append(btn(text="⬅️ Prev", callback_data=f"admin_ref_page:{page - 1}", style="primary"))
    if page < total_pages:
        nav_row.append(btn(text="Next ➡️", callback_data=f"admin_ref_page:{page + 1}", style="primary"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([
        btn(text="🔄 Refresh Stats", callback_data=f"admin_ref_page:{page}", style="primary"),
        btn(text="🔙 Refer Settings", callback_data="admin_referral_menu", style="success")
    ])
    keyboard.append([
        btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit and is_query:
        await safe_edit_message(update_or_query.callback_query, context, text, reply_markup)
    else:
        message = update_or_query.callback_query.message if is_query else update_or_query.message
        await message.reply_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def send_user_premium_plans_menu(update_or_query, context: ContextTypes.DEFAULT_TYPE, edit=False):
    is_query = hasattr(update_or_query, "callback_query") and update_or_query.callback_query is not None
    user = update_or_query.effective_user

    prem = db.get_user_premium(user.id)
    plans = db.get_premium_plans()
    bal = db.get_user_balance(user.id)

    m_p = plans.get("monthly", {})
    q_p = plans.get("quarterly", {})
    y_p = plans.get("yearly", {})

    status_section = ""
    if prem["is_active"]:
        if prem.get("is_trial"):
            status_section = (
                "🎁 <b>Your Active Free Trial:</b>\n"
                f"• Plan: <b>1-Day Free Trial</b> ⚡\n"
                f"• 🎟️ Logins Remaining: <b>{prem.get('remaining_logins', 0)} / 10 Logins</b> (Use on ANY service!)\n"
                f"• 📅 Valid Until: <code>{prem.get('expires_at')}</code>\n\n"
                "Want more logins or separate quotas for all services? Choose a plan below:\n\n"
            )
        else:
            svc_dict = prem.get("service_logins", {})
            svc_details = (
                f"   ├ 🔴 Netflix: <b>{svc_dict.get('netflix', 0)}</b> logins\n"
                f"   ├ 🟠 Crunchyroll: <b>{svc_dict.get('crunchyroll', 0)}</b> logins\n"
                f"   ├ 🟣 HBO Max: <b>{svc_dict.get('hbomax', 0)}</b> logins\n"
                f"   ├ 🔵 Surfshark: <b>{svc_dict.get('surfshark', 0)}</b> logins\n"
                f"   ├ 🟢 Spotify: <b>{svc_dict.get('spotify', 0)}</b> logins\n"
                f"   ├ 🔵 JioHotstar: <b>{svc_dict.get('jiohotstar', 0)}</b> logins\n"
                f"   └ 🎟️ NFToken: <b>{svc_dict.get('nftoken', 0)}</b> tokens\n"
            )
            status_section = (
                "⭐ <b>Your Active Subscription:</b>\n"
                f"• Plan: <b>{prem.get('plan', 'Premium').capitalize()}</b> ✅\n"
                f"• Valid Until: <code>{prem.get('expires_at')}</code>\n"
                f"• <b>Remaining Logins (Per Service):</b>\n{svc_details}\n"
                "Want to extend validity or add more logins? Choose a plan below:\n\n"
            )
    elif prem.get("expires_at"):
        status_section = (
            "⏳ Your Previous Plan Expired:\n"
            f"• Expired On: <code>{prem.get('expires_at')}</code>\n\n"
            "Recharge below to reactivate instant access:\n\n"
        )

    paypal_cfg = db.get_paypal_config()
    usd_prices = paypal_cfg.get("usd_prices", {})
    m_usd = float(usd_prices.get("monthly", 1.00))
    q_usd = float(usd_prices.get("quarterly", 2.49))
    y_usd = float(usd_prices.get("yearly", 5.99))

    text = (
        f"<b><i>💎 XDROID TV PREMIUM MEMBERSHIP ⚡\n\n"
        f"{status_section}"
        f"🔥 Available Subscription Plans:\n\n"
        f"🗓️ Monthly Plan — ₹{m_p.get('price', 49):.0f} / ${m_usd:.2f} USD\n"
        f"• Validity: {m_p.get('days', 30)} Days | Logins: {m_p.get('logins', 10)} Logins / Service 🎟️\n\n"
        f"🗓️ Quarterly Plan — ₹{q_p.get('price', 129):.0f} / ${q_usd:.2f} USD 🔥 (Popular)\n"
        f"• Validity: {q_p.get('days', 90)} Days | Logins: {q_p.get('logins', 35)} Logins / Service 🎟️\n\n"
        f"🗓️ Yearly Plan — ₹{y_p.get('price', 399):.0f} / ${y_usd:.2f} USD 👑 (Best Value)\n"
        f"• Validity: {y_p.get('days', 365)} Days | Logins: {y_p.get('logins', 150)} Logins / Service 🎟️\n\n"
        f"⚡ <i>Har service ke liye separate quota milta hai (Netflix, Crunchyroll, HBO Max, Surfshark, Spotify, JioHotstar)!</i>\n\n"
        f"Select a plan below to activate with UPI QR or PayPal:</i></b>"
    )

    keyboard = [
        [btn(text=f"🗓️ Monthly Plan — ₹{m_p.get('price', 49):.0f} / ${m_usd:.2f}", callback_data="buy_plan:monthly", style="success")],
        [btn(text=f"🗓️ Quarterly Plan — ₹{q_p.get('price', 129):.0f} / ${q_usd:.2f}", callback_data="buy_plan:quarterly", style="success")],
        [btn(text=f"🗓️ Yearly Plan — ₹{y_p.get('price', 399):.0f} / ${y_usd:.2f}", callback_data="buy_plan:yearly", style="success")],
        [btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit and is_query:
        await safe_edit_message(update_or_query.callback_query, context, text, reply_markup)
    else:
        message = update_or_query.callback_query.message if is_query else update_or_query.message
        await message.reply_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def send_payment_method_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_key: str):
    query = update.callback_query
    user = query.from_user

    plans = db.get_premium_plans()
    plan = plans.get(plan_key)
    if not plan:
        await query.answer("❌ Invalid plan selected!", show_alert=True)
        return

    price = float(plan.get("price", 0.0))
    days = int(plan.get("days", 30))
    logins = int(plan.get("logins", 10))
    plan_name = plan.get("name", plan_key.capitalize())
    user_bal = float(db.get_user_balance(user.id))

    can_pay_wallet = user_bal >= price

    if can_pay_wallet:
        wallet_btn = btn(text=f"💳 Pay with Wallet (₹{user_bal:.1f} Available)", callback_data=f"pay_wallet:{plan_key}", style="success")
    else:
        wallet_btn = btn(text=f"💳 Pay with Wallet (₹{user_bal:.1f} - Low Balance)", callback_data=f"pay_wallet:{plan_key}", style="danger")

    paypal_cfg = db.get_paypal_config()
    usd_prices = paypal_cfg.get("usd_prices", {})
    usd_price = float(usd_prices.get(plan_key, 1.00))

    text = (
        f"<b><i>💳 Select Payment Method\n\n"
        f"📦 Selected Plan: <b>{plan_name} Plan</b>\n"
        f"⏳ Validity: <b>{days} Days</b>\n"
        f"🎟️ Logins: <b>{logins} Logins / Service</b>\n"
        f"💰 Plan Price: <b>₹{price:.0f} / ${usd_price:.2f} USD</b>\n\n"
        f"👛 Your Wallet Balance: <b>₹{user_bal:.2f}</b>\n\n"
        f"Choose how you want to pay for your subscription:</i></b>"
    )

    keyboard = [
        [wallet_btn],
        [btn(text=f"📱 Pay with UPI QR (₹{price:.2f})", callback_data=f"pay_upi:{plan_key}", style="primary")]
    ]
    if paypal_cfg.get("is_enabled", True) and paypal_cfg.get("is_configured", True):
        keyboard.append([btn(text=f"🅿️ Pay with PayPal (${usd_price:.2f} USD)", callback_data=f"pay_paypal:{plan_key}", style="success")])
    keyboard.append([btn(text="❌ Cancel Payment", callback_data="menu_premium", style="danger")])

    await safe_edit_message(query, context, text, InlineKeyboardMarkup(keyboard))


async def handle_buy_plan_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_key: str):
    query = update.callback_query
    user = query.from_user

    plans = db.get_premium_plans()
    plan = plans.get(plan_key)
    if not plan:
        await query.answer("❌ Invalid plan selected!", show_alert=True)
        return

    price = float(plan.get("price", 0.0))
    days = int(plan.get("days", 30))
    logins = int(plan.get("logins", 10))
    plan_name = plan.get("name", plan_key.capitalize())

    # Check user balance
    curr_bal = db.get_user_balance(user.id)
    if curr_bal < price:
        await query.answer(f"❌ Insufficient balance! You have ₹{curr_bal:.2f}, but need ₹{price:.2f}.", show_alert=True)
        return

    # Deduct balance
    success, new_bal = db.deduct_user_balance(
        user.id, price,
        desc=f"Purchased {plan_name} Plan ({days}d / {logins} logins)"
    )
    if not success:
        await query.answer(f"❌ Insufficient balance! You have ₹{new_bal:.2f}, but need ₹{price:.2f}.", show_alert=True)
        return

    # Activate premium plan
    prem = db.activate_user_premium(user.id, plan_key, days, logins)

    # Record order in db
    order_id = generate_order_id()
    db.create_order(order_id, user.id, user.username, plan_key, price, logins, days)
    db.update_order_status(order_id, "completed", utr=f"WALLET_{int(time.time())}")

    success_text = (
        f"🎉 <b>TV LOGIN ACTIVATED FOR FREE!</b> ⚡\n\n"
        f"Your <b>{plan_name} Plan</b> was successfully activated using your <b>Wallet Balance</b>!\n\n"
        f"• 🎟️ <b>Logins Added:</b> +{logins} Logins / Service (Total: <b>{prem['remaining_logins']}</b> across all services)\n"
        f"• 📅 <b>Valid Until:</b> <code>{prem['expires_at']}</code>\n"
        f"• 💰 <b>Deducted:</b> ₹{price:.2f}\n"
        f"• 💳 <b>Remaining Balance:</b> <b>₹{new_bal:.2f}</b>\n\n"
        f"🚀 Head over to <b>Activate TV</b> to log into Netflix, HBO Max, Crunchyroll & more!"
    )
    keyboard = [
        [btn(text="📺 Activate TV Now", callback_data="menu_activate", style="success")],
        [btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")]
    ]
    await safe_edit_message(query, context, success_text, InlineKeyboardMarkup(keyboard))


async def handle_buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_key: str):
    query = update.callback_query
    user = query.from_user

    plans = db.get_premium_plans()
    plan = plans.get(plan_key)
    if not plan:
        await query.answer("❌ Invalid plan selected!", show_alert=True)
        return

    pay_cfg = db.get_payment_config()
    upi_id = pay_cfg.get("upi_id", "sudhirxd@fam")
    payee_name = pay_cfg.get("payee_name", "Sudhir kumar")
    price = float(plan.get("price", 49.0))
    days = int(plan.get("days", 30))
    logins = int(plan.get("logins", 10))
    plan_name = plan.get("name", plan_key.capitalize())
    bal = db.get_user_balance(user.id)

    # Generate 18-digit unique Order ID
    order_id = generate_order_id()
    db.create_order(order_id, user.id, user.username, plan_key, price, logins, days)

    if context.user_data is not None:
        context.user_data["awaiting_utr_order_id"] = order_id

    # Generate QR Code buffer
    qr_bio = generate_payment_qr(upi_id, payee_name, price, order_id)

    caption = (
        f"<b><i>💳 Scan & Pay to Activate Premium\n\n"
        f"📦 Plan: {plan_name} ({days} Days / {logins} Logins)\n"
        f"💰 Amount: ₹{price:.2f} (tap to copy: <code>{price:.2f}</code>)\n"
        f"🆔 Order ID: <code>{order_id}</code>\n\n"
        f"📌 Payment Instructions:\n"
        f"1️⃣ Open PhonePe, Google Pay, or Paytm.\n"
        f"2️⃣ Scan the QR code image above to complete payment.\n"
        f"3️⃣ Wait 30-60 sec, will automatically verify your payment.\n"
        f"4️⃣ If you pay and payment not verified yet, please send UTR or Trans ID.\n\n"
        f"⏳ Note: QR code is valid for 5 minutes only.\n"
        f"⚡ Instant auto-verification will activate your premium in seconds!</i></b>"
    )

    keyboard = [
        [btn(text="✍️ Submit UTR / Txn ID", callback_data=f"submit_utr:{order_id}", style="success")],
        [btn(text="🔄 Check Payment Status", callback_data=f"check_order:{order_id}", style="primary")],
        [btn(text="❌ Cancel Payment", callback_data=f"cancel_payment:{order_id}", style="danger")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        msg = await context.bot.send_photo(
            chat_id=query.message.chat.id,
            photo=qr_bio,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        track_last_bot_message(context, msg)

        # Cancel any previous task for this order
        old_task = ACTIVE_PAYMENT_TASKS.get(order_id)
        if old_task and not old_task.done():
            old_task.cancel()

        # Start 5s background auto-verification loop
        task = asyncio.create_task(
            auto_check_payment_loop(
                bot=context.bot,
                chat_id=query.message.chat.id,
                message_id=msg.message_id,
                order_id=order_id,
                user_id=user.id,
                first_name=user.first_name,
                username=user.username or ""
            )
        )
        ACTIVE_PAYMENT_TASKS[order_id] = task

        try:
            await query.message.delete()
        except Exception:
            pass
    except Exception as e:
        print(f"[!] Error sending payment QR: {e}")
        await query.message.reply_text(
            f"❌ <b>Error generating QR:</b> {e}",
            parse_mode=ParseMode.HTML
        )


async def handle_buy_plan_paypal(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_key: str):
    query = update.callback_query
    user = query.from_user

    plans = db.get_premium_plans()
    plan = plans.get(plan_key)
    if not plan:
        await query.answer("❌ Invalid plan selected!", show_alert=True)
        return

    pay_cfg = db.get_paypal_config()
    if not pay_cfg.get("is_enabled", False):
        await query.answer("❌ PayPal payment is currently not configured or disabled.", show_alert=True)
        return

    usd_prices = pay_cfg.get("usd_prices", {})
    usd_price = float(usd_prices.get(plan_key, 1.00))
    currency = pay_cfg.get("currency", "USD")
    days = int(plan.get("days", 30))
    logins = int(plan.get("logins", 10))
    plan_name = plan.get("name", plan_key.capitalize())

    try:
        await query.answer("⚡ Generating PayPal checkout link...")
    except Exception:
        pass

    paypal_client = PayPalClient()
    bot_order_id = generate_order_id()

    order_res = await asyncio.to_thread(
        paypal_client.create_order,
        amount=usd_price,
        currency=currency,
        description=f"{plan_name} Plan - TV Login",
        custom_id=f"{user.id}:{bot_order_id}"
    )

    if not order_res.get("success") or not order_res.get("checkout_url"):
        err = order_res.get("error", "Unknown error")
        await query.message.reply_text(
            f"❌ <b>Error creating PayPal checkout:</b>\n<code>{err}</code>",
            parse_mode=ParseMode.HTML
        )
        return

    paypal_order_id = order_res.get("order_id")
    checkout_url = order_res.get("checkout_url")

    db.create_order(
        order_id=bot_order_id,
        user_id=user.id,
        username=user.username,
        plan_key=plan_key,
        amount=usd_price,
        logins=logins,
        days=days,
        payment_method="paypal",
        paypal_order_id=paypal_order_id,
        currency=currency
    )

    mode_label = "Sandbox (Test Mode)" if paypal_client.sandbox else "Live Secure"

    text = (
        f"<b><i>🅿️ PayPal Instant Auto-Verification\n\n"
        f"📦 Selected Plan: <b>{plan_name} Plan</b>\n"
        f"⏳ Validity: <b>{days} Days</b> | 🎟️ <b>{logins} Logins / Service</b>\n"
        f"💰 Amount to Pay: <b>${usd_price:.2f} {currency}</b>\n"
        f"🆔 Order ID: <code>{bot_order_id}</code>\n"
        f"🌐 Environment: <code>{mode_label}</code>\n\n"
        f"📌 Instructions:\n"
        f"1️⃣ Click <b>🅿️ Pay with PayPal</b> below to open PayPal checkout.\n"
        f"2️⃣ Approve & complete payment in your browser.\n"
        f"3️⃣ Bot automatically captures & verifies your payment in seconds!\n\n"
        f"⚡ No manual confirmation required. Your subscription will activate automatically.</i></b>"
    )

    keyboard = [
        [btn(text=f"🅿️ Pay with PayPal (${usd_price:.2f})", url=checkout_url, style="success")],
        [btn(text="🔄 Check Payment Status", callback_data=f"check_paypal:{bot_order_id}", style="primary")],
        [btn(text="❌ Cancel Payment", callback_data=f"cancel_payment:{bot_order_id}", style="danger")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        msg = await query.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        track_last_bot_message(context, msg)

        # Cancel any previous task for this order
        old_task = ACTIVE_PAYMENT_TASKS.get(bot_order_id)
        if old_task and not old_task.done():
            old_task.cancel()

        # Start 6s background auto-verification loop
        task = asyncio.create_task(
            auto_check_paypal_payment_loop(
                bot=context.bot,
                chat_id=query.message.chat.id,
                message_id=msg.message_id,
                bot_order_id=bot_order_id,
                paypal_order_id=paypal_order_id,
                user_id=user.id,
                first_name=user.first_name,
                username=user.username or "",
                plan_key=plan_key,
                usd_amount=usd_price
            )
        )
        ACTIVE_PAYMENT_TASKS[bot_order_id] = task

        try:
            await query.message.delete()
        except Exception:
            pass
    except Exception as e:
        print(f"[!] Error sending PayPal checkout message: {e}")


async def handle_check_paypal_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    query = update.callback_query
    user = query.from_user

    order = db.get_order(order_id)
    if not order:
        await query.answer("❌ Order not found or expired!", show_alert=True)
        return

    status = order.get("status", "pending")
    if status == "completed":
        await query.answer("✅ Order is already completed & subscription is active!", show_alert=True)
        return

    paypal_order_id = order.get("paypal_order_id")
    if not paypal_order_id:
        await query.answer("❌ PayPal Order reference missing.", show_alert=True)
        return

    await query.answer("🔄 Checking PayPal payment status...", show_alert=False)

    paypal_client = PayPalClient()
    success, pp_status, data = await asyncio.to_thread(paypal_client.get_order_status, paypal_order_id)

    if success and pp_status == "APPROVED":
        captured, cap_msg, cap_data = await asyncio.to_thread(paypal_client.capture_order, paypal_order_id)
        if captured:
            pp_status = "COMPLETED"
            data = cap_data

    if success and pp_status == "COMPLETED":
        capture_id = paypal_order_id
        try:
            captures = data.get("purchase_units", [{}])[0].get("payments", {}).get("captures", [])
            if captures:
                capture_id = captures[0].get("id", paypal_order_id)
        except Exception:
            pass

        db.update_order_status(order_id, "completed", utr=capture_id)
        prem = db.activate_user_premium(
            user.id,
            order["plan_key"],
            int(order.get("days", 30)),
            int(order.get("logins", 10))
        )

        task = ACTIVE_PAYMENT_TASKS.pop(order_id, None)
        if task and not task.done():
            task.cancel()

        try:
            await query.message.delete()
        except Exception:
            pass

        success_text = (
            "<b><i>🎉 PayPal Payment Verified Successfully!\n\n"
            "💎 Your Premium Subscription is now ACTIVE! ⚡\n\n"
            f"• Plan: {order['plan_key'].capitalize()}\n"
            f"• Validity Added: +{order['days']} Days (Until: <code>{prem['expires_at']}</code>)\n"
            f"• Logins Added: +{order['logins']} Logins 🎟️ (Total: {prem['remaining_logins']})\n"
            f"• Order ID: <code>{order_id}</code>\n"
            f"• PayPal Txn ID: <code>{capture_id}</code>\n\n"
            "🚀 You can now activate any TV service or generate Netflix login links instantly!</i></b>"
        )
        keyboard = [
            [btn(text="📺 Activate TV Now", callback_data="menu_activate", style="success")],
            [btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")]
        ]
        await query.message.reply_text(
            success_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        user_handle = f"@{user.username}" if user.username else "No username"
        admin_notif = (
            "🔔 <b>[PayPal Auto-Payment Verified (Manual Refresh)]</b>\n\n"
            f"👤 <b>User:</b> {user.first_name} ({user_handle} | <code>{user.id}</code>)\n"
            f"📦 <b>Plan:</b> {order['plan_key'].capitalize()} (${float(order.get('amount', 1.0)):.2f} USD)\n"
            f"🆔 <b>Order:</b> <code>{order_id}</code>\n"
            f"🧾 <b>PayPal Txn ID:</b> <code>{capture_id}</code>\n"
            "⚡ <b>Status:</b> Auto-Activated ✅"
        )
        for adm_id in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=adm_id, text=admin_notif, parse_mode=ParseMode.HTML)
            except Exception:
                pass
    elif success and pp_status == "CREATED":
        await query.answer("⏳ Waiting for PayPal payment approval. Please complete checkout on PayPal first!", show_alert=True)
    else:
        msg = data.get("message") if isinstance(data, dict) else str(pp_status)
        await query.answer(f"Status: {pp_status}. {msg}", show_alert=True)



async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global proxies_list
    query = update.callback_query
    user = query.from_user
    data = query.data
    
    allowed, err_msg = check_chat_mode(update)
    if not allowed:
        if err_msg:
            try:
                await query.answer(text=re.sub('<[^<]+?>', '', err_msg), show_alert=True)
            except Exception:
                pass
        return

    # Don't answer immediately for activation callbacks — they take too long
    # and the query would expire before the response comes back.
    # For everything else, answer fast to dismiss the loading indicator.
    if not data.startswith("activate:"):
        try:
            await query.answer()
        except Exception:
            pass  # Query already expired or answered, no biggie

    # Admin Ads Gate Toggle Button Callback
    if data == "toggle_ads_gate":
        if not is_admin(user.id):
            try:
                await query.answer("Access Denied: SuperAdmin Only.", show_alert=True)
            except Exception:
                pass
            return
        new_state = bot_db.toggle_ads_gate()
        state_msg = "Ads Gate: ENABLED 🟢 (Tokens & Ads Required)" if new_state else "Ads Gate: DISABLED 🔴 (Free Access / No Tokens Needed)"
        try:
            await query.answer(state_msg, show_alert=True)
        except Exception:
            pass
        text = get_admin_main_text(user.first_name)
        keyboard = get_admin_main_keyboard()
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except Exception:
            await query.message.reply_html(text, reply_markup=keyboard)
        return

    # Route other admin callbacks to admin_callback_query_handler
    if data.startswith("admin_") or data.startswith("adm_") or (is_admin(user.id) and data in ("menu_admin", "adm_home")):
        return await admin_callback_query_handler(update, context)

    # Dynamic TV Activation Selection Callback
    if data.startswith("activate:"):
        # Answer the callback query NOW before the long activation process
        try:
            await query.answer(text="⚡ Starting activation...")
        except Exception:
            pass  # Query expired, but we still proceed with activation
        parts = data.split(":")
        if len(parts) == 3:
            service = parts[1]
            tv_code = parts[2]
            await start_tv_activation(update, context, service, tv_code)
        return

    if data == "btn_activate:netflix":
        keyboard = [
            [
                btn(text="📺 TV Login", callback_data="netflix_mode:tv", style="success"),
                btn(text="💻/📱 PC/Phone Login", callback_data="gen_netflix_link", style="primary")
            ],
            [
                btn(text="🔙 Back", callback_data="menu_activate", style="primary")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await safe_edit_message(
            query,
            context,
            "🔴 <b>Netflix Options:</b>\n\nPlease select your preferred login method:",
            reply_markup
        )
        return

    if data == "netflix_mode:tv":
        if context.user_data is not None:
            context.user_data["selected_service"] = "netflix"
        await query.message.reply_text(
            "✍️ <b>Enter your NETFLIX TV code (8 digits):</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(selective=True)
        )
        return

    if data == "btn_activate:spotify":
        keyboard = [
            [
                btn(text="📺 TV Login", callback_data="spotify_mode:tv", style="success"),
                btn(text="📸 PC/QR Login", callback_data="spotify_mode:qr", style="primary")
            ],
            [
                btn(text="🔙 Back", callback_data="menu_activate", style="primary")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await safe_edit_message(
            query,
            context,
            "🟢 <b>Spotify Options:</b>\n\nPlease select your preferred login method:",
            reply_markup
        )
        return

    if data == "spotify_mode:tv":
        if context.user_data is not None:
            context.user_data["selected_service"] = "spotify"
        await query.message.reply_text(
            "✍️ <b>Enter your SPOTIFY TV code:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(selective=True)
        )
        return

    if data == "spotify_mode:qr":
        if context.user_data is not None:
            context.user_data["selected_service"] = "spotify"
        await query.message.reply_text(
            "📸 <b>Please reply to this message with your Spotify QR code image:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(selective=True)
        )
        return

    if data.startswith("btn_activate:"):
        service = data.split(":")[1]
        if service == "jiohotstar":
            if context.user_data is not None:
                context.user_data["selected_service"] = service
            await query.message.reply_text(
                "📸 <b>Please reply to this message with your JioHotstar QR code image:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )
            return
            
        service_label = service.upper()
        if service == "hbomax":
            service_label = "HBO MAX"
        elif service == "primevideo":
            service_label = "PRIME VIDEO"
        
        if context.user_data is not None:
            context.user_data["selected_service"] = service
        
        prompt_txt = f"✍️ <b>Enter your PRIME VIDEO TV code (6 characters from TV):</b>" if service == "primevideo" else f"✍️ <b>Enter your {service_label} TV code:</b>"
        await query.message.reply_text(
            prompt_txt,
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(selective=True)
        )
        return

    # User Interactive Main Menu callbacks
    if data == "menu_main":
        await send_main_menu(update, context, edit=True)

    elif data == "menu_referral":
        await send_referral_menu(update, context, edit=True)

    elif data == "menu_premium":
        await send_user_premium_plans_menu(update, context, edit=True)

    elif data.startswith("buy_plan:"):
        plan_key = data.split(":", 1)[1]
        await send_payment_method_menu(update, context, plan_key)

    elif data.startswith("pay_wallet:") or data.startswith("buy_plan_wallet:"):
        plan_key = data.split(":", 1)[1]
        await handle_buy_plan_wallet(update, context, plan_key)

    elif data.startswith("pay_upi:"):
        plan_key = data.split(":", 1)[1]
        await handle_buy_plan(update, context, plan_key)

    elif data.startswith("pay_paypal:"):
        plan_key = data.split(":", 1)[1]
        await handle_buy_plan_paypal(update, context, plan_key)

    elif data.startswith("check_paypal:"):
        order_id = data.split(":", 1)[1]
        await handle_check_paypal_order(update, context, order_id)

    elif data.startswith("cancel_payment:"):
        order_id = data.split(":", 1)[1]
        if order_id and order_id != "none":
            task = ACTIVE_PAYMENT_TASKS.pop(order_id, None)
            if task and not task.done():
                task.cancel()
            db.update_order_status(order_id, "cancelled")
            
        if context.user_data is not None:
            context.user_data.pop("awaiting_utr_order_id", None)
            
        await query.answer("❌ Payment cancelled.")
        try:
            await query.message.delete()
        except Exception:
            pass
        await send_user_premium_plans_menu(update, context, edit=False)

    elif data.startswith("submit_utr:"):
        order_id = data.split(":", 1)[1]
        order = db.get_order(order_id)
        if not order:
            await query.answer("❌ Order not found or expired!", show_alert=True)
            return
        if order.get("status") == "completed":
            await query.answer("✅ This order has already been completed!", show_alert=True)
            return
        if context.user_data is not None:
            context.user_data["awaiting_utr_order_id"] = order_id
        await query.message.reply_text(
            f"<b><i>✍️ Submit Payment Reference for Order <code>{order_id}</code>:\n\n"
            f"Please reply to this message with your 12-digit UPI UTR / Transaction ID (e.g. <code>123456789012</code>):\n\n"
            f"💰 Expected Amount: ₹{order['amount']:.2f}</i></b>",
            parse_mode=ParseMode.HTML,
            reply_markup=ForceReply(selective=True)
        )

    elif data.startswith("check_order:"):
        order_id = data.split(":", 1)[1]
        order = db.get_order(order_id)
        if not order:
            await query.answer("❌ Order not found!", show_alert=True)
            return
        status = order.get("status", "pending")
        if status == "completed":
            await query.answer("✅ Order is Completed & Active!", show_alert=True)
            await send_user_premium_plans_menu(update, context, edit=True)
            return
        elif status == "rejected":
            await query.answer("❌ Order was Rejected by Admin.", show_alert=True)
            return

        if order.get("payment_method") == "paypal":
            await handle_check_paypal_order(update, context, order_id)
            return

        await query.answer("🔄 Checking payment status...", show_alert=False)
        amount = float(order.get("amount", 0))
        verified, ver_msg, detail = await asyncio.to_thread(verify_fampay_order_payment, order_id, amount)
        if verified:
            txn_id = detail.get("txn_id", "")
            db.update_order_status(order_id, "completed", utr=txn_id)
            if txn_id:
                db.record_redeemed_utr(txn_id, user.id, order_id)
            prem = db.activate_user_premium(
                user.id,
                order["plan_key"],
                int(order.get("days", 30)),
                int(order.get("logins", 10))
            )

            task = ACTIVE_PAYMENT_TASKS.pop(order_id, None)
            if task and not task.done():
                task.cancel()

            try:
                await query.message.delete()
            except Exception:
                pass

            success_text = (
                "<b><i>🎉 Payment Verified Successfully!\n\n"
                "💎 Your Premium Subscription is now ACTIVE! ⚡\n\n"
                f"• Plan: {order['plan_key'].capitalize()}\n"
                f"• Validity Added: +{order['days']} Days (Until: <code>{prem['expires_at']}</code>)\n"
                f"• Logins Added: +{order['logins']} Logins / Service 🎟️ (Total: {prem['remaining_logins']} across all services)\n"
                f"• Order ID: <code>{order_id}</code>\n"
                f"• Txn ID: <code>{txn_id}</code>\n\n"
                "🚀 You can now activate any TV service or generate Netflix login links instantly!</i></b>"
            )
            keyboard = [
                [btn(text="📺 Activate TV Now", callback_data="menu_activate", style="success")],
                [btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")]
            ]
            await query.message.reply_text(success_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

            user_handle = f"@{user.username}" if user.username else "No username"
            admin_notif = (
                "🔔 <b>[Manual-Check Payment Verified]</b>\n\n"
                f"👤 <b>User:</b> {user.first_name} ({user_handle} | <code>{user.id}</code>)\n"
                f"📦 <b>Plan:</b> {order['plan_key'].capitalize()} (₹{amount:.2f})\n"
                f"🆔 <b>Order:</b> <code>{order_id}</code>\n"
                f"🧾 <b>Txn ID:</b> <code>{txn_id}</code>\n"
                "⚡ <b>Status:</b> Auto-Activated ✅"
            )
            for adm_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(chat_id=adm_id, text=admin_notif, parse_mode=ParseMode.HTML)
                except Exception:
                    pass
        else:
            if status == "failed":
                await query.answer("❌ QR Expired / Payment Failed.\nAgar aapne pay kiya hai to please 'Submit UTR' par click karein.", show_alert=True)
            else:
                await query.answer("⏳ Payment not detected yet.\nAgar aapne payment kar diya hai to 5-10 seconds wait karein ya UTR submit karein.", show_alert=True)

    elif data.startswith("req_admin_review:"):
        parts = data.split(":")
        order_id = parts[1]
        submitted_utr = parts[2] if len(parts) > 2 else "N/A"
        order = db.get_order(order_id)
        if not order:
            await query.answer("❌ Order not found!", show_alert=True)
            return
        await query.answer("✅ Request forwarded to Admins!", show_alert=True)
        await query.edit_message_text(
            f"📨 <b>Admin Review Requested!</b>\n\n"
            f"Your order <code>{order_id}</code> with reference <code>{submitted_utr}</code> has been forwarded to Admins for manual confirmation.\n\n"
            f"You will receive an automated alert here as soon as approved.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")]])
        )
        user_handle = f"@{user.username}" if user.username else "No username"
        admin_req_text = (
            "🚨 <b>Manual Payment Approval Request:</b>\n\n"
            f"👤 <b>User:</b> {user.first_name} ({user_handle} | <code>{user.id}</code>)\n"
            f"📦 <b>Plan:</b> {order['plan_key'].capitalize()} (₹{order['amount']:.2f})\n"
            f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n"
            f"🧾 <b>Submitted UTR:</b> <code>{submitted_utr}</code>\n"
            f"📅 <b>Date:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        admin_keyboard = [
            [
                btn(text="✅ Approve Order", callback_data=f"admin_approve_order:{order_id}:{submitted_utr}", style="success"),
                btn(text="❌ Reject Order", callback_data=f"admin_reject_order:{order_id}", style="danger")
            ]
        ]
        for adm_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=adm_id,
                    text=admin_req_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(admin_keyboard)
                )
            except Exception:
                pass
        
    elif data in ("gen_netflix_link", "btn_gen_link:netflix"):
        has_access, reason = user_has_access(user.id, "nftoken")
        if not has_access:
            await send_premium_required_message(query, context, reason, service="nftoken")
            return
        await handle_netflix_login_link_request(update, context)

    elif data == "menu_activate":
        has_access, reason = user_has_access(user.id)
        if not has_access:
            await send_premium_required_message(query, context, reason)
            return
        # Display service selection menu immediately
        keyboard = [
            [
                btn(text="🔴 Netflix", callback_data="btn_activate:netflix", style="success"),
                btn(text="🟠 Crunchyroll", callback_data="btn_activate:crunchyroll", style="success")
            ],
            [
                btn(text="🟣 HBO Max", callback_data="btn_activate:hbomax", style="success"),
                btn(text="🔵 Surfshark", callback_data="btn_activate:surfshark", style="success")
            ],
            [
                btn(text="🟢 Spotify", callback_data="btn_activate:spotify", style="success"),
                btn(text="🔵 JioHotstar", callback_data="btn_activate:jiohotstar", style="success")
            ],
            [
                btn(text="🎬 Prime Video", callback_data="btn_activate:primevideo", style="success")
            ],
            [
                btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await safe_edit_message(
            query,
            context,
            "📺 <b>Select the Service you want to activate:</b>",
            reply_markup
        )
        
    elif data == "menu_help":
        help_text = (
            "📖 <b>How to Use TV Activation Bot</b>\n\n"
            "This bot helps you activate TV apps using working cookies from our vault.\n\n"
            "🎁 <b>New User Free Trial:</b>\n"
            "• Har new user ko milta hai <b>1-Day Free Trial with 10 Logins</b>.\n"
            "• Aap ye 10 logins apni marzi se <b>kisi bhi service</b> me use kar sakte ho (Netflix, Crunchyroll, HBO Max, Spotify, Surfshark, JioHotstar)!\n\n"
            "👥 <b>Refer & Earn Free TV Logins:</b>\n"
            "• Apne dosto ko referral link share karein.\n"
            "• Har referral par aapke wallet me cash reward add hoga!\n"
            "• Wallet cash se aap direct Premium plans <b>100% FREE</b> buy kar sakte hain!\n\n"
            "💡 <b>Steps to Activate:</b>\n"
            "1️⃣ Open the app on your TV (Netflix, Crunchyroll, HBO Max, Surfshark, Spotify, JioHotstar).\n"
            "2️⃣ Get the activation/login code from the TV screen.\n"
            "3️⃣ Go to Main Menu, click <b>📺 Activate TV</b>, and reply with the code.\n"
            "4️⃣ Select the service from the buttons that appear.\n\n"
            "👑 <b>Support:</b>\n"
            "• Contact admin: @Sudhirxd"
        )
        keyboard = [[btn(text="🔙 Back to Main Menu", callback_data="menu_main", style="primary")]]
        await safe_edit_message(query, context, help_text, InlineKeyboardMarkup(keyboard))
        
    elif data == "menu_admin" and is_admin(user.id):
        admin_text = (
            "👑 <b>Admin Control Panel</b>\n\n"
            "Select an administrative option from the choices below:"
        )
        await safe_edit_message(query, context, admin_text, make_admin_keyboard())

    # Admin sub-menu callbacks
    elif is_admin(user.id):
        if data == "admin_referral_menu":
            ref_cfg = db.get_referral_settings()
            tot_users = len(db.data.get("users", {}))
            tot_wallet = sum(float(u.get("balance", 0.0)) for u in db.data.get("users", {}).values())
            
            all_users = db.data.get("users", {})
            total_referrals = sum(int(u.get("referral_count", 0) or 0) for u in all_users.values() if isinstance(u, dict))
            active_referrers = sum(1 for u in all_users.values() if isinstance(u, dict) and int(u.get("referral_count", 0) or 0) > 0)

            text = (
                "👥 <b>Referral & Wallet Configuration</b>\n\n"
                f"• 🎁 <b>Reward per Referral:</b> <b>₹{ref_cfg['referral_reward']:.2f}</b>\n"
                f"• 🌟 <b>New User Welcome Bonus:</b> <b>₹{ref_cfg['welcome_bonus']:.2f}</b>\n"
                f"• 👥 <b>Total Registered Users:</b> <b>{tot_users}</b>\n"
                f"• 🎯 <b>Total Successful Referrals:</b> <b>{total_referrals}</b>\n"
                f"• 👤 <b>Active Referrers:</b> <b>{active_referrers} Users</b>\n"
                f"• 💰 <b>Total Wallet Distributed:</b> <b>₹{tot_wallet:.2f}</b>\n\n"
                "Select an option to view stats, update settings or credit user balances:"
            )
            keyboard = [
                [btn(text="📊 View Detailed Referral Stats & Top Referrers", callback_data="admin_referral_stats", style="primary")],
                [btn(text="✏️ Change Refer Reward", callback_data="admin_set_refer_reward", style="primary")],
                [btn(text="✏️ Change Welcome Bonus", callback_data="admin_set_welcome_bonus", style="primary")],
                [btn(text="➕ Credit User Wallet", callback_data="admin_credit_user_prompt", style="success")],
                [btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")]
            ]
            await safe_edit_message(query, context, text, InlineKeyboardMarkup(keyboard))

        elif data == "admin_referral_stats" or data.startswith("admin_ref_page:"):
            page = 1
            if ":" in data:
                try:
                    page = int(data.split(":", 1)[1])
                except Exception:
                    page = 1
            await send_admin_referral_stats(update, context, page=page, edit=True)

        elif data == "admin_set_refer_reward":
            await query.message.reply_text(
                "✍️ <b>Set Referral Reward:</b>\n\n"
                "Reply to this message with the reward amount in INR given to the referrer per new user (e.g. <code>25</code> or <code>50</code>):",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )

        elif data == "admin_set_welcome_bonus":
            await query.message.reply_text(
                "✍️ <b>Set New User Welcome Bonus:</b>\n\n"
                "Reply to this message with the starting signup balance in INR for new users (e.g. <code>100</code>):",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )

        elif data == "admin_credit_user_prompt":
            await query.message.reply_text(
                "✍️ <b>Credit User Wallet:</b>\n\n"
                "Reply to this message with <code>&lt;user_id&gt; &lt;amount&gt;</code>\n"
                "Example: <code>5784912530 100</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )

        elif data == "admin_premium_menu":
            await send_admin_premium_settings(update, context, edit=True)

        elif data == "admin_premium_users_list" or data.startswith("admin_prem_page:"):
            page = 1
            if ":" in data:
                try:
                    page = int(data.split(":", 1)[1])
                except Exception:
                    page = 1
            await send_admin_premium_users_list(update, context, page=page, edit=True)

        elif data == "toggle_premium_gate":
            curr = db.is_premium_required()
            db.set_premium_required(not curr)
            await query.answer(f"Premium Gate set to: {'LOCKED (Premium Only)' if not curr else 'UNLOCKED (Free for All)'}")
            await send_admin_premium_settings(update, context, edit=True)

        elif data == "toggle_paypal_status":
            pp_cfg = db.get_paypal_config()
            new_val = not pp_cfg.get("is_enabled", True)
            db.set_paypal_config(is_enabled=new_val)
            await query.answer(f"🅿️ PayPal set to: {'ENABLED ✅' if new_val else 'DISABLED ❌'}")
            await send_admin_premium_settings(update, context, edit=True)

        elif data == "toggle_paypal_mode":
            pp_cfg = db.get_paypal_config()
            curr_mode = pp_cfg.get("mode", "sandbox").lower()
            new_mode = "live" if curr_mode == "sandbox" else "sandbox"
            db.set_paypal_config(mode=new_mode)
            await query.answer(f"🌐 PayPal mode switched to: {new_mode.upper()}")
            await send_admin_premium_settings(update, context, edit=True)

        elif data.startswith("edit_plan:"):
            plan_key = data.split(":", 1)[1]
            plans = db.get_premium_plans()
            plan = plans.get(plan_key, {})
            paypal_cfg = db.get_paypal_config()
            usd_prices = paypal_cfg.get("usd_prices", {})
            curr_usd = float(usd_prices.get(plan_key, 1.00))
            text = (
                f"📦 <b>Configure {plan.get('name', plan_key.capitalize())} Plan:</b>\n\n"
                f"• 💰 Price: <b>₹{plan.get('price', 0):.0f} / ${curr_usd:.2f} USD</b>\n"
                f"• 🎟️ Logins Allowed: <b>{plan.get('logins', 0)} Logins</b>\n"
                f"• 📅 Validity: <b>{plan.get('days', 0)} Days</b>\n\n"
                "💡 <i>Tip: Price change karte waqt aap <code>INR/USD</code> format (e.g. <code>2500/25</code> ya <code>49/1</code>) me bhej sakte hain.</i>\n\n"
                "Select an attribute to modify:"
            )
            keyboard = [
                [btn(text=f"💰 Change Price (₹{plan.get('price', 0):.0f} / ${curr_usd:.2f})", callback_data=f"set_plan_price:{plan_key}", style="primary")],
                [btn(text="🎟️ Change Logins Quota", callback_data=f"set_plan_logins:{plan_key}", style="primary")],
                [btn(text="📅 Change Validity Days", callback_data=f"set_plan_days:{plan_key}", style="primary")],
                [btn(text="🔙 Back to Premium Settings", callback_data="admin_premium_menu", style="primary")]
            ]
            await safe_edit_message(query, context, text, InlineKeyboardMarkup(keyboard))

        elif data.startswith("set_plan_price:"):
            plan_key = data.split(":", 1)[1]
            paypal_cfg = db.get_paypal_config()
            usd_prices = paypal_cfg.get("usd_prices", {})
            curr_usd = float(usd_prices.get(plan_key, 1.00))
            plans = db.get_premium_plans()
            curr_inr = float(plans.get(plan_key, {}).get("price", 49.0))
            await query.message.reply_text(
                f"✍️ <b>Set Price for {plan_key.capitalize()} Plan:</b>\n\n"
                f"Current Price: <b>₹{curr_inr:.0f} / ${curr_usd:.2f} USD</b>\n\n"
                "Reply to this message with:\n"
                "• <b>Both Rupee & Dollar:</b> <code>2500/25</code> (₹2500 INR and $25 USD)\n"
                "• <b>Or only Rupee:</b> <code>2500</code>\n"
                "• <b>Or with dollar sign:</b> <code>2500/25$</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )

        elif data.startswith("set_plan_logins:"):
            plan_key = data.split(":", 1)[1]
            await query.message.reply_text(
                f"✍️ <b>Set Logins for {plan_key.capitalize()} Plan:</b>\n\nReply to this message with number of allowed logins (e.g. <code>10</code>):",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )

        elif data.startswith("set_plan_days:"):
            plan_key = data.split(":", 1)[1]
            await query.message.reply_text(
                f"✍️ <b>Set Validity Days for {plan_key.capitalize()} Plan:</b>\n\nReply to this message with validity in days (e.g. <code>30</code>):",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )

        elif data == "edit_payment_info":
            pay_cfg = db.get_payment_config()
            text = (
                "🏦 <b>Payment Configuration:</b>\n\n"
                f"• <b>UPI ID:</b> <code>{pay_cfg.get('upi_id')}</code>\n"
                f"• <b>Payee Name:</b> <b>{pay_cfg.get('payee_name')}</b>\n"
                f"• <b>Verify API:</b> <code>{pay_cfg.get('verify_api_url')}</code>\n\n"
                "Select what you want to change:"
            )
            keyboard = [
                [btn(text="🏦 Change UPI ID", callback_data="btn_set_upi_id", style="primary")],
                [btn(text="👤 Change Payee Name", callback_data="btn_set_payee_name", style="primary")],
                [btn(text="🔙 Back to Premium Settings", callback_data="admin_premium_menu", style="primary")]
            ]
            await safe_edit_message(query, context, text, InlineKeyboardMarkup(keyboard))

        elif data == "btn_set_upi_id":
            await query.message.reply_text(
                "✍️ <b>Set UPI ID:</b>\n\nReply to this message with your new UPI ID (e.g. <code>yourname@upi</code>):",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )

        elif data == "btn_set_payee_name":
            await query.message.reply_text(
                "✍️ <b>Set Payee Name:</b>\n\nReply to this message with the Payee Name (e.g. <code>Sudhir kumar</code>):",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )

        elif data == "admin_grant_sub_prompt":
            grant_text = (
                "🎁 <b>Grant Subscription / TV Logins</b>\n\n"
                "Aap yahan se directly kisi bhi user ko Subscription Plan ya Logins grant kar sakte hain:\n\n"
                "• <b>Via Command:</b>\n"
                "  <code>/add &lt;logins&gt; &lt;user_id&gt;</code> (e.g. <code>/add 10 5784912530</code>)\n"
                "  <code>/add &lt;plan&gt; &lt;user_id&gt;</code> (e.g. <code>/add monthly 5784912530</code>)\n\n"
                "• <b>Quick Actions:</b>\n"
                "Neeche diye gaye buttons se direct User ID daal kar activate karein:"
            )
            keyboard = [
                [
                    btn(text="📅 Grant Monthly Plan", callback_data="admin_grant_plan_prompt:monthly", style="success"),
                    btn(text="📅 Grant Quarterly Plan", callback_data="admin_grant_plan_prompt:quarterly", style="success")
                ],
                [
                    btn(text="📅 Grant Yearly Plan", callback_data="admin_grant_plan_prompt:yearly", style="success"),
                    btn(text="🎟️ Grant Custom Logins", callback_data="admin_grant_logins_prompt", style="primary")
                ],
                [
                    btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")
                ]
            ]
            await safe_edit_message(query, context, grant_text, InlineKeyboardMarkup(keyboard))

        elif data.startswith("admin_grant_plan_prompt:"):
            plan_key = data.split(":", 1)[1]
            await query.message.reply_text(
                f"✍️ <b>Grant {plan_key.capitalize()} Plan:</b>\n\n"
                f"Reply to this message with the target <b>User ID</b> (e.g. <code>5784912530</code>):",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )

        elif data == "admin_grant_logins_prompt":
            await query.message.reply_text(
                "✍️ <b>Grant Custom Logins:</b>\n\n"
                "Reply to this message with <code>&lt;user_id&gt; &lt;logins&gt;</code>\n"
                "Example: <code>5784912530 15</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )

        elif data.startswith("admin_approve_order:"):
            parts = data.split(":")
            order_id = parts[1]
            utr_val = parts[2] if len(parts) > 2 else "ADMIN_MANUAL"
            order = db.get_order(order_id)
            if not order:
                await query.answer("❌ Order not found!", show_alert=True)
                return
            if order.get("status") == "completed":
                await query.answer("⚠️ Order already approved!", show_alert=True)
                return

            db.record_redeemed_utr(utr_val, order["user_id"], order_id)
            db.update_order_status(order_id, "completed", utr=utr_val)
            prem = db.activate_user_premium(int(order["user_id"]), order["plan_key"], order["days"], order["logins"])

            await query.answer("✅ Order Approved & Premium Activated!", show_alert=True)
            await query.edit_message_text(
                f"✅ <b>Order Approved by Admin {user.first_name}:</b>\n\n"
                f"• User ID: <code>{order['user_id']}</code>\n"
                f"• Order ID: <code>{order_id}</code>\n"
                f"• Plan: <b>{order['plan_key'].capitalize()}</b> (+{order['logins']} logins, +{order['days']} days)\n"
                f"• UTR: <code>{utr_val}</code>",
                parse_mode=ParseMode.HTML
            )
            try:
                user_msg = (
                    "🎉 <b>Payment Approved by Admin!</b>\n\n"
                    "💎 <b>Your Premium Subscription is now ACTIVE!</b> ⚡\n\n"
                    f"• Plan: <b>{order['plan_key'].capitalize()}</b>\n"
                    f"• Validity Added: <b>+{order['days']} Days</b> (Until: <code>{prem['expires_at']}</code>)\n"
                    f"• Logins Added: <b>+{order['logins']} Logins / Service</b> 🎟️ (Total: <b>{prem['remaining_logins']}</b> across all services)\n\n"
                    "Enjoy full access to all TV platforms!"
                )
                keyboard = [[btn(text="📺 Activate TV", callback_data="menu_activate", style="success")]]
                await context.bot.send_message(
                    chat_id=int(order["user_id"]),
                    text=user_msg,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception:
                pass

        elif data.startswith("admin_reject_order:"):
            order_id = data.split(":", 1)[1]
            order = db.get_order(order_id)
            if order:
                db.update_order_status(order_id, "rejected")
                try:
                    await context.bot.send_message(
                        chat_id=int(order["user_id"]),
                        text=f"❌ <b>Payment Rejected for Order <code>{order_id}</code>:</b>\n\nAdmin could not verify your payment reference. Please ensure you sent the correct UTR or contact support: @Sudhirxd",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
            await query.answer("❌ Order Rejected.", show_alert=True)
            await query.edit_message_text(f"❌ <b>Order <code>{order_id}</code> Rejected by Admin.</b>", parse_mode=ParseMode.HTML)

        elif data == "admin_stats":
            cookie_stats_text = ""
            total_cookies = 0
            for s in SUPPORTED_SERVICES:
                c_count = count_vault_cookies(s)
                total_cookies += c_count
                cookie_stats_text += f"• {s.upper()}: <b>{c_count}</b>\n"

            db_stats = db.get_stats_summary()
            service_success_text = ""
            with stats_lock:
                if "nftoken" not in stats or not isinstance(stats["nftoken"], dict):
                    stats["nftoken"] = {
                        "successful": stats.get("nftoken_generated", 0),
                        "failed": stats.get("nftoken_failed", 0)
                    }
                else:
                    gen_cnt = stats.get("nftoken_generated", 0)
                    fail_cnt = stats.get("nftoken_failed", 0)
                    if gen_cnt > stats["nftoken"].get("successful", 0):
                        stats["nftoken"]["successful"] = gen_cnt
                    if fail_cnt > stats["nftoken"].get("failed", 0):
                        stats["nftoken"]["failed"] = fail_cnt

                for s in SUPPORTED_SERVICES:
                    s_stats = stats.get(s, {"successful": 0, "failed": 0})
                    service_success_text += f"• {s.upper()}: Success: {s_stats.get('successful', 0)} | Failed: {s_stats.get('failed', 0)}\n"

                msg = (
                    f"📊 <b>Bot Statistics Summary</b>\n\n"
                    f"🍪 <b>Cookies in vault: (Total: {total_cookies})</b>\n"
                    f"{cookie_stats_text}\n"
                    f"🎬 <b>Global metrics:</b>\n"
                    f"• Total attempts: {stats['total_logins']}\n"
                    f"• Successful: {stats['successful']}\n"
                    f"• Failed (dead cookies): {stats['failed']}\n"
                    f"• Codes rejected: {stats['codes_rejected']}\n"
                    f"• Started: {stats['started_at']}\n\n"
                    f"📈 <b>Stats per service:</b>\n"
                    f"{service_success_text}\n"
                    f"👥 <b>Database Stats:</b>\n"
                    f"• Total Registered Users: <b>{db_stats['total_users']}</b>\n"
                )
            keyboard = [
                [btn(text="🗑️ Clear All Stats", callback_data="admin_clear_stats", style="danger")],
                [btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")]
            ]
            await query.edit_message_text(text=msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_clear_stats":
            clear_stats()
            await query.answer("✅ All stats cleared!", show_alert=True)
            # Refresh the stats panel to show zeroed out stats
            cookie_stats_text = ""
            total_cookies = 0
            for s in SUPPORTED_SERVICES:
                c_count = count_vault_cookies(s)
                total_cookies += c_count
                cookie_stats_text += f"• {s.upper()}: <b>{c_count}</b>\n"
            db_stats = db.get_stats_summary()
            with stats_lock:
                service_success_text = ""
                for s in SUPPORTED_SERVICES:
                    s_stats = stats.get(s, {"successful": 0, "failed": 0})
                    service_success_text += f"• {s.upper()}: Success: {s_stats.get('successful', 0)} | Failed: {s_stats.get('failed', 0)}\n"
                msg = (
                    f"📊 <b>Bot Statistics Summary</b>\n\n"
                    f"🍪 <b>Cookies in vault: (Total: {total_cookies})</b>\n"
                    f"{cookie_stats_text}\n"
                    f"🎬 <b>Global metrics:</b>\n"
                    f"• Total attempts: {stats['total_logins']}\n"
                    f"• Successful: {stats['successful']}\n"
                    f"• Failed (dead cookies): {stats['failed']}\n"
                    f"• Codes rejected: {stats['codes_rejected']}\n"
                    f"• Started: {stats['started_at']}\n\n"
                    f"📈 <b>Stats per service:</b>\n"
                    f"{service_success_text}\n"
                    f"👥 <b>Database Stats:</b>\n"
                    f"• Total Registered Users: <b>{db_stats['total_users']}</b>\n\n"
                    f"✅ <i>Stats were cleared just now.</i>"
                )
            keyboard = [
                [btn(text="🗑️ Clear All Stats", callback_data="admin_clear_stats", style="danger")],
                [btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")]
            ]
            await query.edit_message_text(text=msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_upload_prompt":
            upload_prompt = (
                "📥 <b>Upload Cookies Vault</b>\n\n"
                "To upload cookies:\n"
                "1️⃣ Create a ZIP file containing `.txt` or `.json` cookie files.\n"
                "2️⃣ Reply to the ZIP file with the <code>/upload &lt;service&gt;</code> command to specify target folder.\n\n"
                "Supported services: <code>netflix</code>, <code>crunchyroll</code>, <code>hbomax</code>, <code>surfshark</code>, <code>spotify</code>, <code>jiohotstar</code>"
            )
            keyboard = [[btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")]]
            await query.edit_message_text(text=upload_prompt, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data == "admin_delete_vault_menu":
            del_text = (
                "🗑️ <b>Delete Cookie Vault</b>\n\n"
                "Select a service to clear its cookie vault, or choose to delete all vaults:"
            )
            await query.edit_message_text(text=del_text, parse_mode=ParseMode.HTML, reply_markup=make_delete_vault_keyboard())

        elif data.startswith("admin_delvault_prompt:"):
            target_svc = data.split(":")[1]
            svc_name = "ALL SERVICES" if target_svc == "all" else target_svc.upper()
            c_count = sum(count_vault_cookies(s) for s in SUPPORTED_SERVICES) if target_svc == "all" else count_vault_cookies(target_svc)
            
            confirm_text = (
                f"⚠️ <b>ARE YOU SURE?</b>\n\n"
                f"You are about to delete <b>{c_count}</b> cookies from <b>{svc_name}</b> vault!\n"
                f"This action cannot be undone."
            )
            confirm_keyboard = InlineKeyboardMarkup([
                [
                    btn(text="✅ Yes, Delete", callback_data=f"admin_delvault_confirm:{target_svc}", style="danger"),
                    btn(text="❌ Cancel", callback_data="admin_delete_vault_menu", style="primary")
                ]
            ])
            await query.edit_message_text(text=confirm_text, parse_mode=ParseMode.HTML, reply_markup=confirm_keyboard)

        elif data.startswith("admin_delvault_confirm:"):
            target_svc = data.split(":")[1]
            deleted_count = clear_vault_cookies(target_svc)
            svc_name = "ALL SERVICES" if target_svc == "all" else target_svc.upper()
            
            try:
                await query.answer(text=f"Deleted {deleted_count} cookies from {svc_name}")
            except Exception:
                pass
                
            res_text = f"✅ <b>Successfully deleted {deleted_count} cookies from {svc_name} vault!</b>"
            keyboard = [[btn(text="🔙 Back to Delete Vault", callback_data="admin_delete_vault_menu", style="primary")]]
            await query.edit_message_text(text=res_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "admin_settings":
            await send_admin_settings(update, context, edit=True)

        elif data == "admin_backup_db_now":
            await query.answer("⏳ Backing up database to GitHub...", show_alert=False)
            success_db, msg_db = await sync_github_db_backup()
            users_count = len(db.data.get("users", {}))
            if success_db:
                await query.message.reply_text(
                    f"✅ <b>GitHub DB Backup Success!</b>\n\n"
                    f"📦 <b>Repo:</b> <code>{GITHUB_BACKUP_REPO}</code>\n"
                    f"👥 <b>Total Backed Up Users:</b> <b>{users_count} users</b>\n"
                    f"⚡ <b>Status:</b> <b>{msg_db}</b>\n"
                    f"🕒 <b>Time:</b> <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await query.message.reply_text(
                    f"❌ <b>GitHub Backup Failed:</b>\n\n<code>{msg_db}</code>",
                    parse_mode=ParseMode.HTML
                )

        elif data == "admin_restore_db_now":
            await query.answer("⏳ Restoring database from GitHub...", show_alert=False)
            ok_db, github_data = db.restore_from_github()
            if ok_db and isinstance(github_data, dict) and "users" in github_data:
                db.data = github_data
                db.save_unlocked()
                users_count = len(github_data.get("users", {}))
                await query.message.reply_text(
                    f"✅ <b>Database Restored from GitHub!</b>\n\n"
                    f"📦 <b>Repo:</b> <code>{GITHUB_BACKUP_REPO}</code>\n"
                    f"👥 <b>Total Restored Users:</b> <b>{users_count} users</b>\n"
                    f"🕒 <b>Time:</b> <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await query.message.reply_text(
                    "❌ <b>GitHub Restore Failed:</b>\n\nCould not fetch valid db.json from GitHub backup repository.",
                    parse_mode=ParseMode.HTML
                )

            
        elif data == "btn_set_token_limit":
            await query.message.reply_text(
                "✍️ <b>Set Daily NFToken Limit per User:</b>\n\nReply to this message with the maximum number of login links a regular user can generate per day.\n<i>(Send <code>0</code> for unlimited)</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )

        elif data == "btn_set_group_link":
            await query.message.reply_text(
                "✍️ <b>Set Group Link:</b>\n\nReply to this message with your group invite link (e.g. <code>https://t.me/MyGroup</code>).",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )
            
        elif data == "toggle_mode":
            current = db.get_setting("usage_mode", "both")
            modes = ["both", "private_only", "group_only"]
            next_mode = modes[(modes.index(current) + 1) % len(modes)]
            db.set_setting("usage_mode", next_mode)
            try:
                await query.answer(text=f"Usage mode set to {next_mode.upper()}")
            except Exception:
                pass
            await send_admin_settings(update, context, edit=True)

        elif data == "toggle_cookie_removal_mode":
            current = db.get_setting("cookie_removal_mode", "dead_only")
            next_mode = "delete_on_success" if current == "dead_only" else "dead_only"
            db.set_setting("cookie_removal_mode", next_mode)
            mode_desc = "Delete on Success" if next_mode == "delete_on_success" else "Dead Only"
            try:
                await query.answer(text=f"Cookie removal policy set to: {mode_desc}")
            except Exception:
                pass
            await send_admin_settings(update, context, edit=True)
            
        elif data == "toggle_kito_mode":
            current = db.get_setting("kito_mode", False)
            db.set_setting("kito_mode", not current)
            try:
                await query.answer(text=f"Kito Mode set to {'ON' if not current else 'OFF'}")
            except Exception:
                pass
            await send_admin_settings(update, context, edit=True)
            
        elif data == "btn_add_channel":
            await query.message.reply_text(
                "✍️ <b>Add Force Join Channel:</b>\n\nReply to this message with the channel username (e.g. <code>@MyChannel</code>) or ID (e.g. <code>-100123456789</code>).",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )
            
        elif data == "btn_remove_channel":
            channels = db.get_setting("force_join_channels", [])
            if not channels:
                await query.answer("❌ No force join channels configured!", show_alert=True)
                return
            
            keyboard = []
            for chan in channels:
                keyboard.append([InlineKeyboardButton(text=f"❌ {chan}", callback_data=f"remove_chan:{chan}")])
            keyboard.append([btn(text="🔙 Back to Settings", callback_data="admin_settings", style="primary")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text="<b>Select a channel below to remove it from Force Join:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
            
        elif data.startswith("remove_chan:"):
            chan_to_remove = data.split(":", 1)[1]
            db.remove_force_join_channel(chan_to_remove)
            await query.answer(f"✅ Removed: {chan_to_remove}")
            
            # Refresh the list
            channels = db.get_setting("force_join_channels", [])
            if not channels:
                await send_admin_settings(update, context, edit=True)
                return
                
            keyboard = []
            for chan in channels:
                keyboard.append([InlineKeyboardButton(text=f"❌ {chan}", callback_data=f"remove_chan:{chan}")])
            keyboard.append([btn(text="🔙 Back to Settings", callback_data="admin_settings", style="primary")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text="<b>Select a channel below to remove it from Force Join:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
            
        elif data == "btn_add_admin":
            await query.message.reply_text(
                "✍️ <b>Add Admin ID:</b>\n\nReply to this message with the Telegram User ID (numeric, e.g. <code>123456789</code>) of the new admin.",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True)
            )
            
        elif data == "btn_remove_admin":
            admins = db.get_setting("admin_ids", [])
            if not admins:
                await query.answer("❌ No extra admins configured!", show_alert=True)
                return
                
            keyboard = []
            for adm in admins:
                keyboard.append([InlineKeyboardButton(text=f"❌ {adm}", callback_data=f"remove_admin:{adm}")])
            keyboard.append([btn(text="🔙 Back to Settings", callback_data="admin_settings", style="primary")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await safe_edit_message(
                query,
                context,
                "<b>Select an Admin ID below to remove their administrator rights:</b>",
                reply_markup
            )
            
        elif data.startswith("remove_admin:"):
            adm_to_remove = int(data.split(":", 1)[1])
            admins = db.get_setting("admin_ids", [])
            if adm_to_remove in admins:
                admins.remove(adm_to_remove)
                db.set_setting("admin_ids", admins)
            await query.answer(f"✅ Removed Admin ID: {adm_to_remove}")
            await send_admin_settings(update, context, edit=True)
            
        elif data == "admin_broadcast_prompt":
            context.user_data["awaiting_broadcast"] = True
            keyboard = [[btn(text="❌ Cancel", callback_data="admin_broadcast_cancel", style="danger")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                text="✍️ <b>Enter Broadcast Message:</b>\n\nSend or forward the message (text, photo, video, document, etc.) you want to broadcast.",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
            
        elif data == "admin_broadcast_cancel":
            context.user_data.pop("awaiting_broadcast", None)
            context.user_data.pop("broadcast_msg_id", None)
            context.user_data.pop("broadcast_chat_id", None)
            await query.answer("❌ Broadcast cancelled.")
            admin_text = (
                "👑 <b>Admin Control Panel</b>\n\n"
                "Select an administrative option from the choices below:"
            )
            await query.edit_message_text(text=admin_text, parse_mode=ParseMode.HTML, reply_markup=make_admin_keyboard())
            
        elif data == "admin_broadcast_confirm":
            msg_id = context.user_data.pop("broadcast_msg_id", None)
            chat_id = context.user_data.pop("broadcast_chat_id", None)
            context.user_data.pop("awaiting_broadcast", None)
            
            if not msg_id or not chat_id:
                await query.answer("❌ Error: No broadcast message drafted.", show_alert=True)
                return
                
            await query.edit_message_text(
                text="🚀 <b>Starting broadcast...</b>\n\nInitialising delivery...",
                parse_mode=ParseMode.HTML
            )
            
            # Start background broadcast task
            asyncio.create_task(run_broadcast(context.bot, query.message.chat.id, query.message.message_id, chat_id, msg_id))
            
        elif data == "admin_proxies":
            raw_proxies = get_raw_proxies()
            total = len(raw_proxies)
            target_region = db.get_setting("allowed_proxy_region", "ANY")
            
            in_c = sum(1 for p in proxies_list if get_proxy_country(p) == "IN")
            other_c = len(proxies_list) - in_c
            
            # Prepare proxy list text
            if raw_proxies:
                display_list = []
                for idx, line in enumerate(raw_proxies[:15], 1):
                    p_parsed = parse_proxy_line(line)
                    c_tag = get_proxy_country(p_parsed) if p_parsed else "UNKNOWN"
                    flag = "🇮🇳 " if c_tag == "IN" else ("🌐 " if c_tag != "UNKNOWN" else "❓ ")
                    display_list.append(f"{idx}. {flag}<code>{line}</code>")
                if total > 15:
                    display_list.append(f"...and {total - 15} more.")
                proxy_list_str = "\n".join(display_list)
            else:
                proxy_list_str = "<i>No proxies configured.</i>"
                
            region_flag = "🇮🇳 India Only (IN)" if target_region == "IN" else ("🇺🇸 US Only (US)" if target_region == "US" else "🌏 Any Region (Smart Routing)")
            msg = (
                f"🔌 <b>Manage Bot Proxies</b>\n\n"
                f"🌐 <b>Proxy Filter:</b> <b>{region_flag}</b>\n"
                f"📊 <b>Total Loaded Proxies:</b> {total}\n"
                f"   ├ 🇮🇳 <b>JioHotstar Reserved (IN):</b> <b>{in_c}</b>\n"
                f"   └ 🌐 <b>Crunchyroll Dedicated (Other):</b> <b>{other_c}</b>\n\n"
                f"📝 <b>Proxy List (Top 15):</b>\n{proxy_list_str}\n\n"
                f"💡 <i>India proxies are saved for JioHotstar. Other proxies are dedicated for Crunchyroll. Other services use direct IP.</i>"
            )
            
            keyboard = [
                [
                    InlineKeyboardButton(text=f"🌐 Region Mode: {region_flag}", callback_data="toggle_proxy_region")
                ],
                [
                    btn(text="➕ Add Proxy", callback_data="admin_proxies_add", style="success"),
                    btn(text="❌ Delete Proxy", callback_data="admin_proxies_delete", style="danger")
                ],
                [
                    btn(text="🔍 Check & Clean Dead Proxies", callback_data="admin_proxies_check", style="primary"),
                    btn(text="🗑️ Clear All", callback_data="admin_proxies_clear", style="danger")
                ],
                [
                    btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")
                ]
            ]
            await query.edit_message_text(text=msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "toggle_proxy_region":
            current_region = db.get_setting("allowed_proxy_region", "ANY")
            region_cycle = ["ANY", "IN", "US"]
            next_region = region_cycle[(region_cycle.index(current_region) + 1) % len(region_cycle)] if current_region in region_cycle else "ANY"
            db.set_setting("allowed_proxy_region", next_region)
            await query.answer(f"🌐 Proxy Mode set to {next_region}")
            
            # Refresh admin proxies menu
            raw_proxies = get_raw_proxies()
            total = len(raw_proxies)
            target_region = next_region
            in_c = sum(1 for p in proxies_list if get_proxy_country(p) == "IN")
            other_c = len(proxies_list) - in_c

            if raw_proxies:
                display_list = []
                for idx, line in enumerate(raw_proxies[:15], 1):
                    p_parsed = parse_proxy_line(line)
                    c_tag = get_proxy_country(p_parsed) if p_parsed else "UNKNOWN"
                    flag = "🇮🇳 " if c_tag == "IN" else ("🌐 " if c_tag != "UNKNOWN" else "❓ ")
                    display_list.append(f"{idx}. {flag}<code>{line}</code>")
                if total > 15:
                    display_list.append(f"...and {total - 15} more.")
                proxy_list_str = "\n".join(display_list)
            else:
                proxy_list_str = "<i>No proxies configured.</i>"
                
            region_flag = "🇮🇳 India Only (IN)" if target_region == "IN" else ("🇺🇸 US Only (US)" if target_region == "US" else "🌏 Any Region (Smart Routing)")
            msg = (
                f"🔌 <b>Manage Bot Proxies</b>\n\n"
                f"🌐 <b>Proxy Filter:</b> <b>{region_flag}</b>\n"
                f"📊 <b>Total Loaded Proxies:</b> {total}\n"
                f"   ├ 🇮🇳 <b>JioHotstar Reserved (IN):</b> <b>{in_c}</b>\n"
                f"   └ 🌐 <b>Crunchyroll Dedicated (Other):</b> <b>{other_c}</b>\n\n"
                f"📝 <b>Proxy List (Top 15):</b>\n{proxy_list_str}\n\n"
                f"💡 <i>India proxies are saved for JioHotstar. Other proxies are dedicated for Crunchyroll. Other services use direct IP.</i>"
            )
            keyboard = [
                [
                    InlineKeyboardButton(text=f"🌐 Region Mode: {region_flag}", callback_data="toggle_proxy_region")
                ],
                [
                    btn(text="➕ Add Proxy", callback_data="admin_proxies_add", style="success"),
                    btn(text="❌ Delete Proxy", callback_data="admin_proxies_delete", style="danger")
                ],
                [
                    btn(text="🔍 Check & Clean Dead Proxies", callback_data="admin_proxies_check", style="primary"),
                    btn(text="🗑️ Clear All", callback_data="admin_proxies_clear", style="danger")
                ],
                [
                    btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")
                ]
            ]
            await query.edit_message_text(text=msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

        elif data == "admin_proxies_add":
            context.user_data["awaiting_proxies"] = True
            keyboard = [[btn(text="❌ Cancel", callback_data="admin_proxies", style="danger")]]
            await query.edit_message_text(
                text="✍️ <b>Add Proxies:</b>\n\nReply to this message with a list of proxies (one per line).\n\n<b>Supported formats:</b>\n<code>ip:port</code>\n<code>ip:port:user:pass</code>\n<code>scheme://user:pass@ip:port</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        elif data == "admin_proxies_delete":
            raw_proxies = get_raw_proxies()
            if not raw_proxies:
                await query.answer("❌ No proxies found to delete!", show_alert=True)
                return
            context.user_data["awaiting_proxy_delete"] = True
            keyboard = [[btn(text="❌ Cancel", callback_data="admin_proxies", style="danger")]]
            await query.edit_message_text(
                text="✍️ <b>Delete Proxy:</b>\n\nReply to this message with the **Line Number** (e.g. <code>1</code>) or the **exact proxy string** you want to delete.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        elif data == "admin_proxies_clear":
            save_proxies_to_file([])
            proxies_list = []
            await query.answer("🗑️ All proxies cleared!")
            # Refresh menu
            target_region = db.get_setting("allowed_proxy_region", "IN")
            region_flag = "🇮🇳 India (IN)" if target_region == "IN" else ("🇺🇸 United States (US)" if target_region == "US" else "🌏 Any Region")
            keyboard = [
                [
                    InlineKeyboardButton(text=f"🌐 Filter Region: {region_flag}", callback_data="toggle_proxy_region")
                ],
                [
                    btn(text="➕ Add Proxy", callback_data="admin_proxies_add", style="success"),
                    btn(text="❌ Delete Proxy", callback_data="admin_proxies_delete", style="danger")
                ],
                [
                    btn(text="🔍 Check & Remove Other Regions", callback_data="admin_proxies_check", style="primary"),
                    btn(text="🗑️ Clear All", callback_data="admin_proxies_clear", style="danger")
                ],
                [
                    btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")
                ]
            ]
            await query.edit_message_text(
                text=f"🔌 <b>Manage Bot Proxies</b>\n\n🌐 <b>Allowed Proxy Region:</b> <b>{region_flag}</b>\n<b>Total Loaded Proxies:</b> 0\n\n📝 <b>Proxy List (Top 15):</b>\n<i>No proxies configured.</i>\n\n💡 <i>Bots will rotate working proxies from this list.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        elif data == "admin_proxies_check":
            # Launch background checking task
            asyncio.create_task(check_all_proxies_task(context.bot, query.message.chat.id, query.message.message_id))
            await query.answer("🔍 Checking proxies and removing non-matching regions...")
            
    if data == "verify_force_join":
        channels = db.get_setting("force_join_channels", [])
        not_joined = []
        for chan in channels:
            joined = await check_force_join_single(context.bot, user.id, chan)
            if not joined:
                not_joined.append(chan)

        if not_joined:
            await query.answer(text="❌ You have not joined all channels yet! Please join first.", show_alert=True)
        else:
            db.set_user_verified(user.id, True)
            keyboard = [[btn(text="🚀 Open Main Menu", callback_data="menu_main", style="success")]]
            await query.edit_message_text(
                text="✅ <b>Verification successful!</b>\n\nYou have joined all required channels. You can now use the bot! Click below to open menu.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )


async def check_all_proxies_task(bot, chat_id, message_id):
    raw_proxies = get_raw_proxies()
    target_region = db.get_setting("allowed_proxy_region", "ANY")
    
    if not raw_proxies:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="<b><i>❌ No proxies found to check!</i></b>",
                parse_mode='HTML'
            )
        except:
            pass
        return
        
    try:
        mode_label = "Smart Routing (All Regions)" if target_region == "ANY" else f"Filter: {target_region}"
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"<b><i>🔌 Starting check & health filter ({mode_label}) for {len(raw_proxies)} proxies...</i></b>",
            parse_mode='HTML'
        )
    except:
        pass
        
    working_raw = []
    dead_count = 0
    wrong_region_count = 0
    working_count = 0
    in_count = 0
    other_count = 0
    
    for i, line in enumerate(raw_proxies, 1):
        parsed = parse_proxy_line(line)
        if parsed:
            is_working, cc, cname = await asyncio.to_thread(check_proxy_with_region, parsed)
            if is_working:
                url_key = parsed.get("http", "")
                if url_key and cc and cc != "UNKNOWN":
                    PROXY_COUNTRY_CACHE[url_key] = cc
                # Save all working proxies (IN for JioHotstar, others for Crunchyroll)
                working_raw.append(line)
                working_count += 1
                if cc == "IN":
                    in_count += 1
                else:
                    other_count += 1
            else:
                dead_count += 1
        else:
            dead_count += 1
            
        if i % 3 == 0 or i == len(raw_proxies):
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"<b><i>🔌 Checking proxies... ({i}/{len(raw_proxies)})\n\n"
                        f"✅ Alive Proxies: <b>{working_count}</b>\n"
                        f"   ├ 🇮🇳 JioHotstar (IN): <b>{in_count}</b>\n"
                        f"   └ 🌐 Crunchyroll (Other): <b>{other_count}</b>\n"
                        f"❌ Dead Proxies: <b>{dead_count}</b></i></b>"
                    ),
                    parse_mode='HTML'
                )
            except:
                pass
            await asyncio.sleep(0.1)
            
    save_proxies_to_file(working_raw)
    global proxies_list
    if 'db' in globals() and db is not None:
        try:
            db.set_setting("allowed_proxy_region", "ANY")
            db.set_setting("proxy_country_cache", PROXY_COUNTRY_CACHE)
        except Exception:
            pass
    proxies_list = load_proxies()
    
    keyboard = [[btn(text="🔙 Back to Proxies", callback_data="admin_proxies", style="primary")]]
    
    report_text = (
        f"<b><i>🏁 Proxy Health Check Completed!\n\n"
        f"🎯 Routing Mode: <b>Smart Service Allocation</b>\n"
        f"✅ Total Working Proxies: <b>{working_count}</b>\n"
        f"   ├ 🇮🇳 JioHotstar Reserved (IN): <b>{in_count}</b>\n"
        f"   └ 🌐 Crunchyroll Dedicated (Other): <b>{other_count}</b>\n"
        f"❌ Auto-Removed (Dead): <b>{dead_count}</b>\n\n"
        f"💡 <i>Indian proxies are saved for JioHotstar. Other proxies are dedicated for Crunchyroll. Netflix & others stay on direct IP.</i></i></b>"
    )
        
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=report_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except:
        pass


import telegram.error

async def run_broadcast(bot, admin_chat_id, status_message_id, from_chat_id, message_id):
    users = list(db.data.get("users", {}).keys())
    total_users = len(users)
    
    if total_users == 0:
        try:
            await bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=status_message_id,
                text="❌ <b>Broadcast aborted:</b> No registered users in the database.",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        return

    success_count = 0
    fail_count = 0
    
    try:
        await bot.edit_message_text(
            chat_id=admin_chat_id,
            message_id=status_message_id,
            text=f"🚀 <b>Broadcast in progress...</b>\n\nSending to {total_users} users...",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

    for i, user_id_str in enumerate(users, 1):
        try:
            user_id = int(user_id_str)
            # Copy the message (preserves text formatting, images, media, captions, without forward headers)
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=from_chat_id,
                message_id=message_id
            )
            success_count += 1
        except telegram.error.Forbidden:
            # User blocked the bot
            fail_count += 1
        except Exception:
            fail_count += 1
            
        # Comply with rate limits (30 messages per second, we sleep 0.05s between each message)
        await asyncio.sleep(0.05)
        
        # Edit progress message every 10 users to keep the admin updated without hitting Telegram rate limits for edits
        if i % 10 == 0 or i == total_users:
            try:
                await bot.edit_message_text(
                    chat_id=admin_chat_id,
                    message_id=status_message_id,
                    text=f"🚀 <b>Broadcast in progress...</b>\n\nProgress: <b>{i} / {total_users}</b> users\n✅ Sent: {success_count}\n❌ Failed/Blocked: {fail_count}",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    summary = (
        f"📢 <b>Broadcast Completed!</b>\n\n"
        f"👥 Total Target: <b>{total_users}</b> users\n"
        f"✅ Successfully Sent: <b>{success_count}</b>\n"
        f"❌ Failed/Blocked: <b>{fail_count}</b>"
    )
    
    try:
        keyboard = [[btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")]]
        await bot.edit_message_text(
            chat_id=admin_chat_id,
            message_id=status_message_id,
            text=summary,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception:
        # If edit failed, try to send a new message
        try:
            await bot.send_message(chat_id=admin_chat_id, text=summary, parse_mode=ParseMode.HTML)
        except Exception:
            pass

async def handle_admin_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
        
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
        
    global proxies_list
        
    if context.user_data and context.user_data.get("awaiting_proxies"):
        context.user_data.pop("awaiting_proxies", None)
        raw_text = msg.text.strip() if msg.text else ""
        if not raw_text:
            await msg.reply_text("<b><i>❌ Text message is required to add proxies!</i></b>", parse_mode='HTML')
            raise ApplicationHandlerStop()
            
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        
        valid_proxies = []
        invalid_count = 0
        
        for line in lines:
            if parse_proxy_line(line):
                valid_proxies.append(line)
            else:
                invalid_count += 1
                
        if valid_proxies:
            current_raw = get_raw_proxies()
            new_raw = current_raw + valid_proxies
            save_proxies_to_file(new_raw)
            proxies_list = load_proxies()
            
            response_text = f"<b><i>✅ Successfully added {len(valid_proxies)} proxies!\n❌ Invalid format skipped: {invalid_count}\n\n🔌 Total proxies now: {len(new_raw)}</i></b>"
        else:
            response_text = "<b><i>❌ No valid proxies found in the text. Check formats!</i></b>"
            
        keyboard = [[btn(text="🔙 Back to Proxies", callback_data="admin_proxies", style="primary")]]
        await msg.reply_text(response_text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
        raise ApplicationHandlerStop()
        
    elif context.user_data and context.user_data.get("awaiting_proxy_delete"):
        context.user_data.pop("awaiting_proxy_delete", None)
        target = msg.text.strip() if msg.text else ""
        if not target:
            await msg.reply_text("<b><i>❌ Text input is required to delete proxy!</i></b>", parse_mode='HTML')
            raise ApplicationHandlerStop()
            
        raw_proxies = get_raw_proxies()
        deleted = False
        new_raw = []
        
        # Check if they entered a line number
        if target.isdigit():
            idx = int(target) - 1
            if 0 <= idx < len(raw_proxies):
                removed = raw_proxies.pop(idx)
                new_raw = raw_proxies
                deleted = True
                response_text = f"<b><i>✅ Successfully deleted proxy at line {target}: {removed}</i></b>"
            else:
                response_text = f"<b><i>❌ Invalid line number {target}! Please select a number between 1 and {len(raw_proxies)}</i></b>"
        else:
            # Check if they entered a matching string
            for line in raw_proxies:
                if target in line:
                    deleted = True
                    response_text = f"<b><i>✅ Successfully deleted proxy matching: {line}</i></b>"
                else:
                    new_raw.append(line)
            if not deleted:
                response_text = f"<b><i>❌ No matching proxy found for: {target}</i></b>"
                
        if deleted:
            save_proxies_to_file(new_raw)
            proxies_list = load_proxies()
            
        keyboard = [[btn(text="🔙 Back to Proxies", callback_data="admin_proxies", style="primary")]]
        await msg.reply_text(response_text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
        raise ApplicationHandlerStop()
        
    elif context.user_data and context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        context.user_data["broadcast_msg_id"] = msg.message_id
        context.user_data["broadcast_chat_id"] = msg.chat.id
        
        keyboard = [
            [
                btn(text="✅ Confirm & Start", callback_data="admin_broadcast_confirm", style="success"),
                btn(text="❌ Cancel", callback_data="admin_broadcast_cancel", style="danger")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.reply_text(
            "❓ <b>Are you sure you want to broadcast this message to all registered users?</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            reply_to_message_id=msg.message_id
        )
        raise ApplicationHandlerStop()


async def handle_replies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    msg = update.message
    if not msg or not msg.reply_to_message:
        return
        
    reply_text = msg.reply_to_message.text or msg.reply_to_message.caption or ""
    if not reply_text:
        return
        
    if ("Enter your" in reply_text and ("TV code" in reply_text or "Device code" in reply_text)) or ("QR code image" in reply_text):
        raw_code = msg.text.strip()
        
        # Determine service based on the prompt message
        service = None
        if "NETFLIX" in reply_text.upper():
            service = "netflix"
        elif "CRUNCHYROLL" in reply_text.upper():
            service = "crunchyroll"
        elif "HBO MAX" in reply_text.upper():
            service = "hbomax"
        elif "SURFSHARK" in reply_text.upper():
            service = "surfshark"
        elif "SPOTIFY" in reply_text.upper():
            service = "spotify"
        elif "JIOHOTSTAR" in reply_text.upper() or "HOTSTAR" in reply_text.upper():
            service = "jiohotstar"
        elif "PRIME" in reply_text.upper():
            service = "primevideo"
            
        if service:
            await start_tv_activation(update, context, service, raw_code)
        else:
            # Fallback if somehow service wasn't detected
            db.register_user(user_id, update.effective_user.username, update.effective_user.first_name)
            keyboard = [
                [
                    InlineKeyboardButton(text="🔴 Netflix", callback_data=f"activate:netflix:{raw_code}"),
                    InlineKeyboardButton(text="🟠 Crunchyroll", callback_data=f"activate:crunchyroll:{raw_code}")
                ],
                [
                    InlineKeyboardButton(text="🟣 HBO Max", callback_data=f"activate:hbomax:{raw_code}"),
                    InlineKeyboardButton(text="🔵 Surfshark", callback_data=f"activate:surfshark:{raw_code}")
                ],
                [
                    InlineKeyboardButton(text="🟢 Spotify", callback_data=f"activate:spotify:{raw_code}"),
                    InlineKeyboardButton(text="🔵 JioHotstar", callback_data=f"activate:jiohotstar:{raw_code}")
                ],
                [
                    InlineKeyboardButton(text="🎬 Prime Video", callback_data=f"activate:primevideo:{raw_code}")
                ],
                [
                    InlineKeyboardButton(text="🔙 Main Menu", callback_data="menu_main")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await msg.reply_text(
                f"📺 <b>Select Service for Code:</b> <code>{raw_code.upper()}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )

    elif "Add Force Join Channel:" in reply_text:
        if user_id not in ADMIN_IDS:
            return
        raw_text = msg.text.strip()
        # Split by comma, newline, or space
        tokens = re.split(r'[\n\s,]+', raw_text)
        tokens = [t.strip() for t in tokens if t.strip()]
        
        if not tokens:
            await msg.reply_text("❌ No channels provided. Please reply with valid channel usernames or IDs.", parse_mode=ParseMode.HTML)
            return
            
        success_list = []
        fail_list = []
        
        for token in tokens:
            chan = token
            if chan.lstrip('-').isdigit():
                chan = int(chan)
                
            try:
                chat = await context.bot.get_chat(chat_id=chan)
                chan_val = chat.id if not chat.username else f"@{chat.username}"
                db.add_force_join_channel(chan_val)
                success_list.append(f"<b>{chat.title}</b> (<code>{chan_val}</code>)")
            except Exception as e:
                db.add_force_join_channel(chan)
                fail_list.append(f"<code>{chan}</code> (unverified, error: <i>{e}</i>)")
                
        response = ""
        if success_list:
            response += "✅ <b>Successfully Added:</b>\n" + "\n".join([f"• {x}" for x in success_list]) + "\n\n"
        if fail_list:
            response += "⚠️ <b>Added (Could not verify):</b>\n" + "\n".join([f"• {x}" for x in fail_list]) + "\n<i>Make sure the bot is an admin in these channels.</i>\n\n"
            
        await msg.reply_text(response, parse_mode=ParseMode.HTML)
            
    elif "Remove Force Join Channel:" in reply_text:
        if not is_admin(user_id):
            return
        chan = msg.text.strip()
        if chan.lstrip('-').isdigit():
            chan = int(chan)
            
        success = db.remove_force_join_channel(chan)
        if success:
            await msg.reply_text(f"✅ Removed <code>{chan}</code> from force join list.", parse_mode=ParseMode.HTML)
        else:
            await msg.reply_text(f"❌ Channel <code>{chan}</code> not found in force join list.", parse_mode=ParseMode.HTML)
            
    elif "Set Daily NFToken Limit per User:" in reply_text:
        if not is_admin(user_id):
            return
        target = msg.text.strip()
        if not target.isdigit():
            await msg.reply_text("❌ Please enter a valid number (e.g. <code>3</code> or <code>0</code> for unlimited).", parse_mode=ParseMode.HTML)
            return
        num = int(target)
        db.set_setting("daily_token_limit", num)
        display_val = "Unlimited ♾️" if num == 0 else f"{num} per day"
        await msg.reply_text(
            f"✅ <b>Daily NFToken Limit updated!</b>\n\n🎯 New Limit: <b>{display_val}</b> per user.",
            parse_mode=ParseMode.HTML
        )

    elif "Set Group Link:" in reply_text:
        if not is_admin(user_id):
            return
        link = msg.text.strip()
        db.set_setting("group_link", link)
        await msg.reply_text(f"✅ Group invite link updated successfully!\n\nLink: {link}", parse_mode=ParseMode.HTML)

    elif "Add Admin ID:" in reply_text:
        if not is_admin(user_id):
            return
            
        target = msg.text.strip()
        if not target.isdigit():
            await msg.reply_text("❌ <b>Invalid ID:</b> The admin ID must be a numeric value (e.g. <code>123456789</code>).", parse_mode=ParseMode.HTML)
            return
            
        new_admin = int(target)
        admins = db.get_setting("admin_ids", [])
        
        if new_admin in admins or new_admin in ADMIN_IDS:
            await msg.reply_text(f"❌ <b>Already Admin:</b> User ID <code>{new_admin}</code> is already an admin.", parse_mode=ParseMode.HTML)
            return
            
        admins.append(new_admin)
        db.set_setting("admin_ids", admins)
        
        keyboard = [[btn(text="🔙 Back to Settings", callback_data="admin_settings", style="primary")]]
        await msg.reply_text(
            f"✅ <b>Admin Added Successfully!</b>\n\nUser ID <code>{new_admin}</code> has been granted administrator rights.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif "Submit Payment Reference for Order" in reply_text:
        m = re.search(r"Order <code>([A-Z0-9\-]+)</code>", reply_text)
        order_id = m.group(1) if m else (context.user_data.get("awaiting_utr_order_id") if context.user_data else None)
        if not order_id:
            await msg.reply_text("❌ Could not identify the Order ID. Please click 'Submit UTR' again.", parse_mode=ParseMode.HTML)
            return

        order = db.get_order(order_id)
        if not order:
            await msg.reply_text("❌ Order not found. Please select a plan from the menu again.", parse_mode=ParseMode.HTML)
            return

        if order.get("status") == "completed":
            await msg.reply_text("✅ This order has already been completed and activated!", parse_mode=ParseMode.HTML)
            return

        utr_candidate = msg.text.strip().replace(" ", "")
        if len(utr_candidate) < 6:
            await msg.reply_text("❌ <b>Invalid UTR/Txn ID:</b> Please enter a valid reference number.", parse_mode=ParseMode.HTML)
            return

        if db.is_utr_redeemed(utr_candidate):
            await msg.reply_text(
                "❌ <b>UTR Already Redeemed!</b>\n\nThis transaction ID has already been used for another subscription order. Double-spending is not permitted.",
                parse_mode=ParseMode.HTML
            )
            return

        status_msg = await msg.reply_text(
            "<b><i>⏳ Verifying payment... Please wait a few seconds.</i></b>",
            parse_mode=ParseMode.HTML
        )

        verified, ver_msg, detail = await asyncio.to_thread(
            verify_fampay_payment, utr_candidate, order["amount"]
        )

        if not verified:
            v_ord, m_ord, d_ord = await asyncio.to_thread(
                verify_fampay_order_payment, order_id, order["amount"]
            )
            if v_ord:
                verified, ver_msg, detail = v_ord, m_ord, d_ord

        if verified:
            task = ACTIVE_PAYMENT_TASKS.pop(order_id, None)
            if task and not task.done():
                task.cancel()

            txn_code = detail.get("txn_id") or utr_candidate
            db.record_redeemed_utr(txn_code, user_id, order_id)
            db.update_order_status(order_id, "completed", utr=txn_code)
            prem = db.activate_user_premium(user_id, order["plan_key"], order["days"], order["logins"])

            try:
                await status_msg.delete()
            except Exception:
                pass

            success_text = (
                "<b><i>🎉 Payment Verified Successfully!\n\n"
                "💎 Your Premium Subscription is now ACTIVE! ⚡\n\n"
                f"• Plan: {order['plan_key'].capitalize()}\n"
                f"• Validity Added: +{order['days']} Days (Until: <code>{prem['expires_at']}</code>)\n"
                f"• Logins Added: +{order['logins']} Logins / Service 🎟️ (Total: {prem['remaining_logins']} across all services)\n"
                f"• UTR / Txn ID: <code>{utr_candidate}</code>\n\n"
                "🚀 You can now activate any TV service or generate Netflix login links instantly!</i></b>"
            )
            keyboard = [
                [btn(text="📺 Activate TV Now", callback_data="menu_activate", style="success")],
                [btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")]
            ]
            await msg.reply_text(success_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

            user_handle = f"@{msg.from_user.username}" if msg.from_user.username else "No username"
            admin_notif = (
                "🔔 <b>[Auto-Payment Verified]</b>\n\n"
                f"👤 <b>User:</b> {msg.from_user.first_name} ({user_handle} | <code>{user_id}</code>)\n"
                f"📦 <b>Plan:</b> {order['plan_key'].capitalize()} (₹{order['amount']:.2f})\n"
                f"🆔 <b>Order:</b> <code>{order_id}</code>\n"
                f"🧾 <b>UTR:</b> <code>{utr_candidate}</code>\n"
                "⚡ <b>Status:</b> Auto-Activated ✅"
            )
            for adm_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(chat_id=adm_id, text=admin_notif, parse_mode=ParseMode.HTML)
                except Exception:
                    pass
        else:
            try:
                await status_msg.delete()
            except Exception:
                pass

            fail_text = (
                "⚠️ <b>Payment Verification Could Not Be Confirmed:</b>\n\n"
                f"<i>{ver_msg}</i>\n\n"
                f"• Order ID: <code>{order_id}</code>\n"
                f"• Submitted Reference: <code>{utr_candidate}</code>\n"
                f"• Expected Amount: <b>₹{order['amount']:.2f}</b>\n\n"
                "💡 <b>If your money was debited:</b>\n"
                "1. Wait 1-2 minutes for bank sync and tap <b>Retry Verification</b>.\n"
                "2. Or click <b>Request Admin Review</b> below to have an admin manually approve it."
            )
            keyboard = [
                [btn(text="🔄 Retry Verification", callback_data=f"submit_utr:{order_id}", style="primary")],
                [btn(text="🙋 Request Admin Review", callback_data=f"req_admin_review:{order_id}:{utr_candidate}", style="success")],
                [btn(text="🔙 Back to Plans", callback_data="menu_premium", style="primary")]
            ]
            await msg.reply_text(fail_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

    elif "Set Price for " in reply_text:
        if not is_admin(user_id):
            return
        m = re.search(r"Set Price for (\w+) Plan", reply_text)
        plan_key = m.group(1).lower() if m else None
        val = msg.text.strip().replace("$", "").replace("₹", "").strip()
        
        inr_val = None
        usd_val = None
        
        # Check if entered as '2500/25' or '2500 / 25' or '2500, 25'
        if "/" in val:
            parts = val.split("/", 1)
            try:
                inr_val = float(parts[0].strip())
                usd_val = float(parts[1].strip())
            except ValueError:
                pass
        elif "," in val:
            parts = val.split(",", 1)
            try:
                inr_val = float(parts[0].strip())
                usd_val = float(parts[1].strip())
            except ValueError:
                pass
        else:
            try:
                inr_val = float(val)
            except ValueError:
                pass

        if inr_val is None:
            await msg.reply_text(
                "❌ <b>Invalid format!</b>\n\nPlease enter numbers like:\n"
                "• <code>2500/25</code> (₹2500 INR and $25 USD)\n"
                "• <code>2500</code> (only INR)",
                parse_mode=ParseMode.HTML
            )
            return

        if plan_key:
            db.update_premium_plan(plan_key, price=inr_val)
            
            # If USD value was also supplied, update PayPal USD price for this plan
            if usd_val is not None:
                pp_cfg = db.get_paypal_config()
                usd_prices = pp_cfg.get("usd_prices", {})
                usd_prices[plan_key] = usd_val
                db.set_paypal_config(usd_prices=usd_prices)
                status_msg = f"✅ <b>Price Updated!</b>\n\n{plan_key.capitalize()} plan price is now <b>₹{inr_val:.0f} / ${usd_val:.2f} USD</b>."
            else:
                status_msg = f"✅ <b>Price Updated!</b>\n\n{plan_key.capitalize()} plan price is now <b>₹{inr_val:.0f}</b>."

            await msg.reply_text(
                status_msg,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(text="🔙 Premium Settings", callback_data="admin_premium_menu", style="primary")]])
            )

    elif "Set Logins for " in reply_text:
        if not is_admin(user_id):
            return
        m = re.search(r"Set Logins for (\w+) Plan", reply_text)
        plan_key = m.group(1).lower() if m else None
        val = msg.text.strip()
        if val.isdigit() and plan_key:
            logins = int(val)
            db.update_premium_plan(plan_key, logins=logins)
            await msg.reply_text(
                f"✅ <b>Logins Updated!</b>\n\n{plan_key.capitalize()} plan now includes <b>{logins} Logins</b>.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(text="🔙 Premium Settings", callback_data="admin_premium_menu", style="primary")]])
            )
        else:
            await msg.reply_text("❌ Please enter a valid integer for logins (e.g. <code>10</code>).", parse_mode=ParseMode.HTML)

    elif "Set Validity Days for " in reply_text:
        if not is_admin(user_id):
            return
        m = re.search(r"Set Validity Days for (\w+) Plan", reply_text)
        plan_key = m.group(1).lower() if m else None
        val = msg.text.strip()
        if val.isdigit() and plan_key:
            days = int(val)
            db.update_premium_plan(plan_key, days=days)
            await msg.reply_text(
                f"✅ <b>Validity Updated!</b>\n\n{plan_key.capitalize()} plan validity is now <b>{days} Days</b>.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(text="🔙 Premium Settings", callback_data="admin_premium_menu", style="primary")]])
            )
        else:
            await msg.reply_text("❌ Please enter a valid integer for days (e.g. <code>30</code>).", parse_mode=ParseMode.HTML)

    elif "Set UPI ID:" in reply_text:
        if not is_admin(user_id):
            return
        new_upi = msg.text.strip()
        if "@" in new_upi:
            db.set_payment_config(upi_id=new_upi)
            await msg.reply_text(
                f"✅ <b>UPI ID Updated!</b>\n\nNew UPI ID: <code>{new_upi}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(text="🔙 Premium Settings", callback_data="admin_premium_menu", style="primary")]])
            )
        else:
            await msg.reply_text("❌ Invalid UPI ID. It should look like <code>username@bank</code>.", parse_mode=ParseMode.HTML)

    elif "Set Payee Name:" in reply_text:
        if not is_admin(user_id):
            return
        new_name = msg.text.strip()
        db.set_payment_config(payee_name=new_name)
        await msg.reply_text(
            f"✅ <b>Payee Name Updated!</b>\n\nNew Payee Name: <b>{new_name}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn(text="🔙 Premium Settings", callback_data="admin_premium_menu", style="primary")]])
        )

    elif "Set Referral Reward:" in reply_text:
        if not is_admin(user_id):
            return
        val = msg.text.strip()
        try:
            amt = float(val)
            db.set_referral_settings(reward=amt)
            await msg.reply_text(
                f"✅ <b>Referral Reward Updated!</b>\n\nReferrers will now receive <b>₹{amt:.2f}</b> per invited friend.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(text="🔙 Refer & Wallet Settings", callback_data="admin_referral_menu", style="primary")]])
            )
        except ValueError:
            await msg.reply_text("❌ Please enter a valid number (e.g. <code>25</code> or <code>50</code>).", parse_mode=ParseMode.HTML)

    elif "Set New User Welcome Bonus:" in reply_text:
        if not is_admin(user_id):
            return
        val = msg.text.strip()
        try:
            amt = float(val)
            db.set_referral_settings(welcome=amt)
            await msg.reply_text(
                f"✅ <b>Welcome Bonus Updated!</b>\n\nNew users will now receive <b>₹{amt:.2f}</b> upon registration.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(text="🔙 Refer & Wallet Settings", callback_data="admin_referral_menu", style="primary")]])
            )
        except ValueError:
            await msg.reply_text("❌ Please enter a valid number (e.g. <code>100</code>).", parse_mode=ParseMode.HTML)

    elif "Credit User Wallet:" in reply_text:
        if not is_admin(user_id):
            return
        tokens = msg.text.strip().split()
        if len(tokens) >= 2 and tokens[0].isdigit():
            target_uid = int(tokens[0])
            try:
                amt = float(tokens[1])
                new_b = db.add_user_balance(target_uid, amt, desc="Admin Manual Credit", tx_type="admin_credit")
                await msg.reply_text(
                    f"✅ <b>Wallet Credited!</b>\n\nUser ID: <code>{target_uid}</code>\nCredited: <b>₹{amt:.2f}</b>\nNew Balance: <b>₹{new_b:.2f}</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[btn(text="🔙 Refer & Wallet Settings", callback_data="admin_referral_menu", style="primary")]])
                )
                try:
                    await context.bot.send_message(
                        chat_id=target_uid,
                        text=f"🎁 <b>Funds Added to Your Wallet!</b>\n\nAdmin added <b>₹{amt:.2f}</b> to your wallet.\nCurrent Balance: <b>₹{new_b:.2f}</b>",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
            except ValueError:
                await msg.reply_text("❌ Invalid amount format. Example: <code>5784912530 100</code>", parse_mode=ParseMode.HTML)
        else:
            await msg.reply_text("❌ Invalid format. Please enter: <code>&lt;user_id&gt; &lt;amount&gt;</code>", parse_mode=ParseMode.HTML)

    elif "Grant" in reply_text and "Plan:" in reply_text:
        if not is_admin(user_id):
            return
        plan_match = re.search(r'Grant\s+(\w+)\s+Plan:', reply_text, re.IGNORECASE)
        plan_name = plan_match.group(1).lower() if plan_match else "monthly"
        target_str = msg.text.strip()
        if target_str.isdigit():
            target_uid = int(target_str)
            plans = db.get_premium_plans()
            plan_data = plans.get(plan_name) or plans.get("monthly", {"name": "Monthly", "days": 30, "logins": 10})
            days = int(plan_data.get("days", 30))
            logins = int(plan_data.get("logins", 10))
            prem = db.activate_user_premium(target_uid, plan_name, days, logins)
            await msg.reply_text(
                f"✅ <b>{plan_data.get('name', plan_name.capitalize())} Plan Granted Successfully!</b>\n\n"
                f"• User ID: <code>{target_uid}</code>\n"
                f"• Plan: <b>{plan_data.get('name', plan_name.capitalize())}</b>\n"
                f"• Validity: <b>+{days} Days</b> (Expires: <code>{prem['expires_at']}</code>)\n"
                f"• Logins Added: <b>+{logins} Logins / Service</b>\n"
                f"• Total Remaining: <b>{prem['remaining_logins']} Logins</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")]])
            )
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        f"🎉 <b>Premium Subscription Activated!</b> ⚡\n\n"
                        f"• 📦 Plan: <b>{plan_data.get('name', plan_name.capitalize())}</b>\n"
                        f"• 📅 Validity: <b>{days} Days</b> (Expires: <code>{prem['expires_at']}</code>)\n"
                        f"• 🎟️ Logins Granted: <b>{logins} Logins / Service</b>\n\n"
                        "Enjoy full access to all TV platforms!"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[btn(text="📺 Activate TV", callback_data="menu_activate", style="success")]])
                )
            except Exception:
                pass
        else:
            await msg.reply_text("❌ Invalid User ID. Please enter numeric User ID (e.g. <code>5784912530</code>).", parse_mode=ParseMode.HTML)

    elif "Grant Custom Logins:" in reply_text:
        if not is_admin(user_id):
            return
        tokens = msg.text.strip().split()
        if len(tokens) >= 2 and tokens[0].isdigit() and tokens[1].isdigit():
            target_uid = int(tokens[0])
            logins = int(tokens[1])
            prem = db.add_user_logins(target_uid, logins)
            await msg.reply_text(
                f"✅ <b>Custom Logins Added Successfully!</b>\n\n"
                f"• User ID: <code>{target_uid}</code>\n"
                f"• Logins Added: <b>+{logins} Logins / Service</b>\n"
                f"• Expiry: <code>{prem['expires_at']}</code>\n"
                f"• Total Remaining: <b>{prem['remaining_logins']} Logins</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")]])
            )
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        f"🎉 <b>+{logins} TV Logins Added to Your Account!</b> 🎟️\n\n"
                        f"• Added: <b>{logins} Logins / Service</b>\n"
                        f"• Total Available: <b>{prem['remaining_logins']}</b>\n"
                        f"• Valid Until: <code>{prem['expires_at']}</code>\n\n"
                        "Enjoy streaming!"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[btn(text="📺 Activate TV", callback_data="menu_activate", style="success")]])
                )
            except Exception:
                pass
        else:
            await msg.reply_text("❌ Invalid format. Please enter: <code>&lt;user_id&gt; &lt;logins&gt;</code> (e.g. <code>5784912530 15</code>).", parse_mode=ParseMode.HTML)


async def handle_media_activation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.reply_to_message:
        return
        
    allowed, err_msg = check_chat_mode(update)
    if not allowed:
        return
        
    reply_text = msg.reply_to_message.text
    if not reply_text:
        return
        
    if ("Enter your" in reply_text and "TV code" in reply_text) or ("QR code image" in reply_text):
        service = None
        if "JIOHOTSTAR" in reply_text.upper() or "HOTSTAR" in reply_text.upper():
            service = "jiohotstar"
        elif "SPOTIFY" in reply_text.upper():
            service = "spotify"
            
        if not service:
            return
            
        # Scan the QR
        try:
            processing_msg = await msg.reply_text("🔍 <b>Scanning QR code...</b>", parse_mode=ParseMode.HTML)
        except Exception:
            processing_msg = None
        
        try:
            if msg.photo:
                photo_file = await msg.photo[-1].get_file()
            elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
                photo_file = await msg.document.get_file()
            else:
                if processing_msg:
                    try:
                        await processing_msg.edit_text("❌ Please reply with a valid image file containing the QR code.", parse_mode=ParseMode.HTML)
                    except Exception:
                        pass
                return
                
            image_bytes = await photo_file.download_as_bytearray()
            
            def _request_qr(img_b):
                files = {'file': ('qr_image.jpg', io.BytesIO(img_b), 'image/jpeg')}
                return requests.post('https://api.qrserver.com/v1/read-qr-code/', files=files, timeout=15)
                
            response = await asyncio.to_thread(_request_qr, image_bytes)
            
            if response.status_code != 200:
                if processing_msg:
                    try:
                        await processing_msg.edit_text("❌ QR scanner service error. Please type the text code manually.", parse_mode=ParseMode.HTML)
                    except Exception:
                        pass
                return
                
            data = response.json()
            if not data or not data[0].get('symbol') or not data[0]['symbol'][0].get('data'):
                if processing_msg:
                    try:
                        await processing_msg.edit_text("❌ No QR code detected in the image. Please try again with a clearer picture or enter the code manually.", parse_mode=ParseMode.HTML)
                    except Exception:
                        pass
                return
                
            qr_data = data[0]['symbol'][0]['data']
            
            if service == "jiohotstar":
                if "hotstar.com" not in qr_data and not re.search(r'[a-zA-Z0-9]{4,8}', qr_data):
                    if processing_msg:
                        try:
                            await processing_msg.edit_text("❌ This QR code is not valid for <code>hotstar.com</code>.", parse_mode=ParseMode.HTML)
                        except Exception:
                            pass
                    return
            elif service == "spotify":
                if "spotify.com" not in qr_data and "spotify:" not in qr_data and not re.search(r'[a-zA-Z0-9]{6}', qr_data):
                    if processing_msg:
                        try:
                            await processing_msg.edit_text("❌ This QR code is not valid for <code>spotify.com</code>.", parse_mode=ParseMode.HTML)
                        except Exception:
                            pass
                    return
                
            if processing_msg:
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
            await start_tv_activation(update, context, service, qr_data)
            
        except Exception as e:
            if processing_msg:
                try:
                    await processing_msg.edit_text(f"❌ Scan failed: {e}\nPlease enter your TV code as text manually.", parse_mode=ParseMode.HTML)
                except Exception:
                    pass


async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    allowed, err_msg = check_chat_mode(update)
    if not allowed:
        return
    if msg.reply_to_message:
        await handle_replies(update, context)
    elif msg.chat.type == "private":
        # Check if the text matches a TV code format (6 alphanumeric chars or 8 digits with spaces/dashes)
        text = msg.text.strip()
        digits_only = re.sub(r'\D', '', text)
        clean_text = re.sub(r'[\s\-_.:,]', '', text)
        
        is_netflix_8digit = len(digits_only) == 8
        is_other_6char = len(clean_text) == 6 and clean_text.isalnum()
        
        if is_netflix_8digit or is_other_6char:
            if not await check_force_join(update, context):
                return
            user_id = update.effective_user.id
            db.register_user(user_id, update.effective_user.username, update.effective_user.first_name)
            raw_code = digits_only if is_netflix_8digit else clean_text.upper()
            
            # Check if there is a recently selected service in context.user_data
            selected_service = context.user_data.get("selected_service") if context.user_data is not None else None
            if selected_service:
                context.user_data.pop("selected_service", None)
                await start_tv_activation(update, context, selected_service, raw_code)
                return
            
            keyboard = [
                [
                    btn(text="🔴 Netflix", callback_data=f"activate:netflix:{raw_code}", style="success"),
                    btn(text="🟠 Crunchyroll", callback_data=f"activate:crunchyroll:{raw_code}", style="success")
                ],
                [
                    btn(text="🟣 HBO Max", callback_data=f"activate:hbomax:{raw_code}", style="success"),
                    btn(text="🔵 Surfshark", callback_data=f"activate:surfshark:{raw_code}", style="success")
                ],
                [
                    btn(text="🟢 Spotify", callback_data=f"activate:spotify:{raw_code}", style="success"),
                    btn(text="🔵 JioHotstar", callback_data=f"activate:jiohotstar:{raw_code}", style="success")
                ],
                [
                    btn(text="🎬 Prime Video", callback_data=f"activate:primevideo:{raw_code}", style="success")
                ],
                [
                    btn(text="🔙 Main Menu", callback_data="menu_main", style="primary")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await msg.reply_text(
                f"📺 <b>Select Service for Code:</b> <code>{raw_code}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )

async def cleanup_old_messages_loop(application):
    try:
        await asyncio.sleep(10)
        while True:
            try:
                now = datetime.now()
                threshold = now - timedelta(days=1)
                
                to_delete = []
                remaining = []
                
                with db.lock:
                    sent_msgs = db.data.get("sent_messages", [])
                    for msg in sent_msgs:
                        try:
                            sent_at = datetime.strptime(msg["sent_at"], "%Y-%m-%d %H:%M:%S")
                            if sent_at < threshold:
                                to_delete.append(msg)
                            else:
                                remaining.append(msg)
                        except:
                            pass
                    db.data["sent_messages"] = remaining
                    db.save_unlocked()
                    
                for msg in to_delete:
                    try:
                        await application.bot.delete_message(chat_id=msg["chat_id"], message_id=msg["message_id"])
                    except Exception:
                        pass
            except Exception as e:
                print(f"Error in cleanup loop: {e}")
                
            await asyncio.sleep(300)
    except asyncio.CancelledError:
        pass


async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    allowed, err_msg = check_chat_mode(update)
    if not allowed:
        if err_msg:
            await update.message.reply_text(err_msg, parse_mode=ParseMode.HTML)
        return
    if not await check_force_join(update, context):
        return
    await send_user_premium_plans_menu(update, context, edit=False)


async def add_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 <b>Admin only!</b>", parse_mode=ParseMode.HTML)
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "ℹ️ <b>Usage:</b> <code>/addpremium &lt;user_id&gt; &lt;days&gt; &lt;logins&gt;</code>\n\n"
            "Example: <code>/addpremium 5784912530 30 15</code>",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        target_id = int(args[0])
        days = int(args[1])
        logins = int(args[2])
    except ValueError:
        await update.message.reply_text("❌ User ID, days, and logins must be valid integers.", parse_mode=ParseMode.HTML)
        return

    prem = db.activate_user_premium(target_id, "custom_admin", days, logins)
    await update.message.reply_text(
        f"✅ <b>Premium Granted Successfully!</b>\n\n"
        f"• User ID: <code>{target_id}</code>\n"
        f"• Validity: <b>+{days} Days</b> (Expires: <code>{prem['expires_at']}</code>)\n"
        f"• Logins Added: <b>+{logins} Logins / Service</b> (Total Available: <b>{prem['remaining_logins']}</b> across all services)",
        parse_mode=ParseMode.HTML
    )

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"🎉 <b>Admin Granted You Premium Access!</b> ⚡\n\n"
                f"• Validity: <b>{days} Days</b> (Expires: <code>{prem['expires_at']}</code>)\n"
                f"• Logins Granted: <b>{logins} Logins / Service</b> 🎟️\n\n"
                "Enjoy full access to all TV platforms!"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[btn(text="📺 Activate TV", callback_data="menu_activate", style="success")]])
        )
    except Exception:
        pass


async def remove_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 <b>Admin only!</b>", parse_mode=ParseMode.HTML)
        return

    args = context.args
    if not args:
        await update.message.reply_text("ℹ️ <b>Usage:</b> <code>/removepremium &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID must be a numeric integer.", parse_mode=ParseMode.HTML)
        return

    db.remove_user_premium(target_id)
    await update.message.reply_text(f"✅ Premium revoked for user <code>{target_id}</code>.", parse_mode=ParseMode.HTML)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 <b>Admin only!</b>", parse_mode=ParseMode.HTML)
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "ℹ️ <b>Usage of /add:</b>\n\n"
            "• <b>Grant Login Quota:</b>\n"
            "  <code>/add &lt;logins&gt; &lt;user_id&gt;</code>\n"
            "  <i>Example:</i> <code>/add 10 5784912530</code>\n\n"
            "• <b>Grant Subscription Plan:</b>\n"
            "  <code>/add &lt;plan&gt; &lt;user_id&gt;</code>\n"
            "  <i>Example:</i> <code>/add monthly 5784912530</code>\n"
            "  <i>Example:</i> <code>/add quarterly 5784912530</code>\n"
            "  <i>Example:</i> <code>/add yearly 5784912530</code>\n\n"
            "<i>Tip: Argument order doesn't matter (e.g. <code>/add 5784912530 10</code> or <code>/add 5784912530 monthly</code> also works)!</i>",
            parse_mode=ParseMode.HTML
        )
        return

    arg1 = args[0].strip()
    arg2 = args[1].strip()

    plans = db.get_premium_plans()
    plan_aliases = {
        "monthly": "monthly", "month": "monthly", "1m": "monthly",
        "quarterly": "quarterly", "quarter": "quarterly", "3m": "quarterly",
        "yearly": "yearly", "year": "yearly", "annual": "yearly", "1y": "yearly"
    }

    target_id = None
    action_type = None  # "plan" or "logins"
    action_val = None

    def parse_token(t):
        t_low = t.lower()
        if t_low in plan_aliases:
            return "plan", plan_aliases[t_low]
        if t_low in plans:
            return "plan", t_low
        if t.isdigit():
            return "digit", int(t)
        return None, None

    t1_type, t1_val = parse_token(arg1)
    t2_type, t2_val = parse_token(arg2)

    if t1_type == "plan" and t2_type == "digit":
        action_type = "plan"
        action_val = t1_val
        target_id = t2_val
    elif t2_type == "plan" and t1_type == "digit":
        action_type = "plan"
        action_val = t2_val
        target_id = t1_val
    elif t1_type == "digit" and t2_type == "digit":
        if t1_val > 1000000 and t2_val <= 1000000:
            target_id = t1_val
            action_type = "logins"
            action_val = t2_val
        else:
            target_id = t2_val
            action_type = "logins"
            action_val = t1_val
    else:
        await update.message.reply_text(
            "❌ <b>Invalid Arguments!</b>\n\n"
            "Format:\n"
            "• <code>/add &lt;logins&gt; &lt;user_id&gt;</code> (e.g. <code>/add 10 5784912530</code>)\n"
            "• <code>/add &lt;plan&gt; &lt;user_id&gt;</code> (e.g. <code>/add monthly 5784912530</code>)",
            parse_mode=ParseMode.HTML
        )
        return

    if action_type == "plan":
        plan_key = action_val
        plan_data = plans.get(plan_key) or plans.get("monthly", {"name": "Monthly", "days": 30, "logins": 10})
        days = int(plan_data.get("days", 30))
        logins = int(plan_data.get("logins", 10))
        prem = db.activate_user_premium(target_id, plan_key, days, logins)

        await update.message.reply_text(
            f"✅ <b>{plan_data.get('name', plan_key.capitalize())} Plan Granted Successfully!</b>\n\n"
            f"• 👤 User ID: <code>{target_id}</code>\n"
            f"• 📦 Plan: <b>{plan_data.get('name', plan_key.capitalize())}</b>\n"
            f"• 📅 Validity: <b>+{days} Days</b> (Expires: <code>{prem['expires_at']}</code>)\n"
            f"• 🎟️ Logins Added: <b>+{logins} Logins / Service</b>\n"
            f"• 📊 Total Available: <b>{prem['remaining_logins']} Logins</b>",
            parse_mode=ParseMode.HTML
        )

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    f"🎉 <b>Premium Subscription Activated!</b> ⚡\n\n"
                    f"• 📦 Plan: <b>{plan_data.get('name', plan_key.capitalize())}</b>\n"
                    f"• 📅 Validity: <b>{days} Days</b> (Expires: <code>{prem['expires_at']}</code>)\n"
                    f"• 🎟️ Logins Granted: <b>{logins} Logins / Service</b>\n\n"
                    "Enjoy full access to all TV platforms!"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(text="📺 Activate TV", callback_data="menu_activate", style="success")]])
            )
        except Exception:
            pass

    elif action_type == "logins":
        logins = action_val
        prem = db.add_user_logins(target_id, logins)

        await update.message.reply_text(
            f"✅ <b>Logins Added Successfully!</b>\n\n"
            f"• 👤 User ID: <code>{target_id}</code>\n"
            f"• 🎟️ Added: <b>+{logins} Logins / Service</b>\n"
            f"• 📅 Valid Until: <code>{prem['expires_at']}</code>\n"
            f"• 📊 Total Available: <b>{prem['remaining_logins']} Logins</b>",
            parse_mode=ParseMode.HTML
        )

        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    f"🎉 <b>+{logins} TV Logins Added to Your Account!</b> 🎟️\n\n"
                    f"• Added: <b>{logins} Logins / Service</b>\n"
                    f"• Total Available: <b>{prem['remaining_logins']}</b>\n"
                    f"• Valid Until: <code>{prem['expires_at']}</code>\n\n"
                    "Tap below to start activating:"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[btn(text="📺 Activate TV", callback_data="menu_activate", style="success")]])
            )
        except Exception:
            pass


async def restoredb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 <b>Admin only!</b>", parse_mode=ParseMode.HTML)
        return

    msg = await update.message.reply_text("⏳ <b>Fetching and restoring database from GitHub...</b>", parse_mode=ParseMode.HTML)
    ok_db, github_data = db.restore_from_github()
    if ok_db and isinstance(github_data, dict) and "users" in github_data:
        db.data = github_data
        db.save_unlocked()
        users_count = len(github_data.get("users", {}))
        await msg.edit_text(
            f"✅ <b>Database Restored from GitHub!</b>\n\n"
            f"📦 <b>Repo:</b> <code>{GITHUB_BACKUP_REPO}</code>\n"
            f"👥 <b>Total Restored Users:</b> <b>{users_count} users</b>\n"
            f"🕒 <b>Time:</b> <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>",
            parse_mode=ParseMode.HTML
        )
    else:
        await msg.edit_text("❌ <b>Failed to restore database from GitHub.</b> Check token or repo permissions.", parse_mode=ParseMode.HTML)


async def backupdb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 <b>Admin only!</b>", parse_mode=ParseMode.HTML)
        return

    msg = await update.message.reply_text("⏳ <b>Backing up database to GitHub...</b>", parse_mode=ParseMode.HTML)
    success, res = await sync_github_db_backup()
    if success:
        users_count = len(db.data.get("users", {}))
        await msg.edit_text(
            f"✅ <b>GitHub DB Backup Success!</b>\n\n"
            f"📦 <b>Repo:</b> <code>{GITHUB_BACKUP_REPO}</code>\n"
            f"👥 <b>Backed Up Users:</b> <b>{users_count} users</b>\n"
            f"⚡ <b>Status:</b> <b>{res}</b>\n"
            f"🕒 <b>Time:</b> <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>",
            parse_mode=ParseMode.HTML
        )
    else:
        await msg.edit_text(f"❌ <b>GitHub Backup Failed:</b>\n\n<code>{res}</code>", parse_mode=ParseMode.HTML)


GITHUB_BACKUP_TOKEN = os.getenv("GITHUB_BACKUP_TOKEN", "").strip()
GITHUB_BACKUP_REPO = os.getenv("GITHUB_BACKUP_REPO", "").strip()


async def sync_github_db_backup():
    """Reads db.json, hashes content, checks sha on GitHub, and commits backup if changed."""
    try:
        if not GITHUB_BACKUP_TOKEN or not GITHUB_BACKUP_REPO:
            return False, "GitHub token or repository not configured"

        db_path = os.path.join(BASE_DIR, "db.json")
        if not os.path.exists(db_path):
            if 'db' in globals() and hasattr(db, 'save_unlocked'):
                with db.lock:
                    db.save_unlocked()
            if not os.path.exists(db_path):
                return False, "db.json not found on disk"

        with open(db_path, "r", encoding="utf-8") as f:
            content_str = f.read()

        content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

        headers = {
            "Authorization": f"token {GITHUB_BACKUP_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "TVLoginBot-AutoBackup"
        }

        url = f"https://api.github.com/repos/{GITHUB_BACKUP_REPO}/contents/db.json"
        r_get = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
        sha = None
        if r_get.status_code == 200:
            existing_data = r_get.json()
            sha = existing_data.get("sha")
            existing_content = existing_data.get("content", "").replace("\n", "").strip()
            if existing_content == content_b64:
                return True, "No changes detected (Already up to date)"

            # CRITICAL PROTECTION: Never allow an empty or smaller database to overwrite a bigger backup on GitHub!
            try:
                remote_raw = base64.b64decode(existing_content).decode("utf-8")
                remote_json = json.loads(remote_raw)
                if isinstance(remote_json, dict) and "users" in remote_json:
                    remote_users_count = len(remote_json.get("users", {}))
                    local_users_count = len(json.loads(content_str).get("users", {}))
                    if remote_users_count > local_users_count:
                        # Remote has more users! Do NOT overwrite! Auto-merge remote into local instead!
                        if 'db' in globals() and hasattr(db, 'load'):
                            db.load()
                        return False, f"Backup aborted: GitHub has {remote_users_count} users while local has {local_users_count}. Auto-restored from GitHub instead!"
            except Exception as e:
                print(f"[!] Warning comparing DB backup sizes: {e}")

        payload = {
            "message": f"Auto-backup db.json ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})",
            "content": content_b64
        }
        if sha:
            payload["sha"] = sha

        r_put = await asyncio.to_thread(requests.put, url, headers=headers, json=payload, timeout=20)
        if r_put.status_code in (200, 201):
            return True, "Backup synced successfully"
        else:
            return False, f"GitHub API error: {r_put.status_code}"
    except Exception as e:
        return False, str(e)


async def github_db_backup_loop(application):
    """Runs in background every 6 hours, pushing db.json to GitHub if changed."""
    print("[*] GitHub 6-hour auto-backup loop started.")
    await asyncio.sleep(20) # Boot delay
    while True:
        try:
            success, msg = await sync_github_db_backup()
            if success:
                if "No changes" not in msg:
                    print(f"[*] [GitHub DB Backup] {msg} at {datetime.now().strftime('%H:%M:%S')}")
            else:
                print(f"[!] [GitHub DB Backup Failed] {msg}")
        except Exception as e:
            print(f"[!] [GitHub Auto-Backup Exception] {e}")

        try:
            await asyncio.sleep(6 * 3600) # 6 hours
        except asyncio.CancelledError:
            break


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Ignore transient timeout/network errors so bot never crashes
    if isinstance(context.error, (telegram.error.TimedOut, telegram.error.NetworkError, telegram.error.RetryAfter)):
        return
    print(f"[!] Unhandled exception: {context.error}")


async def post_init(application):
    asyncio.create_task(cleanup_old_messages_loop(application))
    asyncio.create_task(github_db_backup_loop(application))




# ==============================================================================
# Telegram Bot Background Runner
# ==============================================================================
def run_telegram_bot_sync():
    """Runs the Telegram Bot polling loop in a dedicated background daemon thread."""
    if not BOT_TOKEN:
        print("[!] BOT_TOKEN is empty. Telegram bot polling disabled.")
        return
        
    print("[*] Initializing embedded Telegram Bot worker...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    t_request = HTTPXRequest(
        connection_pool_size=30,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )

    if not BOT_TOKEN:
        print("[!] FATAL ERROR: BOT_TOKEN is not configured! Please provide BOT_TOKEN in .env file or environment variables.")
        return

    tg_app = Application.builder().token(BOT_TOKEN).request(t_request).post_init(post_init).build()
    tg_app.add_error_handler(error_handler)

    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("help", help_command))
    tg_app.add_handler(CommandHandler("tv", tv_command))
    tg_app.add_handler(CommandHandler("netflix", netflix_command))
    tg_app.add_handler(CommandHandler("netflixlink", netflix_link_command))
    tg_app.add_handler(CommandHandler("link", netflix_link_command))
    tg_app.add_handler(CommandHandler("crunchy", crunchy_command))
    tg_app.add_handler(CommandHandler("crunchyroll", crunchy_command))
    tg_app.add_handler(CommandHandler("hbomax", hbomax_command))
    tg_app.add_handler(CommandHandler("surfshark", surfshark_command))
    tg_app.add_handler(CommandHandler("spotify", spotify_command))
    tg_app.add_handler(CommandHandler("jiohotstar", jiohotstar_command))
    tg_app.add_handler(CommandHandler("hotstar", jiohotstar_command))
    tg_app.add_handler(CommandHandler("prime", prime_command))
    tg_app.add_handler(CommandHandler("primevideo", prime_command))
    tg_app.add_handler(CommandHandler("upload", upload_command))
    tg_app.add_handler(CommandHandler("settokenlimit", set_token_limit_command))
    tg_app.add_handler(CommandHandler("tokenlimit", set_token_limit_command))
    tg_app.add_handler(CommandHandler("userstats", userstats_command))
    tg_app.add_handler(CommandHandler("settings", settings_command))
    tg_app.add_handler(CommandHandler("clearvault", clearvault_command))
    tg_app.add_handler(CommandHandler("delvault", clearvault_command))
    tg_app.add_handler(CommandHandler("premium", premium_command))
    tg_app.add_handler(CommandHandler("plans", premium_command))
    tg_app.add_handler(CommandHandler("addpremium", add_premium_command))
    tg_app.add_handler(CommandHandler("add", add_command))
    tg_app.add_handler(CommandHandler("removepremium", remove_premium_command))
    tg_app.add_handler(CommandHandler("backupdb", backupdb_command))
    tg_app.add_handler(CommandHandler("restoredb", restoredb_command))
    tg_app.add_handler(CommandHandler("toggleads", cmd_toggleads))
    tg_app.add_handler(CommandHandler("ads", cmd_toggleads))
    
    tg_app.add_handler(CallbackQueryHandler(callback_query_handler))
    tg_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_admin_broadcast_message), group=-1)
    tg_app.add_handler(MessageHandler(filters.PHOTO, handle_media_activation))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("[*] Bot started! Press Ctrl+C to stop.")
    while True:
        try:
            tg_app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=5, close_loop=False, stop_signals=None)
            break
        except (telegram.error.NetworkError, Exception) as e:
            print(f"[!] Network reconnecting... {e}")
            time.sleep(3)





@app.on_event("startup")
async def on_startup():
    if BOT_TOKEN:
        print("[*] Starting embedded Telegram Bot worker in background thread...")
        threading.Thread(target=run_telegram_bot_sync, daemon=True, name="TelegramBotWorker").start()
    else:
        print("[!] Warning: BOT_TOKEN not configured. Telegram Bot worker skipped.")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"[*] Starting Unified XDROID Server & Telegram Bot on port {port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
