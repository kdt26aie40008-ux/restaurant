"""店舗アカウント・運営者アカウントの登録、ログイン、メール認証コードによるパスワード設定。

安全上の方針:
- ログイン失敗・コード送信の応答は、アカウントの有無で変えない（登録されているメールアドレスを探られないため）。
- 同じアカウントで連続して失敗すると一定時間ロックする（DB に記録するので再起動しても続く）。
- 認証コードは8桁・有効期限つき・最新の1つだけ有効・5回間違えると無効。DB には HMAC だけを保存する。
- パスワードを設定・変更したら、そのアカウントのログイン中セッションをすべて無効にする。
"""

import hmac
import logging
import re
import time

from .auth import (
    CODE_DIGITS, MAX_PASSWORD_LENGTH, burn_password_time, check_password_policy, hash_password, keyed_hash, new_code,
    new_token, verify_password,
)
from .db import transaction, write_audit
from .errors import AppError, bad_request, conflict, not_found
from .mailer import mask_email
from .seed import seed_store
from .timeutil import now_iso
from .validation import clean_text

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
CODE_RE = re.compile(rf"^[0-9]{{{CODE_DIGITS}}}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,100}$")

LOCK_THRESHOLD = 5
LOCK_SECONDS = 15 * 60
MAX_CODE_ATTEMPTS = 5
STORE_CODE_TTL = 15 * 60
INVITE_CODE_TTL = 24 * 3600
PASSWORD_TOKEN_TTL = 10 * 60
OPERATOR_CODE_TTL = 10 * 60
CODE_RESEND_INTERVAL = 60
# アカウント全体での上限（総当たり防止）。接続元ごとの上限は server.py 側で別に数える。
# 上限に達して本人も使えなくなった場合は、運営者が「再設定メールを送る」で解除できる。
DAILY_CODE_FAILURE_LIMIT = 30
CODE_HOURLY_LIMIT = 5
OPERATOR_MIN_PASSWORD_LENGTH = 12

STORE_STATUS_LABELS = {"unclaimed": "メール未設定", "invited": "招待中（パスワード未設定）", "active": "利用中", "suspended": "利用停止"}


def _now():
    return int(time.time())


def login_failed():
    return AppError(401, "LOGIN_FAILED", "メールアドレスまたはパスワードが正しくありません。",
                    "入力内容を確認してください。何度も間違えると、しばらくログインできなくなります。")


def code_invalid():
    return AppError(400, "CODE_INVALID", "認証コードが正しくないか、有効期限が切れています。",
                    "メールに届いた8桁の数字を確認してください。うまくいかない場合は、認証コードをもう一度送信してください。")


def token_invalid():
    return AppError(400, "TOKEN_INVALID", "パスワード設定の有効期限が切れたか、すでに使われています。",
                    "お手数ですが、認証コードの送信からやり直してください。")


def normalize_email(value):
    if not isinstance(value, str):
        raise bad_request("INVALID_EMAIL", "メールアドレスの形式が正しくありません。", "例: shop@example.com のように入力してください。")
    email = value.strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise bad_request("INVALID_EMAIL", "メールアドレスの形式が正しくありません。", "例: shop@example.com のように入力してください。")
    return email


# ---------- 共通: パスワード照合とロック ----------

def _lock(conn, table, account_id, now, actor, store_id):
    conn.execute(f"UPDATE {table} SET failed_login_count = 0, locked_until = ? WHERE id = ?", (now + LOCK_SECONDS, account_id))
    write_audit(conn, actor, "login.locked", table, account_id, store_id=store_id)
    log.warning("ログイン失敗が続いたためアカウントをロックしました: %s id=%s", table, account_id)


def _check_password(conn, table, account_id, password, actor, store_id=None):
    """パスワードを照合し、失敗回数・ロックを記録する。ロック中は正しいパスワードでも失敗にする。

    照合（時間がかかる）の前に、最新の状態を読み直して試行を1回分予約する。
    同時に大量のリクエストを送られても、ロックまでに照合されるのは LOCK_THRESHOLD 回だけになる。
    """
    assert table in ("stores", "operators")
    now = _now()
    with transaction(conn):
        row = conn.execute(f"SELECT password_hash, failed_login_count, locked_until FROM {table} WHERE id = ?", (account_id,)).fetchone()
        locked = row["locked_until"] is not None and row["locked_until"] > now
        if not locked:
            conn.execute(f"UPDATE {table} SET failed_login_count = failed_login_count + 1 WHERE id = ?", (account_id,))
            if row["failed_login_count"] + 1 > LOCK_THRESHOLD:
                _lock(conn, table, account_id, now, actor, store_id)
                locked = True
    if locked:
        burn_password_time(password)
        return False
    if row["password_hash"]:
        ok = verify_password(password, row["password_hash"])
    else:
        burn_password_time(password)
        ok = False
    with transaction(conn):
        if ok:
            # 照合中に別の試行でロックされていたら、成功として扱わない。
            ok = conn.execute(
                f"UPDATE {table} SET failed_login_count = 0, locked_until = NULL WHERE id = ? AND (locked_until IS NULL OR locked_until <= ?)",
                (account_id, now),
            ).rowcount == 1
        if not ok:
            current = conn.execute(f"SELECT failed_login_count, locked_until FROM {table} WHERE id = ?", (account_id,)).fetchone()
            already_locked = current["locked_until"] is not None and current["locked_until"] > now
            if current["failed_login_count"] >= LOCK_THRESHOLD and not already_locked:
                _lock(conn, table, account_id, now, actor, store_id)
            write_audit(conn, actor, "login.failed", table, account_id, store_id=store_id)
    return ok


def _validated_password_input(password):
    if not isinstance(password, str) or not password or len(password) > MAX_PASSWORD_LENGTH:
        burn_password_time(password)
        raise login_failed()
    return password


# ---------- 店舗: ログイン ----------

def store_login(conn, email, password):
    try:
        email = normalize_email(email)
    except AppError:
        burn_password_time(password)
        raise login_failed() from None
    password = _validated_password_input(password)
    row = conn.execute("SELECT * FROM stores WHERE email = ?", (email,)).fetchone()
    if row is None or row["status"] != "active":
        burn_password_time(password)
        raise login_failed()
    if not _check_password(conn, "stores", row["id"], password, "store", store_id=row["id"]):
        raise login_failed()
    with transaction(conn):
        conn.execute("UPDATE stores SET last_login_at = ? WHERE id = ?", (now_iso(), row["id"]))
        write_audit(conn, "store", "login.success", "stores", row["id"], store_id=row["id"])
    return row


def verify_store_password(conn, store_id, password):
    """ログイン中の端末で管理機能に入る前の再確認。

    アカウントのロックには数えない（第三者がロックを繰り返しても、ログイン中の店舗が管理機能を使えるように）。
    代わりに、同じログインで続けて間違えた回数をサーバー側で数え、上限でログアウトさせる。
    戻り値: 正しければ True。
    """
    if not isinstance(password, str) or not password or len(password) > MAX_PASSWORD_LENGTH:
        burn_password_time(password)
        return False
    row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
    if row is None or row["status"] != "active" or not row["password_hash"]:
        burn_password_time(password)
        return False
    ok = verify_password(password, row["password_hash"])
    if not ok:
        with transaction(conn):
            write_audit(conn, "store", "reauth.failed", "stores", store_id, store_id=store_id)
    return ok


# ---------- 店舗: 認証コードによるパスワード設定 ----------

def _recent_code_failures(conn, account_type, account_id, now):
    """直近24時間に間違えた認証コードの回数（成功した入力は除く）。"""
    return conn.execute(
        "SELECT COALESCE(SUM(attempts), 0) - COUNT(verified_at) FROM verification_codes "
        "WHERE account_type = ? AND account_id = ? AND created_at > ?",
        (account_type, account_id, now - 86400),
    ).fetchone()[0]


def _issue_store_code(conn, store_row, ttl):
    """呼び出し側のトランザクション内で実行する。以前のコードを無効にして新しいコードを返す。"""
    now = _now()
    code = new_code()
    conn.execute(
        "UPDATE verification_codes SET consumed_at = ? WHERE account_type = 'store' AND account_id = ? "
        "AND purpose = 'store_password' AND consumed_at IS NULL",
        (now, store_row["id"]),
    )
    conn.execute(
        "INSERT INTO verification_codes (account_type, account_id, purpose, code_hash, expires_at, created_at) "
        "VALUES ('store', ?, 'store_password', ?, ?, ?)",
        (store_row["id"], keyed_hash(conn.secret_key, code), now + ttl, now),
    )
    return code


def _store_code_mail(store_name, code, minutes, base_url, invite=False):
    intro = (f"「{store_name}」の店舗アカウントが登録されました。\n下の認証コードでメールアドレスを確認し、ログイン用のパスワードを設定してください。"
             if invite else "パスワード設定用の認証コードをお送りします。")
    subject = "【メニューオーダー】店舗アカウントのご案内" if invite else "【メニューオーダー】認証コードのお知らせ"
    body = (
        f"{store_name} ご担当者様\n\n{intro}\n\n"
        f"認証コード: {code}\n"
        f"有効期限: {minutes}分\n"
        f"設定ページ: {base_url}/account\n\n"
        "このコードは誰にも教えないでください。運営者がコードを電話やメールでお尋ねすることはありません。\n"
        "心当たりがない場合は、このメールを破棄してください（パスワードは変更されません）。\n"
    )
    return subject, body


def request_store_code(conn, email, mailer, base_url):
    """アカウントの有無にかかわらず同じ結果を返す。送信できる場合だけメールを送る。"""
    email = normalize_email(email)
    row = conn.execute("SELECT * FROM stores WHERE email = ?", (email,)).fetchone()
    if row is None or row["status"] not in ("invited", "active"):
        log.info("認証コードの要求（送信対象外）: %s", mask_email(email))
        return
    now = _now()
    recent = [r[0] for r in conn.execute(
        "SELECT created_at FROM verification_codes WHERE account_type = 'store' AND account_id = ? "
        "AND purpose = 'store_password' AND created_at > ? ORDER BY id DESC",
        (row["id"], now - 3600),
    )]
    if (recent and recent[0] > now - CODE_RESEND_INTERVAL) or len(recent) >= CODE_HOURLY_LIMIT:
        log.info("認証コードの再送を間隔制限で見送りました: store_id=%s", row["id"])
        return
    if _recent_code_failures(conn, "store", row["id"], now) >= DAILY_CODE_FAILURE_LIMIT:
        log.warning("認証コードの失敗が多いため、24時間はコードを送りません: store_id=%s", row["id"])
        return
    with transaction(conn):
        code = _issue_store_code(conn, row, STORE_CODE_TTL)
        write_audit(conn, "store", "password.code_requested", "stores", row["id"], store_id=row["id"])
    subject, body = _store_code_mail(row["name"], code, STORE_CODE_TTL // 60, base_url)
    mailer.send_in_background(email, subject, body)


def verify_store_code(conn, email, code, mailer=None):
    """正しければ、パスワード設定用のワンタイムトークン（10分有効）を返す。"""
    try:
        email = normalize_email(email)
    except AppError:
        raise code_invalid() from None
    row = conn.execute("SELECT * FROM stores WHERE email = ?", (email,)).fetchone()
    if row is None or row["status"] not in ("invited", "active"):
        raise code_invalid()
    code = code.strip() if isinstance(code, str) else ""
    now = _now()
    token = None
    cap_reached = False
    with transaction(conn):
        entry = conn.execute(
            "SELECT * FROM verification_codes WHERE account_type = 'store' AND account_id = ? AND purpose = 'store_password' "
            "AND consumed_at IS NULL AND verified_at IS NULL ORDER BY id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        if (entry is None or entry["expires_at"] < now or entry["attempts"] >= MAX_CODE_ATTEMPTS
                or _recent_code_failures(conn, "store", row["id"], now) >= DAILY_CODE_FAILURE_LIMIT):
            pass
        elif CODE_RE.match(code) and hmac.compare_digest(entry["code_hash"], keyed_hash(conn.secret_key, code)):
            token = new_token()
            conn.execute(
                "UPDATE verification_codes SET verified_at = ?, challenge_hash = ?, expires_at = ?, attempts = attempts + 1 WHERE id = ?",
                (now, keyed_hash(conn.secret_key, token), now + PASSWORD_TOKEN_TTL, entry["id"]),
            )
            write_audit(conn, "store", "password.code_verified", "stores", row["id"], store_id=row["id"])
        else:
            attempts = entry["attempts"] + 1
            conn.execute(
                "UPDATE verification_codes SET attempts = ?, consumed_at = CASE WHEN ? >= ? THEN ? ELSE consumed_at END WHERE id = ?",
                (attempts, attempts, MAX_CODE_ATTEMPTS, now, entry["id"]),
            )
            write_audit(conn, "store", "password.code_failed", "stores", row["id"], {"attempts": attempts}, store_id=row["id"])
            cap_reached = _recent_code_failures(conn, "store", row["id"], now) == DAILY_CODE_FAILURE_LIMIT
    if cap_reached:
        log.warning("認証コードの失敗がアカウントの上限に達しました: store_id=%s", row["id"])
        if mailer is not None:
            mailer.send_in_background(
                row["email"], "【メニューオーダー】認証コードの入力失敗が続いています",
                f"{row['name']} ご担当者様\n\n認証コードの入力に何度も失敗したため、24時間はパスワードの再設定を受け付けません。\n"
                "ご自身の操作でない場合は、第三者が試している可能性があります。\n"
                "急ぎでパスワードを設定する必要がある場合は、運営者に「再設定メールの送信」を依頼してください。\n",
            )
    if token is None:
        raise code_invalid()
    return token


def set_store_password(conn, token, new_password, sessions, mailer=None):
    """戻り値: パスワードを設定した店舗の行。"""
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        raise token_invalid()
    now = _now()
    entry = conn.execute(
        "SELECT * FROM verification_codes WHERE challenge_hash = ? AND account_type = 'store' AND purpose = 'store_password'",
        (keyed_hash(conn.secret_key, token),),
    ).fetchone()
    if entry is None or entry["verified_at"] is None or entry["consumed_at"] is not None or entry["expires_at"] < now:
        raise token_invalid()
    store = conn.execute("SELECT * FROM stores WHERE id = ?", (entry["account_id"],)).fetchone()
    if store is None or store["status"] not in ("invited", "active"):
        raise token_invalid()
    check_password_policy(new_password, store["email"])
    password_hash = hash_password(new_password)
    with transaction(conn):
        consumed = conn.execute(
            "UPDATE verification_codes SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL AND expires_at >= ?",
            (now, entry["id"], now),
        )
        if consumed.rowcount != 1:
            raise token_invalid()
        updated = conn.execute(
            "UPDATE stores SET password_hash = ?, status = 'active', failed_login_count = 0, locked_until = NULL, "
            "password_changed_at = ?, updated_at = ? WHERE id = ? AND status IN ('invited', 'active')",
            (password_hash, now_iso(), now_iso(), store["id"]),
        )
        if updated.rowcount != 1:
            raise token_invalid()
        conn.execute(
            "UPDATE verification_codes SET consumed_at = ? WHERE account_type = 'store' AND account_id = ? AND consumed_at IS NULL",
            (now, store["id"]),
        )
        write_audit(conn, "store", "password.set", "stores", store["id"], store_id=store["id"])
    sessions.revoke_account("store", store["id"])
    log.info("店舗のパスワードを設定しました: store_id=%s", store["id"])
    if mailer is not None:
        mailer.send_in_background(
            store["email"], "【メニューオーダー】パスワードが設定されました",
            f"{store['name']} ご担当者様\n\n店舗アカウントのパスワードが設定（変更）されました。\n"
            "ほかの端末でのログインは、安全のためすべてログアウトしました。\n\n"
            "ご自身で変更していない場合は、第三者に操作されたおそれがあります。すぐにパスワードを設定し直し、運営者に連絡してください。\n",
        )
    return conn.execute("SELECT * FROM stores WHERE id = ?", (store["id"],)).fetchone()


# ---------- 運営者 ----------

def create_or_reset_operator(conn, email, password):
    """サーバーを操作できる人だけが実行する CLI 用。"""
    email = normalize_email(email)
    check_password_policy(password, email)
    if len(password) < OPERATOR_MIN_PASSWORD_LENGTH:
        raise bad_request("PASSWORD_TOO_SHORT", f"運営者のパスワードは{OPERATOR_MIN_PASSWORD_LENGTH}文字以上にしてください。", "長めの文にしてください。")
    password_hash = hash_password(password)
    ts = now_iso()
    with transaction(conn):
        existing = conn.execute("SELECT id FROM operators WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE operators SET password_hash = ?, is_active = 1, failed_login_count = 0, locked_until = NULL, updated_at = ? WHERE id = ?",
                (password_hash, ts, existing["id"]),
            )
            operator_id, action = existing["id"], "operator.password_reset"
        else:
            operator_id = conn.execute(
                "INSERT INTO operators (email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)", (email, password_hash, ts, ts)
            ).lastrowid
            action = "operator.create"
        write_audit(conn, "cli", action, "operators", operator_id)
    return operator_id


def operator_login(conn, email, password, mailer):
    """パスワードが正しければ認証コードをメールで送り、コード入力用のチャレンジIDを返す（まだログインではない）。"""
    try:
        email = normalize_email(email)
    except AppError:
        burn_password_time(password)
        raise login_failed() from None
    password = _validated_password_input(password)
    row = conn.execute("SELECT * FROM operators WHERE email = ?", (email,)).fetchone()
    if row is None or not row["is_active"]:
        burn_password_time(password)
        raise login_failed()
    if not _check_password(conn, "operators", row["id"], password, "operator"):
        raise login_failed()
    now = _now()
    code, challenge = new_code(), new_token()
    with transaction(conn):
        conn.execute(
            "UPDATE verification_codes SET consumed_at = ? WHERE account_type = 'operator' AND account_id = ? AND consumed_at IS NULL",
            (now, row["id"]),
        )
        conn.execute(
            "INSERT INTO verification_codes (account_type, account_id, purpose, code_hash, challenge_hash, expires_at, created_at) "
            "VALUES ('operator', ?, 'operator_login', ?, ?, ?, ?)",
            (row["id"], keyed_hash(conn.secret_key, code), keyed_hash(conn.secret_key, challenge), now + OPERATOR_CODE_TTL, now),
        )
        write_audit(conn, "operator", "login.code_sent", "operators", row["id"])
    mailer.send_in_background(
        email, "【メニューオーダー】運営者ログインの認証コード",
        f"運営者サイトへのログイン用の認証コードです。\n\n認証コード: {code}\n有効期限: {OPERATOR_CODE_TTL // 60}分\n\n"
        "ログインした覚えがない場合は、パスワードが漏れている可能性があります。すぐにパスワードを変更してください。\n",
    )
    return challenge


def operator_verify(conn, challenge, code):
    if not isinstance(challenge, str) or not TOKEN_RE.match(challenge):
        raise code_invalid()
    code = code.strip() if isinstance(code, str) else ""
    now = _now()
    operator = None
    with transaction(conn):
        entry = conn.execute(
            "SELECT * FROM verification_codes WHERE challenge_hash = ? AND account_type = 'operator' AND purpose = 'operator_login'",
            (keyed_hash(conn.secret_key, challenge),),
        ).fetchone()
        if entry is None or entry["consumed_at"] is not None or entry["expires_at"] < now or entry["attempts"] >= MAX_CODE_ATTEMPTS:
            pass
        elif CODE_RE.match(code) and hmac.compare_digest(entry["code_hash"], keyed_hash(conn.secret_key, code)):
            row = conn.execute("SELECT * FROM operators WHERE id = ? AND is_active = 1", (entry["account_id"],)).fetchone()
            conn.execute("UPDATE verification_codes SET consumed_at = ?, verified_at = ? WHERE id = ?", (now, now, entry["id"]))
            if row is not None:
                conn.execute("UPDATE operators SET last_login_at = ? WHERE id = ?", (now_iso(), row["id"]))
                write_audit(conn, "operator", "login.success", "operators", row["id"])
                operator = row
        else:
            attempts = entry["attempts"] + 1
            conn.execute(
                "UPDATE verification_codes SET attempts = ?, consumed_at = CASE WHEN ? >= ? THEN ? ELSE consumed_at END WHERE id = ?",
                (attempts, attempts, MAX_CODE_ATTEMPTS, now, entry["id"]),
            )
            write_audit(conn, "operator", "login.code_failed", "operators", entry["account_id"], {"attempts": attempts})
    if operator is None:
        raise code_invalid()
    return operator


# ---------- 運営者による店舗管理 ----------

def _store_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "status": row["status"],
        "status_label": STORE_STATUS_LABELS[row["status"]],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
        "password_changed_at": row["password_changed_at"],
        "order_count": row["order_count"],
        "last_order_at": row["last_order_at"],
        "product_count": row["product_count"],
    }


_STORE_LIST_SQL = (
    "SELECT s.id, s.name, s.email, s.status, s.created_at, s.last_login_at, s.password_changed_at, "
    "(SELECT COUNT(*) FROM orders o WHERE o.store_id = s.id AND o.order_status != 'cancelled') AS order_count, "
    "(SELECT MAX(o.ordered_at) FROM orders o WHERE o.store_id = s.id) AS last_order_at, "
    "(SELECT COUNT(*) FROM products p WHERE p.store_id = s.id) AS product_count "
    "FROM stores s"
)


def list_stores(conn):
    return [_store_to_dict(row) for row in conn.execute(_STORE_LIST_SQL + " ORDER BY s.id")]


def get_store(conn, store_id):
    row = conn.execute(_STORE_LIST_SQL + " WHERE s.id = ?", (store_id,)).fetchone()
    if row is None:
        raise not_found("STORE_NOT_FOUND", "店舗が見つかりません。", "一覧を更新してください。")
    return _store_to_dict(row)


def _ensure_email_free(conn, email, exclude_store_id=None):
    row = conn.execute("SELECT id FROM stores WHERE email = ?", (email,)).fetchone()
    if row and row["id"] != exclude_store_id:
        raise conflict("EMAIL_IN_USE", "このメールアドレスはすでに別の店舗で登録されています。", "別のメールアドレスを指定してください。")


def invite_store(conn, payload, mailer, base_url, operator_id):
    if not isinstance(payload, dict):
        raise bad_request("INVALID_REQUEST", "リクエストの形式が正しくありません。", "画面を再読み込みしてください。")
    name = clean_text(payload.get("name"), "店舗名", 40)
    email = normalize_email(payload.get("email"))
    ts = now_iso()
    with transaction(conn):
        _ensure_email_free(conn, email)
        store_id = conn.execute(
            "INSERT INTO stores (name, email, status, created_at, updated_at) VALUES (?, ?, 'invited', ?, ?)", (name, email, ts, ts)
        ).lastrowid
        seed_store(conn, store_id)
        row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
        code = _issue_store_code(conn, row, INVITE_CODE_TTL)
        write_audit(conn, "operator", "store.invite", "stores", store_id, {"operator_id": operator_id, "email": mask_email(email)}, store_id=store_id)
    subject, body = _store_code_mail(name, code, INVITE_CODE_TTL // 60, base_url, invite=True)
    mailer.send_in_background(email, subject, body)
    return get_store(conn, store_id)


def resend_invite(conn, store_id, payload, mailer, base_url, operator_id):
    """メール未設定・招待中の店舗に招待メールを送る。利用中の店舗には、登録済みのアドレスへ再設定メールを送る。

    利用中の店舗への再設定メールは、第三者にロックや認証コードの上限を使い切られた店舗を救済するためのもの。
    ログインのロックと認証コードの失敗回数を解除する。乗っ取りを防ぐため、利用中の店舗のメールアドレスは変更できない。
    """
    payload = payload if isinstance(payload, dict) else {}
    with transaction(conn):
        row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
        if row is None:
            raise not_found("STORE_NOT_FOUND", "店舗が見つかりません。", "一覧を更新してください。")
        if row["status"] == "suspended":
            raise conflict("STORE_SUSPENDED", "利用停止中の店舗には送れません。", "先に利用を再開してください。")
        if row["status"] == "active":
            if payload.get("email") and normalize_email(payload["email"]) != row["email"]:
                raise conflict("EMAIL_LOCKED", "利用中の店舗のメールアドレスは変更できません。",
                               "なりすまし防止のためです。登録済みのアドレスに再設定メールを送ります。")
            now = _now()
            conn.execute("UPDATE stores SET failed_login_count = 0, locked_until = NULL WHERE id = ?", (store_id,))
            conn.execute(
                "UPDATE verification_codes SET attempts = 0 WHERE account_type = 'store' AND account_id = ? AND created_at > ?",
                (store_id, now - 86400),
            )
            code = _issue_store_code(conn, row, INVITE_CODE_TTL)
            write_audit(conn, "operator", "store.reset_sent", "stores", store_id, {"operator_id": operator_id}, store_id=store_id)
            subject, body = _store_code_mail(row["name"], code, INVITE_CODE_TTL // 60, base_url)
            send_to = row["email"]
        else:
            send_to = None
    if send_to:
        mailer.send_in_background(send_to, subject, body)
        return get_store(conn, store_id)
    with transaction(conn):
        row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
        if row is None or row["status"] not in ("unclaimed", "invited"):
            raise conflict("STORE_STATE_CHANGED", "店舗の状態が変わりました。", "一覧を更新してください。")
        email = normalize_email(payload["email"]) if payload.get("email") else row["email"]
        if not email:
            raise bad_request("EMAIL_REQUIRED", "招待先のメールアドレスを入力してください。", "店舗の担当者のメールアドレスを指定してください。")
        _ensure_email_free(conn, email, exclude_store_id=store_id)
        conn.execute("UPDATE stores SET email = ?, status = 'invited', updated_at = ? WHERE id = ?", (email, now_iso(), store_id))
        conn.execute(
            "UPDATE verification_codes SET attempts = 0 WHERE account_type = 'store' AND account_id = ? AND created_at > ?",
            (store_id, _now() - 86400),
        )
        seed_store(conn, store_id)
        row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
        code = _issue_store_code(conn, row, INVITE_CODE_TTL)
        write_audit(conn, "operator", "store.reinvite", "stores", store_id, {"operator_id": operator_id, "email": mask_email(email)}, store_id=store_id)
    subject, body = _store_code_mail(row["name"], code, INVITE_CODE_TTL // 60, base_url, invite=True)
    mailer.send_in_background(email, subject, body)
    return get_store(conn, store_id)


def set_store_status(conn, store_id, payload, sessions, operator_id):
    status = payload.get("status") if isinstance(payload, dict) else None
    if status not in ("suspended", "active"):
        raise bad_request("INVALID_STATUS", "変更できない状態です。", "「利用停止」か「利用再開」を選んでください。")
    with transaction(conn):
        row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
        if row is None:
            raise not_found("STORE_NOT_FOUND", "店舗が見つかりません。", "一覧を更新してください。")
        if status == "suspended":
            if row["status"] == "suspended":
                raise conflict("ALREADY_SUSPENDED", "この店舗はすでに利用停止中です。", "一覧を更新してください。")
            new_status = "suspended"
        else:
            if row["status"] != "suspended":
                raise conflict("NOT_SUSPENDED", "この店舗は利用停止中ではありません。", "一覧を更新してください。")
            new_status = "active" if row["password_hash"] else ("invited" if row["email"] else "unclaimed")
        conn.execute("UPDATE stores SET status = ?, updated_at = ? WHERE id = ?", (new_status, now_iso(), store_id))
        conn.execute(
            "UPDATE verification_codes SET consumed_at = ? WHERE account_type = 'store' AND account_id = ? AND consumed_at IS NULL",
            (_now(), store_id),
        )
        write_audit(conn, "operator", f"store.{'suspend' if status == 'suspended' else 'resume'}", "stores", store_id,
                    {"operator_id": operator_id}, store_id=store_id)
    sessions.revoke_account("store", store_id)
    return get_store(conn, store_id)
