"""HTTP サーバー（標準ライブラリのみ）。JSON API と static/ の配信を行う。

認証の段階:
- public       : ログイン・パスワード設定・ヘルスチェックなど
- store        : 店舗アカウントでログイン中（注文・会計・履歴）
- store_admin  : 店舗ログインに加え、直近15分以内にパスワードを再入力済み（設定・商品・CSV・取消）
- operator     : 運営者（パスワード＋メール認証コード）でログイン中。既定でサーバー本体からの接続のみ

その他の安全対策:
- データフォルダ（DB・ログ・バックアップ・秘密鍵）は static/ の外にあり、配信されない。
- 状態を変える API は X-Requested-With ヘッダーと Origin の一致を必須にする（CSRF 対策）。
- 内部エラーの詳細はログにだけ出し、画面にはエラー番号と日本語の案内だけを返す。
"""

import io
import ipaddress
import threading
import json
import logging
import re
import sqlite3
import time
import uuid
from datetime import timedelta
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from . import accounts, backup, csv_io, menu, orders, recommend, settings, visits
from .allergens import ALLERGENS
from .auth import RateLimiter, SessionStore, credential_tag, load_or_create_secret_key
from .config import STATIC_DIR
from .db import open_db
from .errors import AppError, bad_request, not_found
from .mailer import Mailer
from .timeutil import parse_date, today
from .validation import strict_int

log = logging.getLogger(__name__)

MAX_JSON_BYTES = 64 * 1024
MAX_UPLOAD_BYTES = csv_io.MAX_CSV_BYTES + 64 * 1024
MAX_EXPORT_DAYS = 366
STORE_COOKIE = "store_session"
OPERATOR_COOKIE = "operator_session"
WINDOW = 15 * 60
MAX_CONNECTIONS = 64
MAX_CONNECTIONS_PER_CLIENT = 8
REQUEST_TIMEOUT_SECONDS = 20
CODE_FAILURES_PER_CLIENT_PER_DAY = 10
FORWARDING_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "X-Real-IP")

# (上限回数, 秒) 接続元IPごと
RATE_LIMITS = {
    "store_login": (10, WINDOW),
    "store_reauth": (10, WINDOW),
    "store_request_code": (5, WINDOW),
    "store_verify_code": (10, WINDOW),
    "store_set_password": (10, WINDOW),
    "operator_login": (5, WINDOW),
    "operator_verify": (10, WINDOW),
}

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
}
STATIC_ALIASES = {"/": "/index.html", "/admin": "/admin.html", "/account": "/account.html", "/operator": "/operator.html"}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class Response:
    def __init__(self, body, status=200, content_type="application/json; charset=utf-8", headers=None):
        self.body = body
        self.status = status
        self.content_type = content_type
        self.headers = headers or []


def json_response(data, status=200, headers=None):
    return Response(json.dumps(data, ensure_ascii=False).encode("utf-8"), status, headers=headers)


class Request:
    def __init__(self, handler, conn, params, query):
        self.handler = handler
        self.conn = conn
        self.params = params
        self.query = query
        self.app = handler.server.app
        self.session = None
        self.session_token = None

    @property
    def store_id(self):
        return self.session["store_id"]

    @property
    def operator_id(self):
        return self.session["account_id"]

    def arg(self, name, default=None):
        values = self.query.get(name)
        return values[0] if values else default

    def body_bytes(self, limit):
        length_header = self.handler.headers.get("Content-Length")
        if length_header is None:
            raise AppError(411, "LENGTH_REQUIRED", "送信データの長さが不明です。", "画面を再読み込みしてからやり直してください。")
        try:
            length = int(length_header)
        except ValueError:
            raise bad_request("INVALID_LENGTH", "送信データが正しくありません。", "画面を再読み込みしてからやり直してください。") from None
        if length < 0 or length > limit:
            raise AppError(413, "PAYLOAD_TOO_LARGE", "送信データが大きすぎます。", "内容を減らしてからやり直してください。")
        return self.handler.rfile.read(length)

    def json(self):
        content_type = self.handler.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            raise AppError(415, "UNSUPPORTED_TYPE", "送信データの種類が正しくありません。", "画面を再読み込みしてからやり直してください。")
        raw = self.body_bytes(MAX_JSON_BYTES)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise bad_request("INVALID_JSON", "送信データの形式が正しくありません。", "画面を再読み込みしてからやり直してください。") from None
        return data

    def json_object(self):
        data = self.json()
        if not isinstance(data, dict):
            raise bad_request("INVALID_JSON", "送信データの形式が正しくありません。", "画面を再読み込みしてからやり直してください。")
        return data

    def date_range(self):
        date_from = parse_date(self.arg("from") or self.arg("date") or today().isoformat(), "開始日")
        date_to = parse_date(self.arg("to") or self.arg("date") or date_from.isoformat(), "終了日")
        if date_to < date_from:
            raise bad_request("INVALID_RANGE", "終了日が開始日より前になっています。", "日付の範囲を確認してください。")
        if date_to - date_from > timedelta(days=MAX_EXPORT_DAYS):
            raise bad_request("RANGE_TOO_LONG", f"期間が長すぎます（最大{MAX_EXPORT_DAYS}日）。", "期間を分けて指定してください。")
        return date_from, date_to

    def with_bom(self):
        value = self.arg("bom")
        if value is None:
            return settings.get_store_settings(self.conn, self.store_id)["csv_bom_default"] == "1"
        return value == "1"

    def client(self):
        return self.handler.client_address[0]

    def limit(self, name):
        limit, window = RATE_LIMITS[name]
        wait = self.app.limiter.hit(f"{name}:{self.client()}", limit, window)
        if wait:
            log.warning("回数制限に達しました: %s %s", name, self.client())
            raise AppError(429, "TOO_MANY_REQUESTS", "短時間に操作が繰り返されたため、一時的に受け付けを止めています。",
                           f"{max(1, wait // 60)}分ほど待ってからやり直してください。")


class App:
    def __init__(self, config, mailer=None):
        self.config = config
        self.secret_key = load_or_create_secret_key(config.secret_key_path, config.secret_key_env)
        self.mailer = mailer or Mailer(config)
        self.sessions = SessionStore()
        self.limiter = RateLimiter()
        self.routes = []
        self._register_routes()

    def add(self, method, pattern, func, auth="store"):
        assert auth in ("public", "store", "store_admin", "operator", "operator_auth")
        self.routes.append((method, re.compile("^" + pattern + "$"), func, auth))

    def match(self, method, path):
        allowed = False
        for route_method, pattern, func, auth in self.routes:
            found = pattern.match(path)
            if found:
                if route_method == method:
                    return func, auth, found.groupdict()
                allowed = True
        if allowed:
            raise AppError(405, "METHOD_NOT_ALLOWED", "この操作は利用できません。", "画面を再読み込みしてください。")
        raise not_found("API_NOT_FOUND", "要求された機能が見つかりません。", "画面を再読み込みしてください。")

    def cookie(self, name, token, max_age=None):
        parts = [f"{name}={token}", "HttpOnly", "SameSite=Strict", "Path=/"]
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        if self.config.secure_cookies:
            parts.append("Secure")
        return "; ".join(parts)

    def backup_dir(self, conn):
        configured = settings.get_platform_settings(conn)["backup_dir"] or self.config.backup_dir_env
        return backup.resolve_backup_dir(self.config, configured)

    # ---------- ルート定義 ----------

    def _register_routes(self):
        oid = r"(?P<order_id>[0-9]{8}-[0-9]{4,6})"
        sid = r"(?P<store_id>[0-9]{1,12})"
        self.add("GET", "/api/health", lambda r: {"ok": True}, auth="public")

        # 店舗アカウント
        self.add("POST", "/api/auth/store/login", self.store_login, auth="public")
        self.add("POST", "/api/auth/store/logout", self.store_logout, auth="public")
        self.add("GET", "/api/auth/store/session", self.store_session, auth="public")
        self.add("POST", "/api/auth/store/reauth", self.store_reauth, auth="store")
        self.add("POST", "/api/auth/store/request-code", self.store_request_code, auth="public")
        self.add("POST", "/api/auth/store/verify-code", self.store_verify_code, auth="public")
        self.add("POST", "/api/auth/store/set-password", self.store_set_password, auth="public")

        # 注文用（店舗ログイン中）
        self.add("GET", "/api/settings/public", lambda r: settings.public_settings(r.conn, r.store_id))
        self.add("GET", "/api/categories", lambda r: {"categories": menu.list_categories(r.conn, r.store_id)})
        self.add("GET", "/api/allergens", lambda r: {"allergens": [{"code": c, "name": n} for c, n in ALLERGENS.items()]})
        self.add("GET", "/api/products", self.get_products)
        self.add("POST", "/api/recommendations", self.post_recommendations)
        self.add("POST", "/api/orders", self.post_order)
        self.add("GET", "/api/orders", self.get_orders)
        self.add("GET", f"/api/orders/{oid}", lambda r: {"order": orders.get_order(r.conn, r.store_id, r.params["order_id"])})
        vid = r"(?P<visit_id>V[0-9]{8}-[0-9]{3,5})"
        self.add("GET", "/api/visits", self.get_visits)
        self.add("GET", f"/api/visits/{vid}", lambda r: {"visit": visits.get_visit(r.conn, r.store_id, r.params["visit_id"])})
        self.add("GET", f"/api/visits/{vid}/bill", lambda r: {"visit": visits.compute_bill(r.conn, r.store_id, r.params["visit_id"])})
        self.add("POST", f"/api/visits/{vid}/check", lambda r: {"visit": visits.mark_checked(r.conn, r.store_id, r.params["visit_id"])})
        self.add("POST", f"/api/visits/{vid}/payment",
                 lambda r: {"visit": visits.record_payment(r.conn, r.store_id, r.params["visit_id"], r.json())})
        self.add("POST", f"/api/orders/{oid}/status",
                 lambda r: {"order": orders.advance_order_status(r.conn, r.store_id, r.params["order_id"], r.json())})
        self.add("GET", "/api/sales/summary", lambda r: orders.sales_summary(r.conn, r.store_id, *r.date_range()))

        # 店舗の管理機能（パスワード再入力済み）
        self.add("POST", f"/api/orders/{oid}/cancel",
                 lambda r: {"order": orders.cancel_order(r.conn, r.store_id, r.params["order_id"], r.json())}, auth="store_admin")
        self.add("GET", "/api/admin/settings", lambda r: {"settings": settings.get_store_settings(r.conn, r.store_id)}, auth="store_admin")
        self.add("PUT", "/api/admin/settings",
                 lambda r: {"settings": settings.update_store_settings(r.conn, r.store_id, r.json())}, auth="store_admin")
        self.add("GET", "/api/admin/categories",
                 lambda r: {"categories": menu.list_categories(r.conn, r.store_id, include_inactive=True)}, auth="store_admin")
        self.add("POST", "/api/admin/categories",
                 lambda r: json_response({"category": menu.create_category(r.conn, r.store_id, r.json())}, 201), auth="store_admin")
        self.add("PUT", r"/api/admin/categories/(?P<category_id>[0-9]{1,12})",
                 lambda r: {"category": menu.update_category(r.conn, r.store_id, int(r.params["category_id"]), r.json())}, auth="store_admin")
        self.add("GET", "/api/admin/products",
                 lambda r: {"products": menu.list_products(r.conn, r.store_id, include_inactive_categories=True)}, auth="store_admin")
        self.add("PUT", r"/api/admin/products/(?P<product_id>[A-Za-z0-9_-]{1,32})",
                 lambda r: {"product": menu.update_product(r.conn, r.store_id, r.params["product_id"], r.json())}, auth="store_admin")
        self.add("POST", "/api/admin/menu-import", self.post_menu_import, auth="store_admin")
        self.add("GET", r"/api/admin/export/(?P<kind>menu|sales|orders)\.csv", self.get_export, auth="store_admin")
        self.add("GET", "/api/admin/audit-logs", self.get_store_audit_logs, auth="store_admin")

        # 運営者
        self.add("POST", "/api/auth/operator/login", self.operator_login, auth="operator_auth")
        self.add("POST", "/api/auth/operator/verify", self.operator_verify, auth="operator_auth")
        self.add("POST", "/api/auth/operator/logout", self.operator_logout, auth="operator_auth")
        self.add("GET", "/api/auth/operator/session", self.operator_session, auth="operator_auth")
        self.add("GET", "/api/operator/stores", lambda r: {"stores": accounts.list_stores(r.conn)}, auth="operator")
        self.add("POST", "/api/operator/stores", self.operator_invite, auth="operator")
        self.add("POST", f"/api/operator/stores/{sid}/invite", self.operator_reinvite, auth="operator")
        self.add("POST", f"/api/operator/stores/{sid}/status",
                 lambda r: {"store": accounts.set_store_status(r.conn, int(r.params["store_id"]), r.json(), self.sessions, r.operator_id)},
                 auth="operator")
        self.add("GET", "/api/operator/settings", lambda r: {"settings": settings.get_platform_settings(r.conn)}, auth="operator")
        self.add("PUT", "/api/operator/settings",
                 lambda r: {"settings": settings.update_platform_settings(r.conn, r.json())}, auth="operator")
        self.add("GET", "/api/operator/backups", lambda r: {"backups": backup.list_backups(self.backup_dir(r.conn)),
                                                           "backup_dir": str(self.backup_dir(r.conn))}, auth="operator")
        self.add("POST", "/api/operator/backups", self.post_backup, auth="operator")
        self.add("POST", "/api/operator/backups/restore", self.post_restore, auth="operator")
        self.add("GET", "/api/operator/audit-logs", self.get_operator_audit_logs, auth="operator")

    # ---------- 店舗アカウント ----------

    def store_login(self, r):
        r.limit("store_login")
        body = r.json_object()
        row = accounts.store_login(r.conn, body.get("email"), body.get("password"))
        self.sessions.revoke(r.handler.cookie_value(STORE_COOKIE))
        token = self.sessions.create("store", row["id"], store_id=row["id"], elevated=body.get("admin") is True,
                                     tag=credential_tag(row["password_hash"]))
        return json_response({"logged_in": True, "store_name": row["name"]}, headers=[("Set-Cookie", self.cookie(STORE_COOKIE, token))])

    def store_logout(self, r):
        self.sessions.revoke(r.handler.cookie_value(STORE_COOKIE))
        return json_response({"logged_in": False}, headers=[("Set-Cookie", self.cookie(STORE_COOKIE, "", max_age=0))])

    def no_login_store_id(self, conn):
        row = conn.execute("SELECT id FROM stores ORDER BY id LIMIT 1").fetchone()
        if row is None:
            raise AppError(503, "NO_STORE", "店舗がまだ登録されていません。", "ログインなしモードでは、最初に登録された店舗として操作します。運営者サイトで店舗を招待してください。")
        return row["id"]

    def store_session(self, r):
        if self.config.no_login:
            store = r.conn.execute("SELECT name FROM stores WHERE id = ?", (self.no_login_store_id(r.conn),)).fetchone()
            return {"logged_in": True, "store_name": store["name"], "elevated": True, "no_login": True}
        session = r.handler.store_session_or_none(r.conn)
        if session is None:
            return {"logged_in": False}
        store = r.conn.execute("SELECT name FROM stores WHERE id = ?", (session["store_id"],)).fetchone()
        return {"logged_in": True, "store_name": store["name"], "elevated": session["elevated_until"] > time.time()}

    def store_reauth(self, r):
        r.limit("store_reauth")
        if accounts.verify_store_password(r.conn, r.store_id, r.json_object().get("password")):
            self.sessions.elevate(r.session_token)
            return {"elevated": True}
        if self.sessions.record_reauth_failure(r.session_token):
            raise AppError(401, "STORE_LOGIN_REQUIRED", "パスワードを続けて間違えたため、ログアウトしました。", "もう一度ログインしてください。")
        raise AppError(401, "REAUTH_FAILED", "パスワードが正しくありません。", "入力内容を確認してください。続けて間違えるとログアウトします。")

    def store_request_code(self, r):
        r.limit("store_request_code")
        accounts.request_store_code(r.conn, r.json_object().get("email"), self.mailer, self.config.public_base_url)
        return {"sent": True, "message": "登録されているメールアドレスであれば、認証コードを送信しました。"}

    def store_verify_code(self, r):
        r.limit("store_verify_code")
        # 認証コードの誤りは接続元ごとにも1日の上限を設ける（アカウント全体の上限を他人に使い切られにくくする）。
        failure_key = f"code_failure:{r.client()}"
        if self.limiter.count(failure_key, 86400) >= CODE_FAILURES_PER_CLIENT_PER_DAY:
            raise AppError(429, "TOO_MANY_REQUESTS", "認証コードの入力に何度も失敗したため、この端末からは一時的に受け付けていません。",
                           "時間をおいてやり直すか、運営者に連絡してください。")
        body = r.json_object()
        try:
            return {"token": accounts.verify_store_code(r.conn, body.get("email"), body.get("code"), self.mailer)}
        except AppError as exc:
            if exc.code == "CODE_INVALID":
                self.limiter.hit(failure_key, CODE_FAILURES_PER_CLIENT_PER_DAY + 1, 86400)
            raise

    def store_set_password(self, r):
        r.limit("store_set_password")
        body = r.json_object()
        store = accounts.set_store_password(r.conn, body.get("token"), body.get("password"), self.sessions, self.mailer)
        # メールで本人確認したうえでの設定なので、そのままログインした状態にする（第三者にロックされ続けても入れるように）。
        token = self.sessions.create("store", store["id"], store_id=store["id"], tag=credential_tag(store["password_hash"]))
        return json_response({"ok": True, "logged_in": True}, headers=[("Set-Cookie", self.cookie(STORE_COOKIE, token))])

    # ---------- 注文用 ----------

    def get_products(self, r):
        category = r.arg("category_id")
        category_id = strict_int(category, "カテゴリ", 1, 2**31) if category else None
        return {"products": menu.list_products(r.conn, r.store_id, category_id)}

    def post_recommendations(self, r):
        body = r.json_object()
        return recommend.recommend(menu.list_products(r.conn, r.store_id), body.get("preferences"))

    def post_order(self, r):
        order, created = orders.create_order(r.conn, r.store_id, r.json(), settings.get_max_party_size(r.conn, r.store_id))
        visit = visits.get_visit(r.conn, r.store_id, order["visit_id"])
        return json_response({"order": order, "visit": visit, "duplicate": not created}, 201 if created else 200)

    def get_visits(self, r):
        keyword = (r.arg("q") or "").strip()[:20]
        date_from, date_to = r.date_range()
        return {"visits": visits.list_visits(r.conn, r.store_id, date_from, date_to, keyword or None, r.arg("status", "all"))}

    def get_orders(self, r):
        keyword = (r.arg("q") or "").strip()[:20]
        date_from, date_to = r.date_range()
        return {"orders": orders.list_orders(r.conn, r.store_id, date_from, date_to, keyword or None)}

    # ---------- 店舗の管理機能 ----------

    def post_menu_import(self, r):
        mode = r.arg("mode")
        if mode not in ("preview", "apply"):
            raise bad_request("INVALID_MODE", "取り込みの指定が正しくありません。", "画面を再読み込みしてください。")
        content_type = r.handler.headers.get("Content-Type", "")
        if not content_type.startswith(("text/csv", "application/octet-stream")):
            raise AppError(415, "UNSUPPORTED_TYPE", "CSVファイルとして送信されていません。", "拡張子が .csv のファイルを選んでください。")
        data = r.body_bytes(MAX_UPLOAD_BYTES)
        if mode == "preview":
            return csv_io.preview_menu_csv(r.conn, r.store_id, data)
        return csv_io.apply_menu_csv(r.conn, r.store_id, data)

    def get_export(self, r):
        kind = r.params["kind"]
        with_bom = r.with_bom()
        if kind == "menu":
            data, name = csv_io.export_menu(r.conn, r.store_id, with_bom), f"menu_{today():%Y%m%d}.csv"
        else:
            date_from, date_to = r.date_range()
            exporter = csv_io.export_sales if kind == "sales" else csv_io.export_orders
            data = exporter(r.conn, r.store_id, date_from, date_to, with_bom)
            name = f"{kind}_{date_from:%Y%m%d}-{date_to:%Y%m%d}.csv"
        log.info("CSVを書き出しました: store_id=%s %s", r.store_id, name)
        return Response(data, content_type="text/csv; charset=utf-8",
                        headers=[("Content-Disposition", f'attachment; filename="{name}"')])

    def get_store_audit_logs(self, r):
        limit = strict_int(r.arg("limit", "100"), "件数", 1, 500)
        rows = r.conn.execute(
            "SELECT occurred_at, actor, action, target_type, target_id, detail FROM audit_logs WHERE store_id = ? ORDER BY id DESC LIMIT ?",
            (r.store_id, limit),
        ).fetchall()
        return {"logs": [dict(row) for row in rows]}

    # ---------- 運営者 ----------

    def operator_login(self, r):
        r.limit("operator_login")
        body = r.json_object()
        challenge = accounts.operator_login(r.conn, body.get("email"), body.get("password"), self.mailer)
        return {"challenge": challenge, "message": "登録メールアドレスに認証コードを送信しました。"}

    def operator_verify(self, r):
        r.limit("operator_verify")
        body = r.json_object()
        operator = accounts.operator_verify(r.conn, body.get("challenge"), body.get("code"))
        self.sessions.revoke(r.handler.cookie_value(OPERATOR_COOKIE))
        token = self.sessions.create("operator", operator["id"], tag=credential_tag(operator["password_hash"]))
        return json_response({"logged_in": True, "email": operator["email"]}, headers=[("Set-Cookie", self.cookie(OPERATOR_COOKIE, token))])

    def operator_logout(self, r):
        self.sessions.revoke(r.handler.cookie_value(OPERATOR_COOKIE))
        return json_response({"logged_in": False}, headers=[("Set-Cookie", self.cookie(OPERATOR_COOKIE, "", max_age=0))])

    def operator_session(self, r):
        if self.config.no_login:
            return {"logged_in": True, "email": "ログインなしモード", "no_login": True}
        session = self.sessions.get(r.handler.cookie_value(OPERATOR_COOKIE), "operator")
        if session is None:
            return {"logged_in": False}
        row = r.conn.execute("SELECT email FROM operators WHERE id = ? AND is_active = 1", (session["account_id"],)).fetchone()
        return {"logged_in": row is not None, "email": row["email"] if row else None}

    def operator_invite(self, r):
        store = accounts.invite_store(r.conn, r.json(), self.mailer, self.config.public_base_url, r.operator_id)
        return json_response({"store": store}, 201)

    def operator_reinvite(self, r):
        store = accounts.resend_invite(r.conn, int(r.params["store_id"]), r.json(), self.mailer, self.config.public_base_url, r.operator_id)
        return {"store": store}

    def post_backup(self, r):
        path = backup.create_backup(self.config.db_path, self.backup_dir(r.conn))
        return json_response({"filename": path.name, "backup_dir": str(path.parent)}, 201)

    def post_restore(self, r):
        body = r.json_object()
        if body.get("confirm_text") != "復元":
            raise bad_request("CONFIRMATION_REQUIRED", "復元の確認が完了していません。", "確認欄に「復元」と入力してください。")
        result = backup.restore_backup(self.config.db_path, self.backup_dir(r.conn), body.get("filename"))
        # アカウント情報も復元前の状態に戻るため、すべてのログインを無効にする。
        self.sessions.revoke_all()
        return result

    def get_operator_audit_logs(self, r):
        limit = strict_int(r.arg("limit", "200"), "件数", 1, 500)
        rows = r.conn.execute(
            "SELECT occurred_at, actor, action, target_type, target_id, detail, store_id FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {"logs": [dict(row) for row in rows]}


class _DeadlineSocketReader(io.RawIOBase):
    """受信のたびに残り時間をソケットのタイムアウトに設定する。

    1バイトずつ少しずつ送り続けてもタイムアウトが延長されないよう、1つの接続で受信できる時間の合計に上限を設ける。
    """

    def __init__(self, sock, seconds):
        self._sock = sock
        self._deadline = time.monotonic() + seconds

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("リクエストの受信が制限時間内に終わりませんでした")
        self._sock.settimeout(remaining)
        return self._sock.recv_into(buffer)


class Handler(BaseHTTPRequestHandler):
    server_version = "RestaurantOrder/0.2"
    sys_version = ""
    # ヘッダーや本文を送らずに接続を開いたままにする攻撃で、スレッドが占有され続けないようにする。
    timeout = REQUEST_TIMEOUT_SECONDS

    def setup(self):
        super().setup()
        # 1接続1リクエスト（HTTP/1.0）なので、接続全体の受信時間に上限を設ける。
        self.rfile = io.BufferedReader(_DeadlineSocketReader(self.connection, self.timeout))

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.client_address[0], fmt % args)

    def cookie_value(self, name):
        raw = self.headers.get("Cookie")
        if not raw or len(raw) > 4096:
            return None
        try:
            cookie = SimpleCookie(raw)
        except CookieError:
            return None
        morsel = cookie.get(name)
        return morsel.value if morsel else None

    def store_session_or_none(self, conn):
        app = self.server.app
        token = self.cookie_value(STORE_COOKIE)
        session = app.sessions.get(token, "store")
        if session is None:
            return None
        row = conn.execute("SELECT status, password_hash FROM stores WHERE id = ?", (session["store_id"],)).fetchone()
        if row is None or row["status"] != "active" or session["tag"] != credential_tag(row["password_hash"]):
            app.sessions.revoke(token)
            return None
        return session

    def _is_local_client(self):
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _host_allowed(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            hostname = host[1:host.find("]")] if "]" in host else ""
        else:
            hostname = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return hostname in self.server.app.config.allowed_hosts

    def _dispatch(self, method):
        path = urlsplit(self.path).path
        try:
            if not self._host_allowed():
                log.warning("許可されていない Host ヘッダーのため拒否しました: %r", (self.headers.get("Host") or "")[:100])
                self._send(Response("このアドレスでは接続できません。RESTAURANT_ALLOWED_HOSTS を確認してください。".encode("utf-8"),
                                    421, "text/plain; charset=utf-8"))
                return
            if path.startswith("/api/"):
                self._handle_api(method, path)
            elif method in ("GET", "HEAD"):
                self._serve_static(path, head=(method == "HEAD"))
            else:
                self._send(json_response({"error": {"code": "METHOD_NOT_ALLOWED", "message": "この操作は利用できません。", "hint": ""}}, 405))
        except (BrokenPipeError, ConnectionResetError):
            log.info("クライアントとの接続が途中で切れました: %s %s", method, path)

    def _check_csrf(self):
        error = AppError(403, "CSRF_CHECK_FAILED", "不正な送信元からの操作のため処理を中止しました。", "この端末の画面を再読み込みしてから操作してください。")
        if self.headers.get("X-Requested-With") != "fetch":
            raise error
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            raise error
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            raise error

    def _authorize(self, request, auth):
        app = self.server.app
        if app.config.no_login:
            # ログインなしモード: このPCからの接続だけを、最初の店舗・運営者として扱う。
            if not self._is_local_client() or any(self.headers.get(name) for name in FORWARDING_HEADERS):
                raise AppError(403, "NO_LOGIN_LOCAL_ONLY", "ログインなしモードでは、サーバーを動かしているPCからのみ利用できます。",
                               "このPCのブラウザで http://localhost:8000/ を開いてください。")
            if auth in ("store", "store_admin"):
                store_id = app.no_login_store_id(request.conn)
                request.session = {"kind": "store", "account_id": store_id, "store_id": store_id, "elevated_until": float("inf")}
            elif auth == "operator":
                request.session = {"kind": "operator", "account_id": None}
            return
        if auth in ("operator", "operator_auth") and not app.config.operator_allow_remote and (
                not self._is_local_client() or any(self.headers.get(name) for name in FORWARDING_HEADERS)):
            # 転送ヘッダーがある＝リバースプロキシ経由（外部からの可能性）なので、接続元が 127.0.0.1 でも拒否する。
            raise AppError(403, "OPERATOR_LOCAL_ONLY", "運営者サイトは、サーバーを動かしているPCからのみ利用できます。",
                           "サーバー本体のブラウザで http://localhost からアクセスしてください。")
        if auth == "operator":
            token = self.cookie_value(OPERATOR_COOKIE)
            session = app.sessions.get(token, "operator")
            row = session and request.conn.execute(
                "SELECT password_hash FROM operators WHERE id = ? AND is_active = 1", (session["account_id"],)
            ).fetchone()
            if not row or session["tag"] != credential_tag(row["password_hash"]):
                app.sessions.revoke(token)
                raise AppError(401, "OPERATOR_LOGIN_REQUIRED", "運営者のログインが必要です。", "もう一度ログインしてください。")
            request.session, request.session_token = session, token
        elif auth in ("store", "store_admin"):
            session = self.store_session_or_none(request.conn)
            if session is None:
                raise AppError(401, "STORE_LOGIN_REQUIRED", "店舗アカウントでのログインが必要です。", "メールアドレスとパスワードでログインしてください。")
            if auth == "store_admin" and session["elevated_until"] < time.time():
                raise AppError(403, "REAUTH_REQUIRED", "管理機能を使うには、パスワードをもう一度入力してください。", "安全のため、管理機能は15分ごとに確認しています。")
            request.session, request.session_token = session, self.cookie_value(STORE_COOKIE)

    def _handle_api(self, method, path):
        app = self.server.app
        error_id = None
        try:
            func, auth, params = app.match(method, path)
            if method not in ("GET", "HEAD"):
                self._check_csrf()
            with open_db(app.config.db_path, app.secret_key) as conn:
                request = Request(self, conn, params, parse_qs(urlsplit(self.path).query))
                self._authorize(request, auth)
                result = func(request)
            response = result if isinstance(result, Response) else json_response(result)
        except AppError as exc:
            if exc.status >= 500:
                error_id = uuid.uuid4().hex[:8]
                log.error("業務エラー [%s] %s %s: %s %s", error_id, method, path, exc.code, exc.message)
            response = self._error_response(exc, error_id)
        except sqlite3.OperationalError as exc:
            error_id = uuid.uuid4().hex[:8]
            log.exception("DB操作エラー [%s] %s %s", error_id, method, path)
            if "locked" in str(exc) or "busy" in str(exc):
                response = self._error_response(AppError(503, "DB_BUSY", "データベースが混み合っていて処理できませんでした。",
                                                         "数秒待ってから、もう一度操作してください。（処理は保存されていません）"), error_id)
            else:
                response = self._error_response(self._db_error(), error_id)
        except sqlite3.DatabaseError:
            error_id = uuid.uuid4().hex[:8]
            log.exception("DBエラー [%s] %s %s", error_id, method, path)
            response = self._error_response(self._db_error(), error_id)
        except Exception:
            error_id = uuid.uuid4().hex[:8]
            log.exception("想定外のエラー [%s] %s %s", error_id, method, path)
            response = self._error_response(AppError(500, "INTERNAL_ERROR", "システムでエラーが発生しました。",
                                                     "もう一度操作してください。繰り返す場合は運営者にエラー番号を伝えてください。"), error_id)
        self._send(response, head=(method == "HEAD"))

    @staticmethod
    def _db_error():
        return AppError(500, "DB_ERROR", "データベースの読み書きでエラーが発生しました。処理は保存されていません。",
                        "もう一度操作してください。繰り返す場合は運営者にエラー番号を伝えてください。")

    @staticmethod
    def _error_response(exc, error_id):
        body = exc.to_dict()
        if error_id:
            body["error_id"] = error_id
            body["hint"] = f"{body['hint']}（エラー番号: {error_id}）"
        return json_response({"error": body}, exc.status)

    def _serve_static(self, path, head=False):
        path = STATIC_ALIASES.get(path, path)
        target = (STATIC_DIR / unquote(path).lstrip("/")).resolve()
        content_type = STATIC_TYPES.get(target.suffix.lower())
        if STATIC_DIR not in target.parents or content_type is None or not target.is_file():
            self._send(Response(b"Not Found", 404, "text/plain; charset=utf-8"), head=head)
            return
        self._send(Response(target.read_bytes(), 200, content_type, [("Cache-Control", "no-cache")]), head=head)

    def _send(self, response, head=False):
        self.connection.settimeout(self.timeout)
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        if response.content_type.startswith(("application/json", "text/csv")):
            self.send_header("Cache-Control", "no-store")
        if self.server.app.config.secure_cookies:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for name, value in [*SECURITY_HEADERS.items(), *response.headers]:
            self.send_header(name, value)
        self.end_headers()
        if not head:
            self.wfile.write(response.body)


class OrderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config, mailer=None):
        self.app = App(config, mailer)
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._per_client = {}
        self._per_client_lock = threading.Lock()
        super().__init__((config.host, config.port), Handler)

    def _acquire(self, client):
        """全体と接続元ごとの同時接続数を確認し、空きがあれば確保する。"""
        with self._per_client_lock:
            if self._per_client.get(client, 0) >= MAX_CONNECTIONS_PER_CLIENT:
                return False
            if not self._slots.acquire(blocking=False):
                return False
            self._per_client[client] = self._per_client.get(client, 0) + 1
            return True

    def _release(self, client):
        with self._per_client_lock:
            remaining = self._per_client.get(client, 1) - 1
            if remaining > 0:
                self._per_client[client] = remaining
            else:
                self._per_client.pop(client, None)
            self._slots.release()

    def process_request(self, request, client_address):
        """同時に処理する接続数に上限を設け（全体・接続元ごと）、超えた接続はすぐに閉じる。"""
        client = client_address[0]
        if not self._acquire(client):
            log.warning("同時接続数の上限に達したため接続を閉じました: %s", client)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._release(client)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release(client_address[0])
