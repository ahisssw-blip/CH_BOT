"""Centralized bot configuration loading."""

import json
import logging
import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
CHANNELS_FILE = BASE_DIR / "channels.json"
DB_PATH = Path(os.environ.get("BOT_DATABASE_PATH", str(BASE_DIR / "database.db"))).expanduser()
logger = logging.getLogger(__name__)


def load_config() -> dict[str, Any]:
    """Load the existing JSON configuration without logging secret values."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as config_file:
            data = json.load(config_file)
        if not isinstance(data, dict):
            raise ValueError("The root of config.json must be an object")
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Unable to load config.json")
        return {}


def load_public_channels() -> list[dict[str, str]]:
    """Load configured public channels; malformed entries are ignored."""
    if not CHANNELS_FILE.exists():
        return []
    try:
        with CHANNELS_FILE.open("r", encoding="utf-8") as channels_file:
            data = json.load(channels_file)
        channels = data.get("channels", []) if isinstance(data, dict) else []
        if not isinstance(channels, list):
            return []
        return [
            channel for channel in channels
            if isinstance(channel, dict)
            and isinstance(channel.get("name"), str)
            and isinstance(channel.get("url"), str)
        ]
    except (OSError, json.JSONDecodeError):
        logger.exception("Unable to load channels.json")
        return []

def load_single_private_channels() -> list[dict[str, str]]:
    """تحميل القنوات الخاصة المعروضة في زر الاشتراك بقناة خاصة واحدة."""
    if not CHANNELS_FILE.exists():
        return []
    try:
        with CHANNELS_FILE.open("r", encoding="utf-8") as channels_file:
            data = json.load(channels_file)

        channels = (
            data.get("single_private_channels", [])
            if isinstance(data, dict)
            else []
        )
        if not isinstance(channels, list):
            return []

        return [
            channel for channel in channels
            if isinstance(channel, dict)
            and isinstance(channel.get("name"), str)
            and isinstance(channel.get("url"), str)
        ]
    except (OSError, json.JSONDecodeError):
        logger.exception("Unable to load single private channels from channels.json")
        return []


CFG = load_config()
TOKEN = str(CFG.get("TOKEN") or "")
_admin_ids = CFG.get("ADMIN_IDS", [])
if not isinstance(_admin_ids, list):
    logger.warning("ADMIN_IDS must be a list; using an empty admin list")
    _admin_ids = []
ADMIN_IDS = [int(value) for value in _admin_ids if str(value).lstrip("-").isdigit()]


def _int_setting(name: str, default: int = 0) -> int:
    try:
        return int(CFG.get(name, default))
    except (TypeError, ValueError):
        logger.warning("Invalid integer configuration for %s; using default", name)
        return default


CHANNEL_ID = _int_setting("CHANNEL_ID")
CHANNEL_LINK = str(CFG.get("CHANNEL_LINK", ""))
BACKUP_CH_ID = _int_setting("BACKUP_CHANNEL_ID", CHANNEL_ID)
SUPPORT = CFG.get("SUPPORT", {}) if isinstance(CFG.get("SUPPORT", {}), dict) else {}
WALLETS = CFG.get("WALLETS", {}) if isinstance(CFG.get("WALLETS", {}), dict) else {}
SUBS = CFG.get("SUBSCRIPTIONS", {}) if isinstance(CFG.get("SUBSCRIPTIONS", {}), dict) else {}
BTNS = CFG.get("BUTTONS", {}) if isinstance(CFG.get("BUTTONS", {}), dict) else {}
TELEGRAM_CONTACT = str(CFG.get("TELEGRAM_CONTACT", "https://t.me/your_support_username"))
