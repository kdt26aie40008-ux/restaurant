"""セキュリティ点検で見つかった問題の再発防止テスト。"""

import socket
import threading
import time
from unittest import mock

from app import accounts, auth
from app.backup import check_backup_dir_allowed
from app.db import connect
from app.errors import AppError
from app.server import Handler
from tests.helpers import (
    OPERATOR_PASSWORD, OTHER_PASSWORD, STORE_PASSWORD, TEST_SECRET, DbTestCase, ServerTestCase, code_from, order_payload,
)


class ConcurrentLockoutTest(DbTestCase):
    def test_parallel_guesses_cannot_exceed_lock_threshold(self):
        """同時に大量のパスワードを送っても、照合されるのは LOCK_THRESHOLD 回まで。"""
        calls = []
        real_verify = auth.verify_password

        def slow_verify(password, stored):
            calls.append(password)
            time.sleep(0.2)  # 照合中に他のリクエストが割り込める状況を作る
            return real_verify(password, stored)

        results = []

        def attempt(password):
            conn = connect(self.config.db_path, TEST_SECRET)
            try:
                accounts.store_login(conn, "a@example.com", password)
                results.append("ok")
            except AppError:
                results.append("ng")
            finally:
                conn.close()

        with mock.patch("app.accounts.verify_password", slow_verify):
            threads = [threading.Thread(target=attempt, args=(f"wrong password {i:02d}",)) for i in range(20)]
            threads.append(threading.Thread(target=attempt, args=(STORE_PASSWORD,)))
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertLessEqual(len(calls), accounts.LOCK_THRESHOLD)
        row = self.conn.execute("SELECT locked_until FROM stores WHERE id = ?", (self.store_id,)).fetchone()
        self.assertIsNotNone(row["locked_until"])
        if "ok" in results:
            self.assertIn(STORE_PASSWORD, calls)


class CodeGuessingTest(DbTestCase):
    def test_daily_cap_on_wrong_codes(self):
        from app.mailer import MemoryMailer

        mailer = MemoryMailer()
        clock = [int(time.time())]
        with mock.patch("app.accounts._now", lambda: clock[0]):
            for _ in range(accounts.DAILY_CODE_FAILURE_LIMIT // accounts.MAX_CODE_ATTEMPTS):
                accounts.request_store_code(self.conn, "a@example.com", mailer, "http://x")
                code = code_from(mailer.outbox[-1])
                wrong = f"{(int(code) + 1) % 10**8:08d}"
                for _ in range(accounts.MAX_CODE_ATTEMPTS):
                    with self.assertRaises(AppError):
                        accounts.verify_store_code(self.conn, "a@example.com", wrong, mailer)
                clock[0] += 720
            self.assertIn("入力失敗が続いています", mailer.outbox[-1]["subject"])  # 本人へのお知らせ
            sent = len(mailer.outbox)
            accounts.request_store_code(self.conn, "a@example.com", mailer, "http://x")
            self.assertEqual(len(mailer.outbox), sent)  # 24時間は新しいコードを送らない
            clock[0] += 86400 + 1
            accounts.request_store_code(self.conn, "a@example.com", mailer, "http://x")
            self.assertEqual(len(mailer.outbox), sent + 1)
            accounts.verify_store_code(self.conn, "a@example.com", code_from(mailer.outbox[-1]))

    def test_codes_have_eight_digits(self):
        codes = {auth.new_code() for _ in range(200)}
        self.assertTrue(all(len(c) == 8 and c.isdigit() for c in codes))
        self.assertGreater(len(codes), 190)


class HttpRegressionTest(ServerTestCase):
    def test_own_login_does_not_reset_rate_limit(self):
        # setUp で1回ログイン済み。誤り8回＋自店舗ログイン1回で、合計10回目。
        for _ in range(8):
            self.request("POST", "/api/auth/store/login", {"email": "b@example.com", "password": "wrong password!!"}, cookie=False)
        self.store_login("a@example.com", STORE_PASSWORD)
        status, data, _ = self.request("POST", "/api/auth/store/login", {"email": "b@example.com", "password": "wrong password!!"}, cookie=False)
        self.assertEqual((status, data["error"]["code"]), (429, "TOO_MANY_REQUESTS"))

    def test_logged_in_store_keeps_working_while_attacker_locks_account(self):
        for _ in range(accounts.LOCK_THRESHOLD):
            self.request("POST", "/api/auth/store/login", {"email": "a@example.com", "password": "wrong password!!"}, cookie=False)
        self.assertIsNotNone(self.conn.execute("SELECT locked_until FROM stores WHERE id = ?", (self.store_id,)).fetchone()[0])
        self.assertEqual(self.request("GET", "/api/orders")[0], 200)
        status, _, _ = self.request("POST", "/api/auth/store/reauth", {"password": STORE_PASSWORD})
        self.assertEqual(status, 200)  # ログイン中の端末の再確認はロックに影響されない

    def test_wrong_reauth_logs_out_after_limit(self):
        for _ in range(auth.MAX_REAUTH_FAILURES - 1):
            status, data, _ = self.request("POST", "/api/auth/store/reauth", {"password": "wrong password!!"})
            self.assertEqual(data["error"]["code"], "REAUTH_FAILED")
        status, data, _ = self.request("POST", "/api/auth/store/reauth", {"password": "wrong password!!"})
        self.assertEqual((status, data["error"]["code"]), (401, "STORE_LOGIN_REQUIRED"))
        self.assertEqual(self.request("GET", "/api/orders")[0], 401)

    def test_password_reset_logs_in_even_when_account_is_locked(self):
        for _ in range(accounts.LOCK_THRESHOLD):
            self.request("POST", "/api/auth/store/login", {"email": "a@example.com", "password": "wrong password!!"}, cookie=False)
        self.request("POST", "/api/auth/store/request-code", {"email": "a@example.com"}, cookie=False)
        code = code_from(self.mailer.outbox[-1])
        _, data, _ = self.request("POST", "/api/auth/store/verify-code", {"email": "a@example.com", "code": code}, cookie=False)
        status, _, headers = self.request("POST", "/api/auth/store/set-password", {"token": data["token"], "password": "recovered long passphrase"}, cookie=False)
        self.assertEqual(status, 200)
        cookie = headers["_set_cookies"][0].split(";")[0]
        self.assertEqual(self.request("GET", "/api/orders", cookie=cookie)[0], 200)
        self.assertIn("パスワードが設定されました", self.mailer.outbox[-1]["subject"])  # 変更のお知らせ

    def test_password_changed_elsewhere_invalidates_sessions(self):
        operator = self.operator_cookie()
        self.conn.execute("UPDATE stores SET password_hash = ? WHERE id = ?", (auth.hash_password("changed by cli 12345"), self.store_id))
        self.assertEqual(self.request("GET", "/api/orders")[0], 401)
        accounts.create_or_reset_operator(self.conn, "ops@example.com", "operator reset passphrase 99")  # CLI での再設定
        self.assertEqual(self.request("GET", "/api/operator/stores", cookie=operator)[0], 401)

    def test_operator_site_refuses_proxied_requests(self):
        operator = self.operator_cookie()
        for header in ["X-Forwarded-For", "Forwarded", "X-Real-IP", "X-Forwarded-Host"]:
            with self.subTest(header=header):
                status, data, _ = self.request("GET", "/api/operator/stores", cookie=operator, headers={header: "203.0.113.5"})
                self.assertEqual((status, data["error"]["code"]), (403, "OPERATOR_LOCAL_ONLY"))

    def test_unknown_host_header_is_rejected(self):
        for host in ["evil.example", "evil.example:8000", "127.0.0.1.evil.example", ""]:
            with self.subTest(host=host):
                status, _, _ = self.request("GET", "/api/health", headers={"Host": host}, raw=True)
                self.assertEqual(status, 421)
        self.assertEqual(self.request("GET", "/api/health", headers={"Host": f"localhost:{self.port}"}, raw=True)[0], 200)

    def test_order_ids_do_not_reveal_other_stores_volume(self):
        other = self.store_login("b@example.com", OTHER_PASSWORD)
        for _ in range(3):
            self.request("POST", "/api/orders", order_payload([("P001", 1)]), cookie=other)
        _, data, _ = self.request("POST", "/api/orders", order_payload([("P001", 1)]))
        self.assertTrue(data["order"]["id"].endswith("-0001"))  # 他店舗の注文数に関係なく自店舗の連番
        self.assertNotIn('"id": 4', str(data))

    def test_idle_connections_are_closed(self):
        with mock.patch.object(Handler, "timeout", 1):
            sock = socket.create_connection(("127.0.0.1", self.port))
            sock.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n")  # ヘッダーを最後まで送らない
            sock.settimeout(5)
            started = time.monotonic()
            try:
                remaining = sock.recv(1024)
            except ConnectionResetError:
                remaining = b""
            self.assertEqual(remaining, b"")
            self.assertLess(time.monotonic() - started, 4)
            sock.close()

    def test_connection_limit(self):
        time.sleep(0.5)  # setUp のログイン接続が終わってから上限を差し替える
        self.server._slots = threading.BoundedSemaphore(2)
        with mock.patch.object(Handler, "timeout", 3):
            idle = [socket.create_connection(("127.0.0.1", self.port)) for _ in range(2)]
            time.sleep(0.3)
            extra = socket.create_connection(("127.0.0.1", self.port))
            extra.settimeout(2)
            extra.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            try:
                self.assertEqual(extra.recv(1024), b"")  # 上限を超えた接続はすぐ閉じられる
            except ConnectionResetError:
                pass
            for s in idle + [extra]:
                s.close()


class RecoveryTest(DbTestCase):
    def test_operator_reset_mail_unblocks_store(self):
        from app.mailer import MemoryMailer

        mailer = MemoryMailer()
        self.conn.execute("UPDATE stores SET locked_until = ? WHERE id = ?", (int(time.time()) + 900, self.store_id))
        self.conn.execute(
            "INSERT INTO verification_codes (account_type, account_id, purpose, code_hash, attempts, expires_at, consumed_at, created_at) "
            "VALUES ('store', ?, 'store_password', 'x', ?, 0, 0, ?)",
            (self.store_id, accounts.DAILY_CODE_FAILURE_LIMIT, int(time.time())),
        )
        store = accounts.resend_invite(self.conn, self.store_id, {}, mailer, "http://x", 1)
        self.assertEqual(store["status"], "active")
        self.assertEqual(mailer.outbox[-1]["to"], "a@example.com")
        token = accounts.verify_store_code(self.conn, "a@example.com", code_from(mailer.outbox[-1]))
        self.assertTrue(token)
        self.assertIsNone(self.conn.execute("SELECT locked_until FROM stores WHERE id = ?", (self.store_id,)).fetchone()[0])
        with self.assertRaises(AppError) as ctx:
            accounts.resend_invite(self.conn, self.store_id, {"email": "attacker@example.com"}, mailer, "http://x", 1)
        self.assertEqual(ctx.exception.code, "EMAIL_LOCKED")


class SlowClientTest(ServerTestCase):
    def test_trickling_client_is_cut_off_at_total_deadline(self):
        with mock.patch.object(Handler, "timeout", 2):
            sock = socket.create_connection(("127.0.0.1", self.port))
            sock.settimeout(1)
            started = time.monotonic()
            closed = False
            while time.monotonic() - started < 8:
                try:
                    sock.sendall(b"X")
                    if sock.recv(1) == b"":
                        closed = True
                        break
                except socket.timeout:
                    continue
                except OSError:
                    closed = True
                    break
            sock.close()
            self.assertTrue(closed)
            self.assertLess(time.monotonic() - started, 5)

    def test_per_client_connection_limit(self):
        from app.server import MAX_CONNECTIONS_PER_CLIENT

        with mock.patch.object(Handler, "timeout", 3):
            idle = [socket.create_connection(("127.0.0.1", self.port)) for _ in range(MAX_CONNECTIONS_PER_CLIENT)]
            time.sleep(0.3)
            extra = socket.create_connection(("127.0.0.1", self.port))
            extra.settimeout(2)
            try:
                extra.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                self.assertEqual(extra.recv(1024), b"")
            except (ConnectionResetError, ConnectionAbortedError):
                pass
            for s in idle + [extra]:
                s.close()

    def test_code_failures_are_capped_per_client(self):
        from app.server import CODE_FAILURES_PER_CLIENT_PER_DAY

        self.request("POST", "/api/auth/store/request-code", {"email": "a@example.com"}, cookie=False)
        for i in range(CODE_FAILURES_PER_CLIENT_PER_DAY):
            self.server.app.limiter.reset("store_verify_code:127.0.0.1")
            status, _, _ = self.request("POST", "/api/auth/store/verify-code", {"email": "a@example.com", "code": f"{i:08d}"}, cookie=False)
            self.assertEqual(status, 400)
        self.server.app.limiter.reset("store_verify_code:127.0.0.1")
        status, data, _ = self.request("POST", "/api/auth/store/verify-code", {"email": "a@example.com", "code": "99999999"}, cookie=False)
        self.assertEqual((status, data["error"]["code"]), (429, "TOO_MANY_REQUESTS"))


class BackupDirTest(DbTestCase):
    def test_network_paths_are_rejected(self):
        for path in ["\\\\evil\\share", "//evil/share", "\\\\?\\UNC\\evil\\share", "\\\\127.0.0.1\\c$"]:
            with self.subTest(path=path):
                with self.assertRaises(AppError):
                    check_backup_dir_allowed(path)


class NoLoginModeTest(ServerTestCase):
    def test_no_login_mode_works_only_from_this_pc(self):
        self.server.app.config.no_login = True
        status, data, _ = self.request("GET", "/api/auth/store/session", cookie=False)
        self.assertEqual((status, data["logged_in"], data["no_login"]), (200, True, True))
        self.assertEqual(self.request("GET", "/api/admin/settings", cookie=False)[0], 200)
        self.assertEqual(self.request("GET", "/api/operator/stores", cookie=False)[0], 200)
        _, created, _ = self.request("POST", "/api/orders", order_payload([("P001", 1)]), cookie=False)
        self.assertEqual(self.count("orders", "store_id = ?", (self.store_id,)), 1)  # 最初の店舗として注文される
        with mock.patch.object(Handler, "_is_local_client", return_value=False):
            status, data, _ = self.request("GET", "/api/orders", cookie=False)
            self.assertEqual((status, data["error"]["code"]), (403, "NO_LOGIN_LOCAL_ONLY"))
        status, data, _ = self.request("GET", "/api/orders", cookie=False, headers={"X-Forwarded-For": "203.0.113.5"})
        self.assertEqual(status, 403)
        self.request("POST", "/api/orders", order_payload([("P001", 1)]), csrf=False, cookie=False)
        self.assertEqual(self.count("orders"), 1)  # CSRF 対策はそのまま

    def test_no_login_mode_refuses_lan_binding(self):
        import run

        self.config.no_login = True
        self.config.host = "0.0.0.0"
        with mock.patch("run.OrderServer") as server:
            run.command_serve(self.config)
        server.assert_not_called()
