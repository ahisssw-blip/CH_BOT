"""Admin command and callback handlers."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from config import ADMIN_IDS
from database import db_get_stats, db_get_pending_payments, db_decide_payment
from handlers.common import admin_only, backup_database, safe_send
from locales import TEXT

logger = logging.getLogger(__name__)

@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waiting_code"] = False
    context.user_data.pop("pay_method", None)
    context.user_data.pop("sub_type", None)
    stats = db_get_stats()
    kbd = [
        [InlineKeyboardButton("📊 الإحصائيات التفصيلية", callback_data="adm_stats"),
         InlineKeyboardButton("💾 نسخة احتياطية فورية", callback_data="adm_backup")],
        [InlineKeyboardButton("📋 طلبات الدفع المعلقة", callback_data="adm_pending")],
        [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban_menu"),
         InlineKeyboardButton("🔓 فك حظر", callback_data="adm_unban_menu")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="end")]
    ]
    text = (
        f"🛡 *لوحة تحكم الإدارة الشاملة*\n\n"
        f"👥 إجمالي المستخدمين: `{stats['total']}`\n"
        f"👑 المشتركون النشطون: `{stats['active']}`\n"
        f"⏳ طلبات بانتظار المراجعة: `{stats['pending']}`\n\n"
        f"📅 جدد اليوم: `{stats['new_today']}`\n"
        f"📅 جدد هذا الأسبوع: `{stats['new_week']}`\n"
        f"📅 جدد هذا الشهر: `{stats['new_month']}`\n\n"
        "اختر أحد الخيارات للتحكم:"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kbd), parse_mode=ParseMode.MARKDOWN)

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if update.effective_user.id not in ADMIN_IDS:
        logger.warning("Rejected admin callback from user_id=%s", update.effective_user.id)
        return

    if data == "adm_main":
        stats = db_get_stats()
        kbd = [
            [InlineKeyboardButton("📊 الإحصائيات التفصيلية", callback_data="adm_stats"),
             InlineKeyboardButton("💾 نسخة احتياطية فورية", callback_data="adm_backup")],
            [InlineKeyboardButton("📋 طلبات الدفع المعلقة", callback_data="adm_pending")],
            [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="adm_ban_menu"),
             InlineKeyboardButton("🔓 فك حظر", callback_data="adm_unban_menu")],
            [InlineKeyboardButton("❌ إغلاق", callback_data="end")]
        ]
        await query.edit_message_text(
            f"🛡 *لوحة التحكم*\n\n"
            f"👥 المستخدمين: `{stats['total']}`\n"
            f"👑 نشطون: `{stats['active']}`\n"
            f"⏳ معلقة: `{stats['pending']}`",
            reply_markup=InlineKeyboardMarkup(kbd),
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "adm_stats":
        stats = db_get_stats()
        text = (
            f"📊 *إحصائيات البوت التفصيلية:*\n\n"
            f"👥 إجمالي المستخدمين: `{stats['total']}`\n"
            f"👑 المشتركون VIP: `{stats['active']}`\n"
            f"⏳ طلبات معلقة: `{stats['pending']}`\n\n"
            f"📅 مستخدمون جدد اليوم: `{stats['new_today']}`\n"
            f"📅 مستخدمون جدد هذا الأسبوع: `{stats['new_week']}`\n"
            f"📅 مستخدمون جدد هذا الشهر: `{stats['new_month']}`"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")]]),
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "adm_backup":
        await backup_database(context)
        await query.answer("✅ تم إرسال النسخة الاحتياطية لقناة الأرشيف!")

    elif data == "adm_pending":
        pending = db_get_pending_payments(10)

        if not pending:
            await query.answer("لا توجد طلبات معلقة حالياً.")
            return

        text = "📋 *آخر الطلبات المعلقة:*\n\n"
        for p in pending:
            text += (
                f"👤 {escape_markdown(p['first_name'] or '')} | "
                f"💎 {escape_markdown(p['sub_type'] or '')} | "
                f"🔑 `{escape_markdown(p['pay_code'] or '')}`\n"
            )

        await query.answer()
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")]]),
            parse_mode=ParseMode.MARKDOWN
        )

    elif data.startswith("adm_ok_"):
        try:
            _, _, uid, pid = data.split("_", maxsplit=3)
            uid, pid = int(uid), int(pid)
        except (ValueError, TypeError):
            logger.warning("Malformed payment approval callback: %s", data)
            return
        if not db_decide_payment(pid, uid, approved=True):
            logger.info("Ignored stale or mismatched approval callback payment_id=%s", pid)
            return
        await safe_send(
            context.bot, uid,
            text=TEXT["payment_approved"],
            parse_mode=ParseMode.MARKDOWN
        )
        await query.edit_message_text(query.message.text + "\n\n🟢 تم القبول والتفعيل ✅")

    elif data.startswith("adm_no_"):
        try:
            _, _, uid, pid = data.split("_", maxsplit=3)
            uid, pid = int(uid), int(pid)
        except (ValueError, TypeError):
            logger.warning("Malformed payment rejection callback: %s", data)
            return
        if not db_decide_payment(pid, uid, approved=False):
            logger.info("Ignored stale or mismatched rejection callback payment_id=%s", pid)
            return
        await safe_send(
            context.bot, uid,
            text=TEXT["payment_rejected"]
        )
        await query.edit_message_text(query.message.text + "\n\n🔴 تم الرفض ❌")

    elif data == "adm_ban_menu":
        context.user_data["admin_action"] = "ban"
        await query.edit_message_text(
            "🚫 *حظر مستخدم*\n\nأرسل الآن `آيدي` (ID) المستخدم الذي تريد حظره:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")]]))

    elif data == "adm_unban_menu":
        context.user_data["admin_action"] = "unban"
        await query.edit_message_text(
            "🔓 *فك حظر مستخدم*\n\nأرسل الآن `آيدي` (ID) المستخدم الذي تريد فك حظره:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="adm_main")]]))
