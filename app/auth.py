"""パスワード・認証コード・ログインセッション・試行回数制限。

- パスワードは PBKDF2-SHA256（60万回）＋ランダムソルトでハッシュ化し、平文はどこにも保存しない。
- 認証コードやワンタイムトークンは、DB の外にある秘密鍵による HMAC だけを保存する
  （DB やバックアップが漏れても、桁数の少ないコードを総当たりで復元できないようにするため）。
- セッショントークンはサーバーのメモリに SHA-256 のハッシュだけを保持する。
"""

import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path

from .errors import bad_request

PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128
CODE_DIGITS = 8

# よく使われる推測しやすいパスワード（小文字で比較）。長さ条件を満たすものだけ載せている。
COMMON_PASSWORDS = {
    "1234567890", "12345678910", "0123456789", "1111111111", "0000000000", "1234512345", "9876543210",
    "qwertyuiop", "asdfghjkl;", "1q2w3e4r5t", "1qaz2wsx3edc", "qwerty1234", "qwerty12345", "password1",
    "password12", "password123", "password1234", "passw0rd123", "iloveyou12", "abcdefghij", "abcd123456",
    "abc1234567", "letmein123", "welcome123", "admin12345", "administrator", "restaurant", "restaurant1",
    "restaurant123", "changeme123", "sunshine123", "princess123", "football123", "baseball123",
}


# ---------- パスワード ----------

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    if not isinstance(password, str) or not stored or len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


_DUMMY_HASH = None
_DUMMY_LOCK = threading.Lock()


def burn_password_time(password):
    """存在しないアカウントでも同じ時間がかかるようにし、応答時間からアカウントの有無を推測させない。"""
    global _DUMMY_HASH
    with _DUMMY_LOCK:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password(secrets.token_hex(16))
    verify_password(password if isinstance(password, str) and len(password) <= MAX_PASSWORD_LENGTH else "x", _DUMMY_HASH)


def check_password_policy(password, email=None):
    hint = f"{MIN_PASSWORD_LENGTH}〜{MAX_PASSWORD_LENGTH}文字で、推測されにくいものにしてください（例: 単語をいくつか組み合わせた長めの文）。"
    if not isinstance(password, str):
        raise bad_request("PASSWORD_INVALID", "パスワードの形式が正しくありません。", hint)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise bad_request("PASSWORD_TOO_SHORT", f"パスワードが短すぎます（{MIN_PASSWORD_LENGTH}文字以上）。", hint)
    if len(password) > MAX_PASSWORD_LENGTH:
        raise bad_request("PASSWORD_TOO_LONG", f"パスワードが長すぎます（{MAX_PASSWORD_LENGTH}文字まで）。", hint)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in password):
        raise bad_request("PASSWORD_INVALID", "パスワードに改行などの制御文字は使えません。", hint)
    lowered = password.lower()
    if len(set(password)) < 4 or lowered in COMMON_PASSWORDS:
        raise bad_request("PASSWORD_WEAK", "推測されやすいパスワードです。", hint)
    if email:
        local = email.split("@", 1)[0].lower()
        if (len(local) >= 4 and local in lowered) or email.lower() in lowered:
            raise bad_request("PASSWORD_WEAK", "メールアドレスを含むパスワードは使えません。", hint)
    return password


# ---------- 認証コード・トークン ----------

def new_code():
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def new_token():
    return secrets.token_urlsafe(32)


def credential_tag(password_hash):
    """パスワードのハッシュから作る短い識別子。セッションに持たせ、パスワードが変わったら（別プロセスでの変更でも）無効にする。"""
    return hashlib.sha256((password_hash or "").encode("utf-8")).hexdigest()[:32]


def keyed_hash(secret_key, value):
    return hmac.new(secret_key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def load_or_create_secret_key(path, env_value=None):
    """認証コードの HMAC 用の秘密鍵。環境変数があればそれを使い、なければ data/secret.key を作る。"""
    if env_value:
        if len(env_value) < 32:
            raise ValueError("RESTAURANT_SECRET_KEY は32文字以上にしてください。")
        return env_value.encode("utf-8")
    path = Path(path)
    if path.exists():
        key = bytes.fromhex(path.read_text(encoding="ascii").strip())
        if len(key) < 32:
            raise ValueError(f"{path} の秘密鍵が短すぎます。")
        return key
    key = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(key.hex())
    return key


# ---------- セッション ----------

SESSION_POLICIES = {
    # 種類: (無操作で切れるまでの秒数, ログインからの最長秒数)
    "store": (12 * 3600, 24 * 3600),
    "operator": (30 * 60, 8 * 3600),
}
ELEVATED_SECONDS = 15 * 60
MAX_REAUTH_FAILURES = 5


class SessionStore:
    """サーバーのメモリ上だけに持つログインセッション。再起動するとログアウトされる。"""

    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, kind, account_id, store_id=None, elevated=False, tag=None):
        token = new_token()
        now = time.time()
        with self._lock:
            self._purge(now)
            self._sessions[self._key(token)] = {
                "kind": kind,
                "account_id": account_id,
                "store_id": store_id,
                "created": now,
                "last_seen": now,
                "elevated_until": now + ELEVATED_SECONDS if elevated else 0,
                "tag": tag,
                "reauth_failures": 0,
            }
        return token

    def get(self, token, kind):
        if not token or not isinstance(token, str) or len(token) > 200:
            return None
        now = time.time()
        with self._lock:
            session = self._sessions.get(self._key(token))
            if session is None or session["kind"] != kind:
                return None
            idle, absolute = SESSION_POLICIES[kind]
            if now - session["last_seen"] > idle or now - session["created"] > absolute:
                del self._sessions[self._key(token)]
                return None
            session["last_seen"] = now
            return dict(session)

    def elevate(self, token):
        with self._lock:
            session = self._sessions.get(self._key(token))
            if session:
                session["elevated_until"] = time.time() + ELEVATED_SECONDS

    def record_reauth_failure(self, token):
        """パスワード再確認の失敗を数え、上限に達したらそのログインを無効にする。無効にしたら True。"""
        with self._lock:
            session = self._sessions.get(self._key(token))
            if session is None:
                return True
            session["reauth_failures"] += 1
            if session["reauth_failures"] >= MAX_REAUTH_FAILURES:
                del self._sessions[self._key(token)]
                return True
            return False

    def revoke(self, token):
        if token:
            with self._lock:
                self._sessions.pop(self._key(token), None)

    def revoke_account(self, kind, account_id):
        with self._lock:
            for key in [k for k, s in self._sessions.items() if s["kind"] == kind and s["account_id"] == account_id]:
                del self._sessions[key]

    def revoke_all(self):
        with self._lock:
            self._sessions.clear()

    def _purge(self, now):
        for key in [k for k, s in self._sessions.items()
                    if now - s["last_seen"] > SESSION_POLICIES[s["kind"]][0]
                    or now - s["created"] > SESSION_POLICIES[s["kind"]][1]]:
            del self._sessions[key]


# ---------- 試行回数制限 ----------

class RateLimiter:
    """接続元ごとの回数制限（スライディングウィンドウ）。アカウント単位のロックは DB 側で行う。"""

    def __init__(self):
        self._hits = {}
        self._lock = threading.Lock()

    def hit(self, key, limit, window_seconds):
        """記録して、上限を超えていれば待つべき秒数を返す（0 なら許可）。"""
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < window_seconds]
            if len(hits) >= limit:
                self._hits[key] = hits
                return int(window_seconds - (now - hits[0])) + 1
            hits.append(now)
            self._hits[key] = hits
            if len(self._hits) > 10000:
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < 3600}
            return 0

    def count(self, key, window_seconds):
        now = time.monotonic()
        with self._lock:
            return len([t for t in self._hits.get(key, []) if now - t < window_seconds])

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)
