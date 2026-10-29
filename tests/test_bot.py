import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

# Use a disposable path before importing modules that load configuration.
os.environ.setdefault("BOT_DATABASE_PATH", str(Path(tempfile.gettempdir()) / "unused-bot-test.db"))

import database
import main


class FakeUser:
    def __init__(self, user_id=1001, first_name="Test_User", username="tester"):
        self.id = user_id
        self.first_name = first_name
        self.username = username
        self.last_name = ""


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return FakeStatusMessage()


class FakeStatusMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeBot:
    def __init__(self, forbidden_ids=()):
        self.sent = []
        self.forbidden_ids = set(forbidden_ids)

    async def send_message(self, chat_id, **kwargs):
        self.sent.append((chat_id, kwargs))
        if chat_id in self.forbidden_ids:
            from telegram.error import Forbidden
            raise Forbidden("bot was blocked by the user")
        return object()


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answers = []
        self.edits = []
        self.message = SimpleNamespace(text="admin payment request")

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class BotIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="telegram-bot-test-")
        database.close_db()
        database.DB_PATH = Path(self.temp_dir.name) / "database.db"
        database.init_db()
        main.ADMIN_IDS[:] = [777]
        main.spam_tracker.clear()
        main.spam_warned.clear()

    def tearDown(self):
        database.close_db()
        self.temp_dir.cleanup()

    def test_01_fresh_schema_matches_advanced_schema(self):
        conn = database.get_db()
        expected = {
            "payments": {"phone", "updated_at", "pay_code"},
            "messages_log": {"msg_type"},
            "broadcast_log": {"targets_json", "last_index", "status"},
            "users": {"is_blocked", "is_banned"},
        }
        for table, required in expected.items():
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            self.assertTrue(required <= actual, f"{table} missing {required - actual}")

    def test_02_legacy_schema_migrates_without_data_loss(self):
        database.close_db()
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        conn = sqlite3.connect(legacy_path)
        conn.executescript("""
            CREATE TABLE users(user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
              phone TEXT, points INTEGER DEFAULT 0, referred_by INTEGER, join_date TEXT, last_seen TEXT,
              is_banned INTEGER DEFAULT 0, sub_status TEXT DEFAULT 'none', sub_type TEXT DEFAULT '');
            CREATE TABLE payments(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, sub_type TEXT,
              pay_method TEXT, pay_code TEXT, status TEXT DEFAULT 'pending', created_at TEXT);
            CREATE TABLE messages_log(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
              first_name TEXT, message TEXT, created_at TEXT);
            INSERT INTO users(user_id,first_name) VALUES (456,'Existing');
        """)
        conn.commit()
        conn.close()
        database.DB_PATH = legacy_path
        database.init_db()
        row = database.get_db().execute("SELECT first_name,is_blocked FROM users WHERE user_id=456").fetchone()
        self.assertEqual(row["first_name"], "Existing")
        self.assertEqual(row["is_blocked"], 0)
        self.assertIn("updated_at", {r[1] for r in database.get_db().execute("PRAGMA table_info(payments)")})

    def test_03_payment_txid_is_inserted_once_case_insensitively(self):
        database.db_upsert_user(FakeUser())
        first_id, duplicate = database.db_insert_payment_if_new(1001, "VIP", "usdt", "TxID-ABC")
        second_id, duplicate_again = database.db_insert_payment_if_new(2002, "VIP", "usdt", "txid-abc")
        self.assertIsNotNone(first_id)
        self.assertFalse(duplicate)
        self.assertIsNone(second_id)
        self.assertTrue(duplicate_again)
        count = database.get_db().execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        self.assertEqual(count, 1)

    def test_04_payment_decision_is_atomic_and_only_pending_once(self):
        database.db_upsert_user(FakeUser())
        payment_id, _ = database.db_insert_payment_if_new(1001, "VIP", "usdt", "unique-001")
        self.assertFalse(database.db_decide_payment(payment_id, 2002, approved=True))
        self.assertTrue(database.db_decide_payment(payment_id, 1001, approved=True))
        self.assertFalse(database.db_decide_payment(payment_id, 1001, approved=False))
        row = database.get_db().execute(
            "SELECT status FROM payments WHERE id=?", (payment_id,)
        ).fetchone()
        user = database.get_db().execute(
            "SELECT sub_status FROM users WHERE user_id=1001"
        ).fetchone()
        self.assertEqual(row["status"], "approved")
        self.assertEqual(user["sub_status"], "active")

    def test_05_non_admin_cannot_trigger_admin_callback(self):
        database.db_upsert_user(FakeUser())
        payment_id, _ = database.db_insert_payment_if_new(1001, "VIP", "usdt", "admin-check")
        query = FakeQuery(f"adm_ok_1001_{payment_id}")
        update = SimpleNamespace(callback_query=query, effective_user=FakeUser(9000))
        context = SimpleNamespace(user_data={}, bot=FakeBot())
        asyncio.run(main.handle_callback(update, context))
        status = database.get_db().execute(
            "SELECT status FROM payments WHERE id=?", (payment_id,)
        ).fetchone()[0]
        self.assertEqual(status, "pending")
        self.assertEqual(len(query.answers), 1)

    def test_06_payment_input_escapes_markdown_and_duplicate_sends_no_admin_alert(self):
        user = FakeUser(1001, "Name_[x]")
        bot = FakeBot()
        context = SimpleNamespace(user_data={
            "waiting_code": True, "pay_method": "usdt", "sub_type": "VIP"
        }, bot=bot)
        first_message = FakeMessage("https://example.test/tx?id=1&tag=*x*")
        update = SimpleNamespace(effective_user=user, message=first_message)
        asyncio.run(main.handle_text(update, context))
        self.assertEqual(len(bot.sent), 1)
        self.assertIn(r"\*x\*", bot.sent[0][1]["text"])
        self.assertEqual(len(first_message.replies), 1)
        duplicate_message = FakeMessage("https://example.test/tx?id=1&tag=*x*")
        update.message = duplicate_message
        asyncio.run(main.handle_text(update, context))
        self.assertEqual(len(bot.sent), 1)
        self.assertFalse(context.user_data["waiting_code"])
        self.assertEqual(database.get_db().execute("SELECT COUNT(*) FROM payments").fetchone()[0], 1)

    def test_07_overlong_txid_is_not_persisted_or_forwarded(self):
        bot = FakeBot()
        context = SimpleNamespace(user_data={"waiting_code": True}, bot=bot)
        message = FakeMessage("x" * (database.MAX_TXID_LENGTH + 1))
        update = SimpleNamespace(effective_user=FakeUser(), message=message)
        asyncio.run(main.handle_text(update, context))
        self.assertEqual(bot.sent, [])
        self.assertEqual(database.get_db().execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)
        self.assertTrue(context.user_data["waiting_code"])

    def test_08_broadcast_progress_resumes_and_marks_blocked_user(self):
        database.db_upsert_user(FakeUser(1001))
        database.db_upsert_user(FakeUser(1002))
        targets = [1001, 1002]
        broadcast_id = database.db_create_broadcast(777, "hello *users*", targets)
        bot = FakeBot(forbidden_ids={1002})
        status_message = FakeStatusMessage()
        asyncio.run(main.run_broadcast(bot, broadcast_id, "hello *users*", targets, 0, 0, 0, status_message))
        record = database.get_db().execute(
            "SELECT last_index,sent_count,fail_count,status FROM broadcast_log WHERE id=?",
            (broadcast_id,),
        ).fetchone()
        blocked = database.get_db().execute(
            "SELECT is_blocked FROM users WHERE user_id=1002"
        ).fetchone()[0]
        self.assertEqual(tuple(record), (2, 1, 1, "completed"))
        self.assertEqual(blocked, 1)
        self.assertIn(r"\*users\*", bot.sent[0][1]["text"])
        self.assertEqual(len(status_message.edits), 1)

    def test_09_welcome_copy_is_preserved_exactly(self):
        expected = (
            "✨ *أهـلاً و سـهـلاً Alice!* ✨\n\n"
            "📺 *بـعـض قـنـواتـنـا الـعـامـة الـمـجـانـيـة* 👇\n\n"
            "🔴 *هـذه الـقـائـمـة مـتـجـددة بـشـكـل شـبـه يـومـي 🔥* \n\n"
            "⛔️ *مـلاحـظـة هـامـة جـداً:*اي تواصل مع اي طرف خارج نطاق هذا البوت وخارج طرق التواصل التي تُذكر ضمن القنوات التي في الأسفل، ليس لنا أي علاقة بها، ولسنا مسؤولين عنها. \n*وجب التنويه♥️* \n\n"
            "🔴 سنقوم بالنشر يومياً على إحدى هذه القنوات , يمكنك الإشتراك بهم حميعاً 🔥\n\n"
            "_اضغط على أي قناة للانتقال إليها مباشرة_\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "👇 للاشتراك بالقنوات الخاصة اضغط الزر أدناه"
        )
        self.assertEqual(main.TEXT["welcome"].format(first_name="Alice"), expected)

    def test_10_channel_buttons_keep_three_column_order_and_private_button_last(self):
        markup = main.build_channels_keyboard()
        rows = markup.inline_keyboard
        self.assertEqual([len(row) for row in rows[:-2]], [3] * (len(rows) - 2 - 1) + [1])
        self.assertEqual(rows[-2][0].callback_data, "noop")
        self.assertEqual(rows[-1][0].callback_data, "private_sub")

    def test_11_start_command_renders_welcome_and_keyboard(self):
        user = FakeUser(1101, "Alice")
        message = FakeMessage()
        update = SimpleNamespace(effective_user=user, message=message)
        asyncio.run(main.cmd_start(update, SimpleNamespace(user_data={})))
        self.assertEqual(message.replies[0][0], main.TEXT["welcome"].format(first_name="Alice"))
        self.assertIsNotNone(message.replies[0][1]["reply_markup"])
        self.assertEqual(message.replies[0][1]["parse_mode"], main.ParseMode.MARKDOWN)

    def test_12_help_command_returns_the_existing_user_copy(self):
        message = FakeMessage()
        update = SimpleNamespace(effective_user=FakeUser(1201), message=message)
        asyncio.run(main.cmd_help(update, SimpleNamespace(user_data={})))
        self.assertEqual(
            message.replies[0][0],
            "👋 *مرحباً بك في البوت الخاص بنا.*\n\n"
            "يمكنك من خلال هذا البوت الإنضمام لقنواتنا المجانية و الإشتراك بالقنوات الخاصة.\n"
            "للتواصل معنا بشكل مباشر .. اضغط هنا  @xSyrb .",
        )

    def test_13_admin_approval_callback_notifies_user_and_replay_is_ignored(self):
        database.db_upsert_user(FakeUser(1301))
        payment_id, _ = database.db_insert_payment_if_new(1301, "VIP", "usdt", "approve-once")
        bot = FakeBot()
        context = SimpleNamespace(user_data={}, bot=bot)
        update = SimpleNamespace(
            effective_user=FakeUser(777),
            callback_query=FakeQuery(f"adm_ok_1301_{payment_id}"),
        )
        asyncio.run(main.handle_callback(update, context))
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(update.callback_query.edits, [("admin payment request\n\n🟢 تم القبول والتفعيل ✅", {})])
        replay = FakeQuery(f"adm_ok_1301_{payment_id}")
        update.callback_query = replay
        asyncio.run(main.handle_callback(update, context))
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(replay.edits, [])

    def test_14_support_and_end_callbacks_remain_functional(self):
        context = SimpleNamespace(user_data={}, bot=FakeBot())
        update = SimpleNamespace(effective_user=FakeUser(1401))
        support = FakeQuery("support")
        update.callback_query = support
        asyncio.run(main.handle_callback(update, context))
        self.assertEqual(support.edits[0][0], "📞 تواصل معنا مباشرة عبر الواتساب:")
        self.assertTrue(all(button.url.startswith("https://wa.me/") for row in support.edits[0][1]["reply_markup"].inline_keyboard for button in row if button.url))
        end = FakeQuery("end")
        update.callback_query = end
        asyncio.run(main.handle_callback(update, context))
        self.assertEqual(end.edits[0][0], "👋 تم إغلاق الجلسة. شكراً لاستخدامك البوت!")

    def test_15_regular_link_message_is_escaped_before_admin_forwarding(self):
        bot = FakeBot()
        message = FakeMessage("[click](https://example.test/a_b) and *bold*")
        update = SimpleNamespace(effective_user=FakeUser(1501), message=message)
        context = SimpleNamespace(user_data={}, bot=bot)
        asyncio.run(main.handle_text(update, context))
        self.assertEqual(len(bot.sent), 1)
        forwarded = bot.sent[0][1]["text"]
        self.assertIn(r"\[click]", forwarded)
        self.assertIn(r"\*bold\*", forwarded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
