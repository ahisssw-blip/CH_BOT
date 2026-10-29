"""Payment submission and admin notification flow."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from config import ADMIN_IDS
from database import db_insert_payment_if_new, MAX_TXID_LENGTH
from handlers.common import safe_send
from locales import TEXT

logger = logging.getLogger(__name__)

async def process_payment_submission(update: Update, context: ContextTypes.DEFAULT_TYPE, user, text: str) -> bool:
    """Handle a submitted transaction code; return whether the message was consumed."""
    if not context.user_data.get("waiting_code"):
        return False
    if not text.strip() or len(text.strip()) > MAX_TXID_LENGTH:
        logger.warning("Ignored transaction code with invalid length from user_id=%s", user.id)
        return True
    sub_type = context.user_data.get("sub_type", "VIP")
    pay_method = context.user_data.get("pay_method", "unknown")
    try:
        pay_id, duplicate = db_insert_payment_if_new(user.id, sub_type, pay_method, text)
    except Exception:
        logger.exception("Unable to record payment from user_id=%s", user.id)
        return True
    if duplicate:
        logger.info("Duplicate transaction code ignored for user_id=%s", user.id)
        return True
    context.user_data["waiting_code"] = False
    await update.message.reply_text(TEXT["payment_received"])
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ قبول", callback_data=f"adm_ok_{user.id}_{pay_id}"),
         InlineKeyboardButton("❌ رفض", callback_data=f"adm_no_{user.id}_{pay_id}")]
    ])
    for aid in ADMIN_IDS:
        await safe_send(
            context.bot, aid,
            text=f"🔔 *طلب دفع جديد!*\n"
                 f"👤 {escape_markdown(user.first_name or '')}\n"
                 f"🆔 `{user.id}`\n"
                 f"💎 {sub_type}\n"
                 f"💳 {pay_method}\n"
                 f"🔑 `{escape_markdown(text)}`",
            reply_markup=kbd,
            parse_mode=ParseMode.MARKDOWN
        )
    return True
