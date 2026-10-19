"""起動・管理用コマンド。

    py run.py                      サーバーを起動
    py run.py create-operator      運営者アカウントを作成（同じメールなら再設定）
    py run.py backup               DB をバックアップ
    py run.py list-backups         バックアップ一覧
    py run.py restore <ファイル名>   バックアップから復元（サーバー停止中に実行）
"""

import argparse
import getpass
import logging
import sys
from logging.handlers import RotatingFileHandler

from app import accounts, backup, settings
from app.config import Config
from app.db import DatabaseCorruptedError, init_database, open_db
from app.errors import AppError
from app.mailer import Mailer
from app.server import OrderServer


def setup_logging(config):
    config.ensure_dirs()
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    file_handler = RotatingFileHandler(config.log_dir / "app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)


def command_serve(config):
    if config.no_login and config.host not in ("127.0.0.1", "localhost"):
        logging.error("ログインなしモード（RESTAURANT_NO_LOGIN=1）は、RESTAURANT_HOST が 127.0.0.1 のときだけ使えます。起動を中止しました。")
        return
    server = OrderServer(config)
    if config.no_login:
        logging.warning("ログインなしモードです。このPCからは誰でもログインせずに全機能を使えます。お試し専用です。")
    host = "localhost" if config.host in ("127.0.0.1", "localhost") else config.host
    logging.info("サーバーを起動しました: http://%s:%s/ （店舗管理: /admin  運営者: /operator）停止するには Ctrl+C", host, config.port)
    if config.host not in ("127.0.0.1", "localhost"):
        logging.warning("同じネットワーク上の他の端末からも接続できる設定です（RESTAURANT_HOST=%s）。", config.host)
    if not Mailer(config).configured:
        logging.warning("メール送信（SMTP）が未設定のため開発モードです。認証コードはこの画面にだけ表示されます。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("停止しています…")
    finally:
        server.server_close()
        logging.info("サーバーを停止しました。")


def command_create_operator(config):
    email = input("運営者のメールアドレス（ログイン時の認証コードの送り先）: ").strip()
    password = getpass.getpass(f"パスワード（{accounts.OPERATOR_MIN_PASSWORD_LENGTH}文字以上）: ")
    if getpass.getpass("もう一度入力してください: ") != password:
        print("入力したパスワードが一致しません。", file=sys.stderr)
        return 1
    with open_db(config.db_path) as conn:
        accounts.create_or_reset_operator(conn, email, password)
    print("運営者アカウントを設定しました。サーバー本体のブラウザで http://localhost:%s/operator を開いてください。" % config.port)
    return 0


def _backup_dir(config):
    with open_db(config.db_path) as conn:
        configured = settings.get_platform_settings(conn)["backup_dir"] or config.backup_dir_env
        return backup.resolve_backup_dir(config, configured)


def main(argv=None):
    parser = argparse.ArgumentParser(description="メニューオーダーシステム")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve")
    sub.add_parser("create-operator")
    sub.add_parser("backup")
    sub.add_parser("list-backups")
    restore = sub.add_parser("restore")
    restore.add_argument("filename")
    args = parser.parse_args(argv)

    config = Config()
    setup_logging(config)
    try:
        init_database(config.db_path)
    except DatabaseCorruptedError:
        logging.error("データベースが壊れている可能性があるため起動を中止しました。README の「復元方法」を参照してください。")
        return 2

    try:
        if args.command in (None, "serve"):
            command_serve(config)
        elif args.command == "create-operator":
            return command_create_operator(config)
        elif args.command == "backup":
            print("バックアップを作成しました:", backup.create_backup(config.db_path, _backup_dir(config)))
        elif args.command == "list-backups":
            for item in backup.list_backups(_backup_dir(config)):
                print(item["filename"], f"{item['size_bytes']:,} bytes")
        elif args.command == "restore":
            answer = input(f"{args.filename} から復元します。サーバーは停止していますか？ 続ける場合は「復元」と入力: ")
            if answer.strip() != "復元":
                print("中止しました。")
                return 1
            result = backup.restore_backup(config.db_path, _backup_dir(config), args.filename, actor="cli")
            print(f"復元しました。復元前のデータは {result['safety_copy']} に保存しました。")
    except AppError as exc:
        print(f"エラー: {exc.message} {exc.hint}", file=sys.stderr)
        return 1
    except OSError as exc:
        logging.exception("起動または処理に失敗しました")
        print(f"エラー: {exc}（ポートが使用中の場合は RESTAURANT_PORT で別の番号を指定してください）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
