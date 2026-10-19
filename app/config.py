"""パスと起動設定。DB・ログ・バックアップはソースや公開フォルダと分けて data/ 以下に置く。"""

import os
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class Config:
    def __init__(self, data_dir=None, host=None, port=None):
        raw_data_dir = data_dir or os.environ.get("RESTAURANT_DATA_DIR") or (BASE_DIR / "data")
        self.data_dir = Path(raw_data_dir).expanduser().resolve()
        self.db_path = self.data_dir / "restaurant.db"
        self.log_dir = self.data_dir / "logs"
        self.default_backup_dir = self.data_dir / "backups"
        self.host = host or os.environ.get("RESTAURANT_HOST", "127.0.0.1")
        self.port = int(port if port is not None else os.environ.get("RESTAURANT_PORT", "8000"))
        self.secret_key_path = self.data_dir / "secret.key"
        self.secret_key_env = os.environ.get("RESTAURANT_SECRET_KEY")
        self.public_base_url = os.environ.get("RESTAURANT_PUBLIC_URL", "").rstrip("/") or f"http://localhost:{self.port}"
        # HTTPS で公開する場合は 1 にして Cookie に Secure を付ける。
        self.secure_cookies = os.environ.get("RESTAURANT_SECURE_COOKIES") == "1"
        # 運営者サイトは既定でサーバー本体（127.0.0.1）からしか使えない。
        self.operator_allow_remote = os.environ.get("RESTAURANT_OPERATOR_ALLOW_REMOTE") == "1"
        self.backup_dir_env = os.environ.get("RESTAURANT_BACKUP_DIR", "")
        # お試し用「ログインなしモード」。このPC（localhost）からの接続だけを受け付け、最初の店舗として操作する。
        self.no_login = os.environ.get("RESTAURANT_NO_LOGIN") == "1"
        # 受け付ける Host（DNS リバインディング対策）。LAN の IP アドレスで使う場合は RESTAURANT_ALLOWED_HOSTS に追加する。
        self.allowed_hosts = {"localhost", "127.0.0.1", "::1", urlsplit(self.public_base_url).hostname or "localhost"}
        if self.host not in ("0.0.0.0", "::", ""):
            self.allowed_hosts.add(self.host.lower())
        self.allowed_hosts |= {h.strip().lower() for h in os.environ.get("RESTAURANT_ALLOWED_HOSTS", "").split(",") if h.strip()}
        self.smtp_host = os.environ.get("RESTAURANT_SMTP_HOST", "")
        self.smtp_port = int(os.environ.get("RESTAURANT_SMTP_PORT", "587"))
        self.smtp_user = os.environ.get("RESTAURANT_SMTP_USER", "")
        self.smtp_password = os.environ.get("RESTAURANT_SMTP_PASSWORD", "")
        self.mail_from = os.environ.get("RESTAURANT_MAIL_FROM", "")
        self.smtp_security = os.environ.get("RESTAURANT_SMTP_SECURITY", "starttls")
        if self.smtp_security not in ("starttls", "ssl"):
            raise ValueError("RESTAURANT_SMTP_SECURITY は starttls か ssl を指定してください（暗号化なしの送信はできません）。")

        if self.data_dir == STATIC_DIR or STATIC_DIR in self.data_dir.parents:
            raise ValueError("データフォルダを Web 公開フォルダ（static/）の中に置くことはできません。")

    def ensure_dirs(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
