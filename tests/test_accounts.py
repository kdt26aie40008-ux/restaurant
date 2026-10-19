"""アカウント（招待・認証コード・パスワード設定・ログイン・ロック・運営者の二段階認証）のテスト。"""

import time
from unittest import mock

from app import accounts
from app.auth import SessionStore, check_password_policy, hash_password, verify_password
from app.db import connect
from app.errors import AppError
from app.mailer import MemoryMailer
from tests.helpers import OPERATOR_PASSWORD, STORE_PASSWORD, TEST_SECRET, DbTestCase, code_from

BASE_URL = "http://localhost:8000"
NEW_PASSWORD = "brand new long passphrase"


class Clock:
    """accounts._now を進められる時計。"""

    def __init__(self):
        self.now = int(time.time())

    def __call__(self):
        return self.now


class AccountTestCase(DbTestCase):
    def setUp(self):
        super().setUp()
        self.mailer = MemoryMailer()
        self.sessions = SessionStore()
        self.clock = Clock()
        patcher = mock.patch("app.accounts._now", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def invite(self, name="C店", email="c@example.com"):
        store = accounts.invite_store(self.conn, {"name": name, "email": email}, self.mailer, BASE_URL, operator_id=1)
        return store, code_from(self.mailer.outbox[-1])

    def request_code(self, email):
        before = len(self.mailer.outbox)
        accounts.request_store_code(self.conn, email, self.mailer, BASE_URL)
        return code_from(self.mailer.outbox[-1]) if len(self.mailer.outbox) > before else None


class InviteAndPasswordSetupTest(AccountTestCase):
    def test_invited_store_sets_password_with_emailed_code_then_logs_in(self):
        store, code = self.invite()
        self.assertEqual(store["status"], "invited")
        self.assertEqual(self.mailer.outbox[-1]["to"], "c@example.com")
        self.assertIn("/account", self.mailer.outbox[-1]["body"])
        self.assertEqual(self.count("products", "store_id = ?", (store["id"],)), 36)  # サンプルメニューつき
        with self.assertRaises(AppError):
            accounts.store_login(self.conn, "c@example.com", NEW_PASSWORD)

        token = accounts.verify_store_code(self.conn, "C@Example.com ", code)  # 大文字・空白は正規化
        accounts.set_store_password(self.conn, token, NEW_PASSWORD, self.sessions)
        row = accounts.store_login(self.conn, "c@example.com", NEW_PASSWORD)
        self.assertEqual((row["id"], row["status"]), (store["id"], "active"))
        self.assertEqual(accounts.get_store(self.conn, store["id"])["status"], "active")

    def test_forgot_password_flow_for_active_store(self):
        code = self.request_code("a@example.com")
        self.assertIsNotNone(code)
        token = accounts.verify_store_code(self.conn, "a@example.com", code)
        accounts.set_store_password(self.conn, token, NEW_PASSWORD, self.sessions)
        accounts.store_login(self.conn, "a@example.com", NEW_PASSWORD)
        with self.assertRaises(AppError):
            accounts.store_login(self.conn, "a@example.com", STORE_PASSWORD)  # 古いパスワードは使えない

    def test_unknown_or_suspended_email_gets_no_mail_and_same_response(self):
        self.assertIsNone(self.request_code("nobody@example.com"))
        self.conn.execute("UPDATE stores SET status = 'suspended' WHERE id = ?", (self.other_store_id,))
        self.assertIsNone(self.request_code("b@example.com"))
        self.assertEqual(self.mailer.outbox, [])
        with self.assertRaises(AppError):
            accounts.request_store_code(self.conn, "not-an-email", self.mailer, BASE_URL)

    def test_code_requests_are_throttled(self):
        self.assertIsNotNone(self.request_code("a@example.com"))
        self.assertIsNone(self.request_code("a@example.com"))  # 60秒以内の再送は送らない
        for _ in range(4):
            self.clock.now += 61
            self.assertIsNotNone(self.request_code("a@example.com"))
        self.clock.now += 61
        self.assertIsNone(self.request_code("a@example.com"))  # 1時間に5回まで
        self.clock.now += 3600
        self.assertIsNotNone(self.request_code("a@example.com"))

    def test_only_latest_code_is_valid(self):
        old = self.request_code("a@example.com")
        self.clock.now += 61
        new = self.request_code("a@example.com")
        if old == new:
            self.skipTest("偶然同じコードが出た")
        with self.assertRaises(AppError):
            accounts.verify_store_code(self.conn, "a@example.com", old)
        accounts.verify_store_code(self.conn, "a@example.com", new)

    def test_code_is_locked_after_five_wrong_attempts(self):
        code = self.request_code("a@example.com")
        wrong = f"{(int(code) + 1) % 100000000:08d}"
        for _ in range(5):
            with self.assertRaises(AppError) as ctx:
                accounts.verify_store_code(self.conn, "a@example.com", wrong)
            self.assertEqual(ctx.exception.code, "CODE_INVALID")
        with self.assertRaises(AppError):
            accounts.verify_store_code(self.conn, "a@example.com", code)  # 正しいコードでももう使えない

    def test_code_expires(self):
        code = self.request_code("a@example.com")
        self.clock.now += accounts.STORE_CODE_TTL + 1
        with self.assertRaises(AppError):
            accounts.verify_store_code(self.conn, "a@example.com", code)

    def test_malformed_codes_are_rejected(self):
        self.request_code("a@example.com")
        for bad in ["", "123456", "1234567", "123456789", "abcdefgh", None, 12345678, "1234 678", "１２３４５６７８"]:
            with self.subTest(code=bad):
                with self.assertRaises(AppError):
                    accounts.verify_store_code(self.conn, "a@example.com", bad)

    def test_password_token_is_single_use_and_expires(self):
        token = accounts.verify_store_code(self.conn, "a@example.com", self.request_code("a@example.com"))
        accounts.set_store_password(self.conn, token, NEW_PASSWORD, self.sessions)
        with self.assertRaises(AppError) as ctx:
            accounts.set_store_password(self.conn, token, "yet another passphrase", self.sessions)
        self.assertEqual(ctx.exception.code, "TOKEN_INVALID")

        self.clock.now += 3600
        token = accounts.verify_store_code(self.conn, "a@example.com", self.request_code("a@example.com"))
        self.clock.now += accounts.PASSWORD_TOKEN_TTL + 1
        with self.assertRaises(AppError):
            accounts.set_store_password(self.conn, token, "yet another passphrase", self.sessions)
        for bad in [None, "", "x" * 500, "../../etc", 12345]:
            with self.assertRaises(AppError):
                accounts.set_store_password(self.conn, bad, NEW_PASSWORD, self.sessions)

    def test_weak_password_is_rejected_and_token_can_be_retried(self):
        token = accounts.verify_store_code(self.conn, "a@example.com", self.request_code("a@example.com"))
        for weak in ["short", "1234567890", "password123", "aaaaaaaaaaaa", "a@example.com-shop", "x" * 129, "tab\tpassword1"]:
            with self.subTest(password=weak):
                with self.assertRaises(AppError):
                    accounts.set_store_password(self.conn, token, weak, self.sessions)
        accounts.set_store_password(self.conn, token, NEW_PASSWORD, self.sessions)

    def test_setting_password_logs_out_existing_sessions(self):
        token_a = self.sessions.create("store", self.store_id, store_id=self.store_id)
        token_b = self.sessions.create("store", self.other_store_id, store_id=self.other_store_id)
        token = accounts.verify_store_code(self.conn, "a@example.com", self.request_code("a@example.com"))
        accounts.set_store_password(self.conn, token, NEW_PASSWORD, self.sessions)
        self.assertIsNone(self.sessions.get(token_a, "store"))
        self.assertIsNotNone(self.sessions.get(token_b, "store"))

    def test_codes_and_passwords_are_not_stored_in_plain_text(self):
        code = self.request_code("a@example.com")
        token = accounts.verify_store_code(self.conn, "a@example.com", code)
        accounts.set_store_password(self.conn, token, NEW_PASSWORD, self.sessions)
        self.conn.close()
        raw = b"".join(p.read_bytes() for p in self.config.db_path.parent.glob("restaurant.db*"))
        self.conn = connect(self.config.db_path, TEST_SECRET)
        for secret in [NEW_PASSWORD, STORE_PASSWORD, code, token]:
            self.assertNotIn(secret.encode(), raw, secret)
        logs = " ".join(r[0] or "" for r in self.conn.execute("SELECT detail FROM audit_logs"))
        for secret in [NEW_PASSWORD, code, token]:
            self.assertNotIn(secret, logs)

    def test_leaked_database_without_secret_key_cannot_verify_codes(self):
        code = self.request_code("a@example.com")
        stolen = connect(self.config.db_path, b"attacker-guess-key-0123456789abcd")
        with self.assertRaises(AppError):
            accounts.verify_store_code(stolen, "a@example.com", code)
        stolen.close()


class LoginTest(AccountTestCase):
    def test_login_errors_do_not_reveal_whether_account_exists(self):
        errors = []
        for email, password in [("nobody@example.com", "whatever password"), ("a@example.com", "wrong password!!"),
                                ("broken", "x"), ("a@example.com", "x" * 200), ("a@example.com", None)]:
            with self.assertRaises(AppError) as ctx:
                accounts.store_login(self.conn, email, password)
            errors.append((ctx.exception.status, ctx.exception.code, ctx.exception.message))
        self.assertEqual(len(set(errors)), 1, errors)

    def test_account_is_locked_after_repeated_failures(self):
        for _ in range(accounts.LOCK_THRESHOLD):
            with self.assertRaises(AppError):
                accounts.store_login(self.conn, "a@example.com", "wrong password!!")
        with self.assertRaises(AppError) as ctx:
            accounts.store_login(self.conn, "a@example.com", STORE_PASSWORD)  # ロック中は正しくても不可
        self.assertEqual(ctx.exception.code, "LOGIN_FAILED")
        self.assertEqual(self.count("audit_logs", "action = 'login.locked'"), 1)
        self.clock.now += accounts.LOCK_SECONDS + 1
        accounts.store_login(self.conn, "a@example.com", STORE_PASSWORD)

    def test_suspended_store_cannot_log_in(self):
        sessions = SessionStore()
        accounts.set_store_status(self.conn, self.store_id, {"status": "suspended"}, sessions, operator_id=1)
        with self.assertRaises(AppError) as ctx:
            accounts.store_login(self.conn, "a@example.com", STORE_PASSWORD)
        self.assertEqual(ctx.exception.code, "LOGIN_FAILED")  # 利用停止かどうかで応答を変えない
        accounts.set_store_status(self.conn, self.store_id, {"status": "active"}, sessions, operator_id=1)
        accounts.store_login(self.conn, "a@example.com", STORE_PASSWORD)

    def test_password_hashing(self):
        stored = hash_password("some long passphrase")
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotEqual(stored, hash_password("some long passphrase"))  # ソルトが毎回違う
        self.assertTrue(verify_password("some long passphrase", stored))
        for bad in ["some long passphrasE", "", None, "x" * 1000]:
            self.assertFalse(verify_password(bad, stored))
        for broken in ["", "md5$1$aa$bb", "pbkdf2_sha256$x$zz$yy", "a$b"]:
            self.assertFalse(verify_password("some long passphrase", broken))
        check_password_policy(NEW_PASSWORD, "a@example.com")


class OperatorTest(AccountTestCase):
    def setUp(self):
        super().setUp()
        accounts.create_or_reset_operator(self.conn, "ops@example.com", OPERATOR_PASSWORD)

    def test_two_step_login(self):
        challenge = accounts.operator_login(self.conn, "ops@example.com", OPERATOR_PASSWORD, self.mailer)
        code = code_from(self.mailer.outbox[-1])
        self.assertEqual(self.mailer.outbox[-1]["to"], "ops@example.com")
        operator = accounts.operator_verify(self.conn, challenge, code)
        self.assertEqual(operator["email"], "ops@example.com")
        with self.assertRaises(AppError):
            accounts.operator_verify(self.conn, challenge, code)  # 1回しか使えない

    def test_wrong_password_sends_no_code(self):
        with self.assertRaises(AppError):
            accounts.operator_login(self.conn, "ops@example.com", "wrong password!!!", self.mailer)
        with self.assertRaises(AppError):
            accounts.operator_login(self.conn, "nobody@example.com", OPERATOR_PASSWORD, self.mailer)
        self.assertEqual(self.mailer.outbox, [])

    def test_code_attempts_and_expiry(self):
        challenge = accounts.operator_login(self.conn, "ops@example.com", OPERATOR_PASSWORD, self.mailer)
        code = code_from(self.mailer.outbox[-1])
        wrong = f"{(int(code) + 1) % 100000000:08d}"
        for _ in range(accounts.MAX_CODE_ATTEMPTS):
            with self.assertRaises(AppError):
                accounts.operator_verify(self.conn, challenge, wrong)
        with self.assertRaises(AppError):
            accounts.operator_verify(self.conn, challenge, code)

        challenge = accounts.operator_login(self.conn, "ops@example.com", OPERATOR_PASSWORD, self.mailer)
        code = code_from(self.mailer.outbox[-1])
        self.clock.now += accounts.OPERATOR_CODE_TTL + 1
        with self.assertRaises(AppError):
            accounts.operator_verify(self.conn, challenge, code)
        with self.assertRaises(AppError):
            accounts.operator_verify(self.conn, "not-a-real-challenge-token-xxxxx", code)

    def test_operator_password_must_be_long(self):
        with self.assertRaises(AppError):
            accounts.create_or_reset_operator(self.conn, "ops2@example.com", "elevenchars")

    def test_store_management(self):
        store, _ = self.invite("D店", "d@example.com")
        with self.assertRaises(AppError) as ctx:
            self.invite("E店", "D@example.com")  # 大文字小文字違いも同じアドレス
        self.assertEqual(ctx.exception.code, "EMAIL_IN_USE")
        listed = {s["id"]: s for s in accounts.list_stores(self.conn)}
        self.assertEqual(set(listed), {self.store_id, self.other_store_id, store["id"]})
        self.assertNotIn("password_hash", listed[self.store_id])
        resent = accounts.resend_invite(self.conn, store["id"], {}, self.mailer, BASE_URL, 1)
        self.assertEqual(resent["status"], "invited")
        reset = accounts.resend_invite(self.conn, self.store_id, {}, self.mailer, BASE_URL, 1)  # 利用中なら再設定メール
        self.assertEqual((reset["status"], self.mailer.outbox[-1]["to"]), ("active", "a@example.com"))
        accounts.set_store_status(self.conn, self.store_id, {"status": "suspended"}, self.sessions, 1)
        with self.assertRaises(AppError) as ctx:
            accounts.resend_invite(self.conn, self.store_id, {}, self.mailer, BASE_URL, 1)
        self.assertEqual(ctx.exception.code, "STORE_SUSPENDED")
