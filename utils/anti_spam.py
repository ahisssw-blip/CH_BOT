"""Memory-bounded anti-spam tracking."""
import logging
import time
from collections import defaultdict
from telegram.ext import ContextTypes
from config import ADMIN_IDS

logger = logging.getLogger(__name__)
spam_tracker: dict = defaultdict(list)
SPAM_MAX_MESSAGES = 5
SPAM_WINDOW_SECONDS = 10
SPAM_MUTE_SECONDS = 30
spam_warned: dict = {}

def is_spamming(user_id: int) -> bool:
    """يتحقق إذا كان المستخدم يرسل رسائل بشكل مفرط"""
    if user_id in ADMIN_IDS:
        return False
    now = time.monotonic()
    # إزالة الرسائل القديمة خارج النافذة الزمنية
    spam_tracker[user_id] = [t for t in spam_tracker[user_id] if now - t < SPAM_WINDOW_SECONDS]
    spam_tracker[user_id].append(now)
    return len(spam_tracker[user_id]) > SPAM_MAX_MESSAGES

def cleanup_spam_trackers(context: ContextTypes.DEFAULT_TYPE | None = None) -> None:
    """Discard expired tracker entries so inactive users do not accumulate forever."""
    now = time.monotonic()
    expired = [
        user_id for user_id, timestamps in spam_tracker.items()
        if not timestamps or now - timestamps[-1] >= SPAM_WINDOW_SECONDS
    ]
    for user_id in expired:
        spam_tracker.pop(user_id, None)
    for user_id, warned_at in list(spam_warned.items()):
        if now - warned_at >= SPAM_MUTE_SECONDS:
            spam_warned.pop(user_id, None)
