"""HTTP API・認証・セキュリティ・配信のテスト。"""

import sqlite3
import threading
from unittest import mock

from app.server import Handler
from tests.helpers import OTHER_PASSWORD, STORE_PASSWORD, ServerTestCase, code_from, order_payload

STORE_GET_ROUTES = ["/api/settings/public", "/api/categories", "/api/products", "/api/orders", "/api/orders/20261019-0001",
                    "/api/visits", "/api/visits/V20261019-001", "/api/visits/V20261019-001/bill", "/api/sales/summary"]
STORE_ADMIN_GET_ROUTES = ["/api/admin/settings", "/api/admin/categories", "/api/admin/products",
                          "/api/admin/export/sales.csv", "/api/admin/export/menu.csv", "/api/admin/audit-logs"]
OPERATOR_GET_ROUTES = ["/api/operator/stores", "/api/operator/backups", "/api/operator/settings", "/api/operator/audit-logs"]


class OrderApiTest(ServerTestCase):
    def test_menu_endpoints(self):
        status, data, _ = self.request("GET", "/api/settings/public")
        self.assertEqual((status, data), (200, {"store_name": "A店", "max_party_size": 30}))
        _, categories, _ = self.request("GET", "/api/categories")
        drink = next(c for c in categories["categories"] if c["name"] == "ドリンク")
        _, products, _ = self.request("GET", f"/api/products?category_id={drink['id']}")
        self.assertTrue(products["products"])
        self.assertTrue(all(p["category_id"] == drink["id"] for p in products["products"]))
        self.assertEqual(self.request("GET", "/api/products?category_id=abc")[0], 400)

    def test_order_flow_over_http(self):
        status, data, _ = self.request("POST", "/api/orders", order_payload([("P001", 2)], 2, expected_total=1360))
        self.assertEqual(status, 201)
        order_id = data["order"]["id"]
        visit_id = data["visit"]["id"]
        self.assertEqual(data["order"]["visit_id"], visit_id)
        status, bill, _ = self.request("GET", f"/api/visits/{visit_id}/bill")
        self.assertEqual((status, bill["visit"]["bill"]["total"], bill["visit"]["bill"]["split"]["per_person"]), (200, 1360, 680))
        _, history, _ = self.request("GET", "/api/orders")
        self.assertEqual([o["id"] for o in history["orders"]], [order_id])
        status, error, _ = self.request("POST", "/api/orders", order_payload([("P001", 1)], 0))
        self.assertEqual((status, error["error"]["code"]), (400, "PARTY_SIZE_ZERO"))
        self.assertTrue(error["error"]["message"] and error["error"]["hint"])

    @mock.patch("app.server.MAX_CONNECTIONS_PER_CLIENT", 64)  # 同じ端末からの同時接続上限（8）を、このテストでは外す
    def test_rapid_duplicate_submissions_create_one_order(self):
        payload = order_payload([("P001", 1), ("P006", 2)], 2)
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.request("POST", "/api/orders", payload)[:2])) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        statuses = sorted(status for status, _ in results)
        self.assertEqual((statuses.count(201), statuses.count(200)), (1, 9), statuses)
        self.assertEqual(len({data["order"]["id"] for _, data in results}), 1)
        self.assertEqual((self.count("orders"), self.count("order_items")), (1, 2))

    def test_payment_and_status_endpoints(self):
        _, data, _ = self.request("POST", "/api/orders", order_payload([("P015", 1)]))
        oid, vid = data["order"]["id"], data["visit"]["id"]
        self.assertEqual(self.request("POST", f"/api/visits/{vid}/payment", {"confirmed": True, "expected_total": 380})[0], 409)
        self.assertEqual(self.request("POST", f"/api/visits/{vid}/check")[0], 200)
        status, paid, _ = self.request("POST", f"/api/visits/{vid}/payment", {"confirmed": True, "expected_total": 380})
        self.assertEqual((status, paid["visit"]["payment_status"]), (200, "paid"))
        status, moved, _ = self.request("POST", f"/api/orders/{oid}/status", {"current_status": "received", "new_status": "cooking"})
        self.assertEqual((status, moved["order"]["order_status"]), (200, "cooking"))
        status, _, _ = self.request("POST", f"/api/orders/{oid}/status", {"current_status": ["x"], "new_status": "cooking"})
        self.assertEqual(status, 400)

    def test_recommendations_endpoint(self):
        status, data, _ = self.request("POST", "/api/recommendations", {"preferences": None})
        self.assertEqual(status, 200)
        self.assertTrue(data["items"])
        self.assertEqual(self.request("POST", "/api/recommendations", {"preferences": {"age_group": "x"}})[0], 400)
        self.assertEqual(self.request("POST", "/api/recommendations", ["not", "object"])[0], 400)

    def test_database_error_is_hidden_and_nothing_saved(self):
        with mock.patch("app.orders._insert_items", side_effect=sqlite3.OperationalError("disk I/O error")):
            status, data, _ = self.request("POST", "/api/orders", order_payload([("P001", 1)]))
        self.assertEqual((status, data["error"]["code"]), (500, "DB_ERROR"))
        self.assertIn("error_id", data["error"])
        self.assertNotIn("disk I/O", str(data))
        self.assertEqual(self.count("orders"), 0)

    def test_store_admin_operations(self):
        status, _, _ = self.request("PUT", "/api/admin/settings", {"store_name": "テスト食堂", "max_party_size": "8"})
        self.assertEqual(status, 200)
        _, public, _ = self.request("GET", "/api/settings/public")
        self.assertEqual(public, {"store_name": "テスト食堂", "max_party_size": 8})
        status, data, _ = self.request("POST", "/api/orders", order_payload([("P001", 1)], 9))
        self.assertEqual((status, data["error"]["code"]), (400, "PARTY_SIZE_TOO_LARGE"))
        status, _, _ = self.request("PUT", "/api/admin/settings", {"backup_dir": "C:\\"})
        self.assertEqual(status, 400)  # 店舗はバックアップ先を変えられない

        status, body, headers = self.request("GET", "/api/admin/export/menu.csv?bom=1", raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertIn("attachment", headers["Content-Disposition"])

        csv_body = "product_id,category_name,product_name,price,image_path,is_available,popularity_score,recommendation_tags\nZ1,フード,新商品,500,,1,10,\n".encode("utf-8")
        status, preview, _ = self.request("POST", "/api/admin/menu-import?mode=preview", csv_body, headers={"Content-Type": "text/csv"})
        self.assertEqual((status, preview["summary"]["add"]), (200, 1))
        self.assertEqual(self.request("POST", "/api/admin/menu-import?mode=apply", csv_body, headers={"Content-Type": "text/csv"})[0], 200)
        self.assertEqual(self.count("products", "id = 'Z1'"), 1)
        self.assertEqual(self.count("products", "id = 'Z1' AND store_id = ?", (self.other_store_id,)), 0)

        _, data, _ = self.request("POST", "/api/orders", order_payload([("P001", 1)]))
        status, cancelled, _ = self.request("POST", f"/api/orders/{data['order']['id']}/cancel", {"reason": "入力ミス"})
        self.assertEqual((status, cancelled["order"]["order_status"]), (200, "cancelled"))


class AuthenticationTest(ServerTestCase):
    def test_all_store_apis_require_login(self):
        for path in STORE_GET_ROUTES + STORE_ADMIN_GET_ROUTES:
            with self.subTest(path=path):
                status, data, _ = self.request("GET", path, cookie=False)
                self.assertEqual((status, data["error"]["code"]), (401, "STORE_LOGIN_REQUIRED"))
        for path, body in [("/api/orders", order_payload([("P001", 1)])), ("/api/recommendations", {}),
                           ("/api/visits/V20261019-001/check", {}), ("/api/auth/store/reauth", {"password": STORE_PASSWORD})]:
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path, body, cookie=False)[0], 401)
        for bad_cookie in ["store_session=forged-token-value", "store_session=", "store_session=" + "a" * 5000, "garbage;;=="]:
            with self.subTest(cookie=bad_cookie[:30]):
                self.assertEqual(self.request("GET", "/api/orders", cookie=bad_cookie)[0], 401)
        self.assertEqual(self.count("orders"), 0)

    def test_admin_functions_require_recent_password(self):
        cookie = self.store_login("a@example.com", STORE_PASSWORD, admin=False)
        self.assertEqual(self.request("GET", "/api/orders", cookie=cookie)[0], 200)
        for path in STORE_ADMIN_GET_ROUTES:
            with self.subTest(path=path):
                status, data, _ = self.request("GET", path, cookie=cookie)
                self.assertEqual((status, data["error"]["code"]), (403, "REAUTH_REQUIRED"))
        self.assertEqual(self.request("POST", "/api/auth/store/reauth", {"password": "wrong password!!"}, cookie=cookie)[0], 401)
        self.assertEqual(self.request("POST", "/api/auth/store/reauth", {"password": STORE_PASSWORD}, cookie=cookie)[0], 200)
        self.assertEqual(self.request("GET", "/api/admin/settings", cookie=cookie)[0], 200)

    def test_store_cannot_access_other_store_data(self):
        _, data, _ = self.request("POST", "/api/orders", order_payload([("P001", 1)]))
        order_id, visit_id = data["order"]["id"], data["visit"]["id"]
        other = self.store_login("b@example.com", OTHER_PASSWORD, admin=True)
        for method, path, body in [("GET", f"/api/visits/{visit_id}", None), ("GET", f"/api/visits/{visit_id}/bill", None),
                                   ("POST", f"/api/visits/{visit_id}/check", {}),
                                   ("POST", f"/api/visits/{visit_id}/payment", {"confirmed": True, "expected_total": 680})]:
            with self.subTest(path=path, method=method):
                status, response, _ = self.request(method, path, body, cookie=other)
                self.assertEqual((status, response["error"]["code"]), (404, "VISIT_NOT_FOUND"))
        status, response, _ = self.request("POST", "/api/orders", {**order_payload([("P001", 1)]), "visit_id": visit_id}, cookie=other)
        self.assertEqual((status, response["error"]["code"]), (404, "VISIT_NOT_FOUND"))  # 他店舗の伝票に追加注文できない
        for method, path, body in [("GET", f"/api/orders/{order_id}", None),
                                   ("POST", f"/api/orders/{order_id}/status", {"current_status": "received", "new_status": "cooking"}),
                                   ("POST", f"/api/orders/{order_id}/cancel", {"reason": "x"})]:
            with self.subTest(path=path, method=method):
                status, response, _ = self.request(method, path, body, cookie=other)
                self.assertEqual((status, response["error"]["code"]), (404, "ORDER_NOT_FOUND"))
        _, listed, _ = self.request("GET", "/api/orders", cookie=other)
        self.assertEqual(listed["orders"], [])
        status, body, _ = self.request("GET", "/api/admin/export/orders.csv", cookie=other, raw=True)
        self.assertNotIn(data["order"]["order_number"].encode(), body)
        row = self.conn.execute("SELECT order_status, payment_status FROM orders WHERE order_number = ? AND store_id = ?", (order_id, self.store_id)).fetchone()
        self.assertEqual(tuple(row), ("received", "unpaid"))

    def test_login_responses_and_cookie_flags(self):
        outcomes = set()
        for email, password in [("nobody@example.com", "whatever password"), ("a@example.com", "wrong password!!")]:
            status, data, _ = self.request("POST", "/api/auth/store/login", {"email": email, "password": password}, cookie=False)
            outcomes.add((status, data["error"]["code"], data["error"]["message"]))
        self.assertEqual(len(outcomes), 1)
        status, data, headers = self.request("POST", "/api/auth/store/login", {"email": "a@example.com", "password": STORE_PASSWORD}, cookie=False)
        cookie = headers["_set_cookies"][0]
        for flag in ["HttpOnly", "SameSite=Strict", "Path=/"]:
            self.assertIn(flag, cookie)
        self.assertNotIn("Secure", cookie)
        self.assertNotIn("password", str(data))
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_login_rotates_session_and_logout_revokes_it(self):
        old = self.cookie
        new = self.store_login("a@example.com", STORE_PASSWORD)
        self.assertNotEqual(old, new)
        status, _, headers = self.request("POST", "/api/auth/store/logout", cookie=new)
        self.assertIn("Max-Age=0", headers["_set_cookies"][0])
        self.assertEqual(self.request("GET", "/api/orders", cookie=new)[0], 401)  # サーバー側でも無効

    def test_login_attempts_are_rate_limited_per_client(self):
        for _ in range(10):
            self.request("POST", "/api/auth/store/login", {"email": "b@example.com", "password": "wrong password!!"}, cookie=False)
        status, data, _ = self.request("POST", "/api/auth/store/login", {"email": "b@example.com", "password": OTHER_PASSWORD}, cookie=False)
        self.assertEqual((status, data["error"]["code"]), (429, "TOO_MANY_REQUESTS"))

    def test_password_reset_over_http_and_existing_sessions_end(self):
        status, first, _ = self.request("POST", "/api/auth/store/request-code", {"email": "a@example.com"}, cookie=False)
        status2, second, _ = self.request("POST", "/api/auth/store/request-code", {"email": "nobody@example.com"}, cookie=False)
        self.assertEqual((status, first), (status2, second))  # 登録の有無で応答が変わらない
        self.assertEqual(len(self.mailer.outbox), 1)
        code = code_from(self.mailer.outbox[0])
        status, data, _ = self.request("POST", "/api/auth/store/verify-code", {"email": "a@example.com", "code": code}, cookie=False)
        self.assertEqual(status, 200)
        status, _, _ = self.request("POST", "/api/auth/store/set-password", {"token": data["token"], "password": "fresh long passphrase"}, cookie=False)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/orders")[0], 401)  # 以前のログインは無効
        self.store_login("a@example.com", "fresh long passphrase")

    def test_suspended_store_session_stops_immediately(self):
        operator = self.operator_cookie()
        status, _, _ = self.request("POST", f"/api/operator/stores/{self.store_id}/status", {"status": "suspended"}, cookie=operator)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/orders")[0], 401)
        status, data, _ = self.request("POST", "/api/auth/store/login", {"email": "a@example.com", "password": STORE_PASSWORD}, cookie=False)
        self.assertEqual((status, data["error"]["code"]), (401, "LOGIN_FAILED"))


class OperatorSiteTest(ServerTestCase):
    def test_operator_apis_require_operator_login(self):
        for path in OPERATOR_GET_ROUTES:
            with self.subTest(path=path):
                status, data, _ = self.request("GET", path)  # 店舗のログインでは使えない
                self.assertEqual((status, data["error"]["code"]), (401, "OPERATOR_LOGIN_REQUIRED"))
        operator = self.operator_cookie()
        for path in STORE_GET_ROUTES:
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path, cookie=operator)[0], 401)  # 運営者は店舗の注文を操作できない

    def test_password_alone_is_not_enough(self):
        from app import accounts
        from tests.helpers import OPERATOR_PASSWORD

        accounts.create_or_reset_operator(self.conn, "ops@example.com", OPERATOR_PASSWORD)
        status, data, headers = self.request("POST", "/api/auth/operator/login", {"email": "ops@example.com", "password": OPERATOR_PASSWORD}, cookie=False)
        self.assertEqual(status, 200)
        self.assertEqual(headers["_set_cookies"], [])  # まだログインしていない
        self.assertNotIn(code_from(self.mailer.outbox[-1]), str(data))  # コードを応答に含めない
        status, _, _ = self.request("POST", "/api/auth/operator/verify", {"challenge": data["challenge"], "code": "000000"}, cookie=False)
        self.assertEqual(status, 400)

    def test_store_list_and_invite(self):
        operator = self.operator_cookie()
        status, data, _ = self.request("GET", "/api/operator/stores", cookie=operator)
        self.assertEqual(status, 200)
        self.assertEqual({s["name"] for s in data["stores"]}, {"A店", "B店"})
        self.assertNotIn("password_hash", str(data))
        self.assertNotIn("pbkdf2", str(data))
        status, invited, _ = self.request("POST", "/api/operator/stores", {"name": "C店", "email": "c@example.com"}, cookie=operator)
        self.assertEqual((status, invited["store"]["status"]), (201, "invited"))
        self.assertEqual(self.mailer.outbox[-1]["to"], "c@example.com")
        status, _, _ = self.request("POST", "/api/operator/stores", {"name": "<script>", "email": "bad"}, cookie=operator)
        self.assertEqual(status, 400)

    def test_operator_site_is_local_only_by_default(self):
        operator = self.operator_cookie()
        with mock.patch.object(Handler, "_is_local_client", return_value=False):
            for method, path in [("GET", "/api/operator/stores"), ("POST", "/api/auth/operator/login")]:
                status, data, _ = self.request(method, path, {} if method == "POST" else None, cookie=operator)
                self.assertEqual((status, data["error"]["code"]), (403, "OPERATOR_LOCAL_ONLY"))
        self.server.app.config.operator_allow_remote = True
        with mock.patch.object(Handler, "_is_local_client", return_value=False):
            self.assertEqual(self.request("GET", "/api/operator/stores", cookie=operator)[0], 200)

    def test_backup_restore_logs_everyone_out(self):
        operator = self.operator_cookie()
        status, created, _ = self.request("POST", "/api/operator/backups", cookie=operator)
        self.assertEqual(status, 201)
        status, data, _ = self.request("POST", "/api/operator/backups/restore", {"filename": created["filename"]}, cookie=operator)
        self.assertEqual((status, data["error"]["code"]), (400, "CONFIRMATION_REQUIRED"))
        status, _, _ = self.request("POST", "/api/operator/backups/restore", {"filename": created["filename"], "confirm_text": "復元"}, cookie=operator)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/orders")[0], 401)
        self.assertEqual(self.request("GET", "/api/operator/stores", cookie=operator)[0], 401)
        for name in ["../restaurant.db", "..\\restaurant.db", "secret.key"]:
            operator = self.operator_cookie()
            status, _, _ = self.request("POST", "/api/operator/backups/restore", {"filename": name, "confirm_text": "復元"}, cookie=operator)
            self.assertEqual(status, 400)


class TransportSecurityTest(ServerTestCase):
    def test_state_changes_require_csrf_header_and_same_origin(self):
        status, data, _ = self.request("POST", "/api/orders", order_payload([("P001", 1)]), csrf=False)
        self.assertEqual((status, data["error"]["code"]), (403, "CSRF_CHECK_FAILED"))
        self.assertEqual(self.request("POST", "/api/orders", order_payload([("P001", 1)]), headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/orders", order_payload([("P001", 1)]), headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("POST", "/api/auth/store/login", {"email": "a@example.com", "password": STORE_PASSWORD}, csrf=False, cookie=False)[0], 403)
        self.assertEqual(self.count("orders"), 0)

    def test_data_files_are_not_served(self):
        for path in ["/data/restaurant.db", "/../data/restaurant.db", "/%2e%2e/data/restaurant.db", "/%2e%2e/data/secret.key",
                     "/..%5cdata%5csecret.key", "/%2e%2e/app/server.py", "/%2e%2e/run.py", "/images/", "/admin.css/../../data/secret.key"]:
            with self.subTest(path=path):
                status, body, _ = self.request("GET", path, raw=True)
                self.assertEqual(status, 404)
                self.assertNotIn(b"SQLite", body)

    def test_static_pages_have_security_headers(self):
        for page in ["/", "/admin", "/account", "/operator"]:
            with self.subTest(page=page):
                status, _, headers = self.request("GET", page, raw=True)
                self.assertEqual(status, 200)
                self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["X-Frame-Options"], "DENY")
        status, _, headers = self.request("GET", "/js/app.js", raw=True)
        self.assertEqual((status, headers["Content-Type"]), (200, "text/javascript; charset=utf-8"))

    def test_secure_cookie_and_hsts_when_https(self):
        self.server.app.config.secure_cookies = True
        _, _, headers = self.request("POST", "/api/auth/store/login", {"email": "a@example.com", "password": STORE_PASSWORD}, cookie=False)
        self.assertIn("Secure", headers["_set_cookies"][0])
        self.assertIn("max-age", headers["Strict-Transport-Security"])

    def test_malformed_requests_are_rejected_safely(self):
        cases = [
            ("POST", "/api/orders", b"{not json", {"Content-Type": "application/json"}),
            ("POST", "/api/orders", b"[1,2,3]", {"Content-Type": "application/json"}),
            ("POST", "/api/orders", b"{}", {"Content-Type": "text/plain"}),
            ("POST", "/api/orders", b"[" * 100000 + b"]" * 100000, {"Content-Type": "application/json"}),
            ("POST", "/api/auth/store/login", b'{"email": {"$ne": null}, "password": {"$gt": ""}}', {"Content-Type": "application/json"}),
            ("POST", "/api/auth/store/login", b'{"email": "a@example.com\' OR 1=1 --", "password": "x"}', {"Content-Type": "application/json"}),
        ]
        for method, path, body, headers in cases:
            with self.subTest(body=body[:40]):
                status, data, _ = self.request(method, path, body, headers=headers)
                self.assertIn(status, (400, 401, 413, 415))
                self.assertNotIn("Traceback", str(data))
        self.assertEqual(self.count("orders"), 0)

    def test_missing_product_image_falls_back_to_placeholder(self):
        self.request("PUT", "/api/admin/products/P001", {"image_path": "images/products/not-created-yet.png"})
        _, products, _ = self.request("GET", "/api/products")
        path = next(p["image_path"] for p in products["products"] if p["id"] == "P001")
        self.assertEqual(self.request("GET", "/" + path, raw=True)[0], 404)  # 画像ファイルが無い（画面は仮画像を出す）
        self.assertEqual(self.request("GET", "/images/products/P002.svg", raw=True)[0], 200)
        status, body, headers = self.request("GET", "/images/placeholder.svg", raw=True)
        self.assertEqual((status, headers["Content-Type"]), (200, "image/svg+xml"))

    def test_html_in_product_name_is_returned_as_plain_data(self):
        status, _, _ = self.request("PUT", "/api/admin/products/P001", {"name": "<img src=x onerror=alert(1)>"})
        self.assertEqual(status, 200)
        _, products, headers = self.request("GET", "/api/products")
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("<img src=x onerror=alert(1)>", [p["name"] for p in products["products"]])
