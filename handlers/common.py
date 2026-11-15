"""Shared Telegram helpers, backups, and broadcast execution."""
import asyncio
import datetime
import json
import logging
import tempfile
from pathlib import Path
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes
from telegram.helpers import escape_markdown

from config import (
    ADMIN_IDS, BACKUP_CH_ID, CHANNEL_ID, DB_PATH, BTNS,
    load_public_channels, load_single_private_channels,
)

from database import (
    db_backup_to, db_broadcast_progress, db_finish_broadcast,
    db_pending_broadcasts, db_mark_user_blocked,
)
from locales import TEXT

logger = logging.getLogger(__name__)

async def backup_database(context: ContextTypes.DEFAULT_TYPE):
    snapshot_path = None
    try:
        if not DB_PATH.exists(): return
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        with tempfile.NamedTemporaryFile(prefix="bot-backup-", suffix=".db", delete=False) as f:
            snapshot_path = Path(f.name)
        db_backup_to(snapshot_path)
        with snapshot_path.open("rb") as f:
            await context.bot.send_document(
                chat_id=BACKUP_CH_ID,
                document=f,
                caption=TEXT["backup_caption"].format(timestamp=now_str)
            )
        logger.info("✅ Periodic backup sent successfully.")
    except Exception:
        logger.exception("Database backup failed")
    finally:
        if snapshot_path:
            snapshot_path.unlink(missing_ok=True)

async def run_broadcast(bot, broadcast_id: int, message: str, targets: list[int],
                        start_index: int, sent: int, failed: int, status_msg=None) -> None:
    """Send a broadcast sequentially and persist progress after each recipient."""
    safe_message = escape_markdown(message[:3900])
    blocked = 0
    for index in range(start_index, len(targets)):
        user_id = int(targets[index])
        try:
            await bot.send_message(
                chat_id=user_id,
                text=TEXT["broadcast_body"].format(message=safe_message),
                parse_mode=ParseMode.MARKDOWN,
            )
            sent += 1
        except TelegramError as exc:
            failed += 1
            error_text = str(exc).lower()
            if "blocked" in error_text or "forbidden" in error_text:
                blocked += 1
                db_mark_user_blocked(user_id)
            logger.warning("Broadcast %s failed for user_id=%s: %s", broadcast_id, user_id, exc)
        db_broadcast_progress(broadcast_id, index + 1, sent, failed)
        await asyncio.sleep(0.05)

    db_finish_broadcast(broadcast_id)
    if status_msg is not None:
        try:
            await status_msg.edit_text(
                f"✅ تم الإرسال لـ {sent} مستخدم\n🚫 {blocked} مستخدمين حظروا البوت"
            )
        except TelegramError:
            logger.exception("Unable to update broadcast status message for broadcast_id=%s", broadcast_id)

async def resume_broadcasts(application: Application) -> None:
    """Resume unfinished broadcasts after a process restart."""
    for row in db_pending_broadcasts():
        try:
            targets = json.loads(row["targets_json"] or "[]")
            if not isinstance(targets, list):
                raise ValueError("Invalid broadcast target list")
            await run_broadcast(
                application.bot,
                int(row["id"]),
                str(row["message"] or ""),
                targets,
                int(row["last_index"] or 0),
                int(row["sent_count"] or 0),
                int(row["fail_count"] or 0),
            )
        except Exception:
            logger.exception("Unable to resume broadcast_id=%s", row["id"])

async def is_subscribed(bot, user_id: int) -> bool:
    if user_id in ADMIN_IDS: return True
    if not CHANNEL_ID: return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ("member", "creator", "administrator")
    except TelegramError:
        logger.exception("Unable to check channel membership for user_id=%s", user_id)
        return False

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            logger.warning("Rejected admin command without an effective user")
            return
        uid = update.effective_user.id
        if uid not in ADMIN_IDS: return
        return await func(update, context)
    return wrapper

async def safe_send(bot, chat_id, **kwargs):
    try:
        return await bot.send_message(chat_id=chat_id, **kwargs)
    except TelegramError:
        logger.exception("Telegram send failed for chat_id=%s", chat_id)
        return None

def build_channels_keyboard(include_back: bool = False) -> InlineKeyboardMarkup:
    """يبني لوحة مفاتيح القنوات العامة مع زر الاشتراك بالقنوات الخاصة"""
    channels = load_public_channels()
    kbd = []

    if channels:
        row = []
        for ch in channels:
            row.append(InlineKeyboardButton(ch["name"], url=ch["url"]))
            if len(row) == 3:
                kbd.append(row)
                row = []
        if row:
            kbd.append(row)

    # زر عريض للاشتراك بالقنوات الخاصة
    # زر عريض للفاصل
    kbd.append([InlineKeyboardButton("👇👇👇👇👇👇👇👇👇", callback_data="noop")])

    # زر الاشتراك بقناة خاصة واحدة
    kbd.append([
        InlineKeyboardButton(
            "🟣الاشـتـراك بـقـنـاة خـاصـة واحـدة🟣",
            callback_data="single_private_sub"
        )
    ])

    # زر الاشتراك بالقنوات الخاصة المدفوعة
    kbd.append([
        InlineKeyboardButton(
            "🔴الإشـتـراك الـكـامـل بـالـقـنـوات الـخـاصـة🔴",
            callback_data="private_sub"
        )
    ])


    if include_back:
        kbd.append([InlineKeyboardButton(BTNS.get("btn_back", "🔙 رجوع"), callback_data="main_menu")])

    return InlineKeyboardMarkup(kbd)
