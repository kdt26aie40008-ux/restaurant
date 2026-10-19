import http.client
import json
import logging
import re
import secrets
import shutil
import tempfile
import threading
import unittest

from app import auth
from app.config import Config
from app.db import connect, init_database, transaction
from app.mailer import MemoryMailer
from app.seed import seed_store
from app.server import OrderServer
from app.timeutil import now_iso

logging.disable(logging.CRITICAL)
# テストを速くするため、ハッシュの反復回数だけ下げる（照合は保存された回数で行うので仕組みは同じ）。
auth.PBKDF2_ITERATIONS = 1000

TEST_SECRET = b"test-secret-key-0123456789abcdef"
STORE_PASSWORD = "correct horse battery staple"
OTHER_PASSWORD = "another long passphrase 42"
OPERATOR_PASSWORD = "operator long passphrase 2026"


def request_key():
    return secrets.token_hex(16)


def order_payload(items, party_size=2, key=None, expected_total=None):
    payload = {
        "request_key": key or request_key(),
        "party_size": party_size,
        "items": [{"product_id": pid, "quantity": qty} for pid, qty in items],
    }
    if expected_total is not None:
        payload["expected_total"] = expected_total
    return payload


def create_store(conn, name, email, password=None, status="active"):
    ts = now_iso()
    with transaction(conn):
        store_id = conn.execute(
            "INSERT INTO stores (name, email, password_hash, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, email, auth.hash_password(password) if password else None, status, ts, ts),
        ).lastrowid
        seed_store(conn, store_id)
    return store_id


def code_from(mail):
    return re.search(r"認証コード: (\d{8})", mail["body"]).group(1)


class DbTestCase(unittest.TestCase):
    """テストごとに一時フォルダへ新しい DB を作り、2つの店舗（A・B）を登録する。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="restaurant-test-")
        self.config = Config(data_dir=self.tmp, host="127.0.0.1", port=0)
        self.config.secret_key_env = TEST_SECRET.decode()
        self.config.ensure_dirs()
        init_database(self.config.db_path)
        self.conn = connect(self.config.db_path, TEST_SECRET)
        self.store_id = create_store(self.conn, "A店", "a@example.com", STORE_PASSWORD)
        self.other_store_id = create_store(self.conn, "B店", "b@example.com", OTHER_PASSWORD)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def count(self, table, where="1=1", params=()):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]


class ServerTestCase(DbTestCase):
    """実際に HTTP サーバーを別スレッドで起動して API を叩く。"""

    def setUp(self):
        super().setUp()
        self.mailer = MemoryMailer()
        self.server = OrderServer(self.config, mailer=self.mailer)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = self.store_login("a@example.com", STORE_PASSWORD, admin=True)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, method, path, body=None, headers=None, raw=False, csrf=True, cookie=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        all_headers = {"X-Requested-With": "fetch"} if csrf else {}
        if cookie is True and getattr(self, "cookie", None):
            all_headers["Cookie"] = self.cookie
        elif isinstance(cookie, str):
            all_headers["Cookie"] = cookie
        all_headers.update(headers or {})
        data = None
        if body is not None:
            if isinstance(body, (bytes, bytearray)):
                data = bytes(body)
            else:
                data = json.dumps(body).encode("utf-8")
                all_headers.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=all_headers)
        response = conn.getresponse()
        content = response.read()
        result_headers = dict(response.getheaders())
        result_headers["_set_cookies"] = response.headers.get_all("Set-Cookie") or []
        conn.close()
        if raw:
            return response.status, content, result_headers
        parsed = json.loads(content.decode("utf-8")) if content and "json" in result_headers.get("Content-Type", "") else None
        return response.status, parsed, result_headers

    def store_login(self, email, password, admin=False):
        status, data, headers = self.request("POST", "/api/auth/store/login",
                                             {"email": email, "password": password, "admin": admin}, cookie=False)
        self.assertEqual(status, 200, data)
        return headers["_set_cookies"][0].split(";")[0]

    def operator_cookie(self, email="ops@example.com"):
        from app import accounts

        accounts.create_or_reset_operator(self.conn, email, OPERATOR_PASSWORD)
        status, data, _ = self.request("POST", "/api/auth/operator/login", {"email": email, "password": OPERATOR_PASSWORD}, cookie=False)
        self.assertEqual(status, 200, data)
        code = code_from(self.mailer.outbox[-1])
        status, _, headers = self.request("POST", "/api/auth/operator/verify", {"challenge": data["challenge"], "code": code}, cookie=False)
        self.assertEqual(status, 200)
        return headers["_set_cookies"][0].split(";")[0]
