"""User-facing command, message, and callback handlers."""
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from config import (
    ADMIN_IDS, SUPPORT, SUBS, TELEGRAM_CONTACT, WALLETS, BTNS,
    load_single_private_channels,
)
from database import (
    db_upsert_user, is_user_banned, db_set_banned,
    db_broadcast_targets, db_create_broadcast,
)
from handlers.admin import handle_admin_callback
from handlers.common import build_channels_keyboard, run_broadcast, safe_send
from handlers.payment import process_payment_submission
from locales import TEXT
from utils.anti_spam import is_spamming, spam_warned, SPAM_MUTE_SECONDS

logger = logging.getLogger(__name__)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data["waiting_code"] = False
    context.user_data.pop("pay_method", None)
    context.user_data.pop("sub_type", None)
    db_upsert_user(user)

    if is_user_banned(user.id):
        await update.message.reply_text(TEXT["banned_notice"])
        return

    # ─── الشاشة الرئيسية: ترحيب + قنوات عامة فورية ───
    welcome_text = TEXT["welcome"].format(first_name=escape_markdown(user.first_name or ""))
    await update.message.reply_text(
        welcome_text,
        reply_markup=build_channels_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data["waiting_code"] = False
    context.user_data.pop("pay_method", None)
    context.user_data.pop("sub_type", None)
    if user.id in ADMIN_IDS:
        help_text = (
            "🛡 *أوامر الأدمن:*\n\n"
            "ـ `/admin` - لوحة التحكم الشاملة\n"
            "`بث [نص]` - رسالة جماعية لكل المستخدمين\n"
            "`رد [آيدي] [نص]` - رد مباشر على مستخدم"
        )
    else:
        help_text = (
            "👋 مرحباً بك في *الـبـوت الـرسـمـي الأول* الخاص بنا.\n\n\n"
            "● يمكنك من خلال هذا البوت الإنضمام لقنواتنا المجانية بسهولة.\n\n"
            "● ويمكنك أيضاً الإشتراك بقنواتنا الخاصة المدفوعة.\n\n"
            "‏▬▬▬▬▬▬▬▬▬▬▬‏▬‏▬\n\n"
            "🟢 *للتواصل معنا بشكل مباشر:* 👇🏼👇🏼\n\n"
            "● @VNBNV . \n\n"
            "🔰"
        )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def show_private_sub_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة الاشتراك بالقنوات الخاصة مع أزرار الدفع"""
    sub = SUBS.get("VIP", {})
    price_usd = sub.get("price_usd", 50)
    label = sub.get("label", "👑 اشتراك VIP المميز 👑")

    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("🪙  الدفع عن طريق USDT  🪙", callback_data="meth_usdt")],
        [InlineKeyboardButton("📝  الــتــفــاصــيــل  📝", callback_data="sub_details")],
        [InlineKeyboardButton("💬  تواصل معنا لأي طريقة دفع أخرى  💬", url=TELEGRAM_CONTACT)],
        [InlineKeyboardButton(BTNS.get("btn_back", "🔙 رجــوع"), callback_data="back_to_start")]
    ])

    text = (
        f"💎 *{label}*\n\n"
        f"💰 السعر: *{price_usd}$*\n\n"
        "● الإشتراك الآلي عن طريق البوت ، متوفر حالياً فقط عن طريق العملات الرقمية USDT.\n\n\n"
        "‏▬▬▬▬▬▬▬▬▬▬▬‏▬‏▬\n\n"
        "*‏🔴 للإشتراك والدفع بأي طريقة أُخرى ، تواصل معنا.*\n\n"
        "🌍 *متوفر جميع طرق الدفع من كل أنحاء العالم.* 🌎\n\n"
        "✅"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)

async def show_single_private_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض شرح وروابط القنوات الخاصة المحددة لهذا الخيار."""
    channels = load_single_private_channels()
    keyboard = []

    row = []
    for channel in channels:
        row.append(
            InlineKeyboardButton(
                channel["name"],
                url=channel["url"]
            )
        )

        if len(row) == 3:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    # زر مختارات/نخبة بعرض سطر مستقل
    keyboard.append([
        InlineKeyboardButton(
            "🔥مختارات/نخبة🔥",
            url="https://t.me/+7Ho7TCzT0icyZGU0"
        )
    ])

    # زر الرجوع في سطر مستقل
    keyboard.append([
        InlineKeyboardButton("🔙 رجوع", callback_data="back_to_start")
    ])


    text = TEXT["single_private_intro"]

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = TEXT["main_menu"].format(first_name=escape_markdown(user.first_name or ""))
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=build_channels_keyboard(), parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            text, reply_markup=build_channels_keyboard(), parse_mode=ParseMode.MARKDOWN
        )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = update.effective_user

    context.user_data["waiting_code"] = False
    context.user_data.pop("pay_method", None)
    context.user_data.pop("sub_type", None)

    if is_user_banned(user.id):
        await query.answer("عذراً .. حاول في وقت آخر.", show_alert=True)
        return
    if data not in ("adm_backup", "adm_pending") or user.id not in ADMIN_IDS:
        await query.answer()

    # زر وهمي (فاصل)
    if data == "noop":
        return

    if data in ("main_menu", "back_to_start"):
        await show_main_menu(update, context)

    elif data == "private_sub":
        await show_private_sub_menu(update, context)

    elif data == "single_private_sub":
        await show_single_private_sub(update, context)

    elif data == "sub_details":
        details = SUBS.get("VIP", {}).get("details", "اشتراك VIP يمنحك الوصول لكافة القنوات الخاصة بشكل دائم.")
        kbd = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="private_sub")]])
        await query.edit_message_text(details, reply_markup=kbd, parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("meth_"):
        method = data.replace("meth_", "")
        context.user_data["pay_method"] = method
        context.user_data["sub_type"] = "VIP"

        back_kbd = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="private_sub")]])

        if method == "usdt":
            await query.edit_message_text(
                f"🪙 *USDT*\n\n"
                f"قم بتحويل {SUBS.get('VIP', {}).get('price_usd', 15)}$ إلى أحد المحافظ التالية:\n\n"
                f"BEP20:\n`{WALLETS.get('usdt_bep20')}`\n\n"
                f"TRC20:\n`{WALLETS.get('usdt_trc20')}`\n\n"
                "يمكنك التواصل مع الدعم للتحويل المباشر (خارج السلسلة) إلى بينانس أو كوين اكس أو تراست والت أو سي والت.\n"
                "بالإضافة إلى شبكات: Erc20 ETH - TON.\n\n"
                "*ثم أرسل TxID هنا 👇*",
                reply_markup=back_kbd, parse_mode=ParseMode.MARKDOWN
            )
            context.user_data["waiting_code"] = True

    elif data == "support":
        kbd = [
            [InlineKeyboardButton(f"💬 {SUPPORT.get('label1')} - واتساب", url=f"https://wa.me/{SUPPORT.get('whatsapp1')}")],
            [InlineKeyboardButton(f"💬 {SUPPORT.get('label2')} - واتساب", url=f"https://wa.me/{SUPPORT.get('whatsapp2')}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
        await query.edit_message_text(
            "📞 تواصل معنا مباشرة عبر الواتساب:",
            reply_markup=InlineKeyboardMarkup(kbd)
        )

    elif data == "end":
        await query.edit_message_text("👋 تم إغلاق الجلسة. شكراً لاستخدامك البوت!")

    elif data.startswith("adm_"):
        if user.id not in ADMIN_IDS:
            logger.warning("Rejected admin callback from user_id=%s", user.id)
            return
        await handle_admin_callback(update, context)
    
    elif data.startswith("reply_to_"):
        if user.id not in ADMIN_IDS:
            logger.warning("Rejected reply callback from user_id=%s", user.id)
            return
        user_id = data.split("_")[2]
        context.user_data["replying_to_user"] = user_id
        await query.message.reply_text(f"✍️ أرسل الآن ردك على المستخدم ({user_id}):")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    if not isinstance(text, str):
        return

    # --- ميزة الرد الذكي للأدمن ---
    if user.id in ADMIN_IDS and context.user_data.get("replying_to_user"):
        target_id = context.user_data.get("replying_to_user")
        try:
            escaped_text = escape_markdown(text[:3500])
            await context.bot.send_message(chat_id=int(target_id), text=f"💬 *رسالة من الإدارة:*\n\n{escaped_text}", parse_mode=ParseMode.MARKDOWN)
            await update.message.reply_text("✅ تم إرسال ردك بنجاح.")
        except (TelegramError, ValueError, TypeError) as e:
            logger.exception("Admin reply failed for target=%s", target_id)
            await update.message.reply_text(f"❌ فشل الإرسال: {e}")
        context.user_data["replying_to_user"] = None
        return

    if is_user_banned(user.id):
        return  # تجاهل رسائل المستخدم المحظور بصمت

    # ─── فحص السبام ───
    if user.id not in ADMIN_IDS:
        if is_spamming(user.id):
            now_ts = time.monotonic()
            last_warn = spam_warned.get(user.id, 0)
            # إرسال تحذير مرة واحدة فقط خلال فترة التجاهل
            if now_ts - last_warn > SPAM_MUTE_SECONDS:
                spam_warned[user.id] = now_ts
                await update.message.reply_text(
                    "⚠️ *أنت ترسل رسائل بشكل سريع جداً!*\n\n"
                    f"يرجى الانتظار {SPAM_MUTE_SECONDS} ثانية قبل الإرسال مجدداً.",
                    parse_mode=ParseMode.MARKDOWN
                )
            return  # تجاهل الرسالة

    # تسجيل الرسائل من غير الأدمن
    if user.id not in ADMIN_IDS and not context.user_data.get("waiting_code"):
        # db_log_message(user.id, user.username, user.first_name, text) # تم التهميش للسرعة
        for aid in ADMIN_IDS:
            # إضافة زر رد سهل بدلاً من الطريقة القديمة
            kbd = InlineKeyboardMarkup([[InlineKeyboardButton("📝 الرد على المستخدم", callback_data=f"reply_to_{user.id}")]])
            try:
                safe_user_text = escape_markdown(text[:3500])
                safe_first_name = escape_markdown(user.first_name or "")
                await context.bot.send_message(
                    chat_id=aid,
                    text=f"👁 *رسالة من:* {safe_first_name} ({user.id})\n💬 {safe_user_text}",
                    reply_markup=kbd,
                    parse_mode=ParseMode.MARKDOWN
                )
            except TelegramError:
                logger.exception("Unable to forward message from user_id=%s to admin_id=%s", user.id, aid)

    if await process_payment_submission(update, context, user, text):
        return

    # أوامر الأدمن النصية
    if user.id in ADMIN_IDS:
        admin_action = context.user_data.get("admin_action")
        if admin_action in ["ban", "unban"]:
            try:
                target_id = int(text.strip())
                db_set_banned(target_id, admin_action == "ban")
                action_text = "حظر" if admin_action == "ban" else "فك حظر"
                await update.message.reply_text(
                    f"✅ تم {action_text} المستخدم `{target_id}` بنجاح.",
                    parse_mode=ParseMode.MARKDOWN
                )
                context.user_data["admin_action"] = None
            except ValueError:
                await update.message.reply_text("❌ يرجى إرسال آيدي (رقم) صحيح.")
            except Exception:
                logger.exception("Unable to update ban status for target_id=%s", text)
            return

        if text.startswith("بث "):
            msg = text[len("بث "):].strip()
            users = db_broadcast_targets()
            broadcast_id = db_create_broadcast(user.id, msg, users)
            status_msg = await update.message.reply_text(f"⏳ جاري الإرسال لـ {len(users)} مستخدم...")
            await run_broadcast(context.bot, broadcast_id, msg, users, 0, 0, 0, status_msg)

        elif text.startswith("رد "):
            parts = text.split(maxsplit=2)
            if len(parts) == 3:
                try:
                    target_id = int(parts[1])
                except ValueError:
                    logger.warning("Ignored admin reply with invalid target id: %s", parts[1])
                    return
                response = parts[2]
                if await safe_send(
                    context.bot, target_id,
                    text=f"💬 *رد من الإدارة:*\n\n{escape_markdown(response[:3500])}",
                    parse_mode=ParseMode.MARKDOWN
                ):
                    await update.message.reply_text(f"✅ تم إرسال الرد إلى {target_id}")
                else:
                    await update.message.reply_text("❌ فشل إرسال الرد، قد يكون المستخدم حظر البوت.")
