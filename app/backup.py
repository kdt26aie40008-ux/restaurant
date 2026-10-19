"""SQLite のバックアップと復元。

SQLite のオンラインバックアップ API を使うため、サーバー起動中でも整合性のとれたコピーが作れる。
復元は「バックアップフォルダ内の、命名規則に合い、検証に通ったファイル」から「設定された DB」へだけ行う。
"""

import logging
import os
import re
import sqlite3
from pathlib import Path
from urllib.request import pathname2url

from .config import STATIC_DIR
from .db import REQUIRED_TABLES, connect, migrate, open_db, write_audit, transaction
from .errors import AppError, bad_request, not_found
from .timeutil import now

log = logging.getLogger(__name__)

BACKUP_NAME_RE = re.compile(r"^restaurant-\d{8}-\d{6}(?:-\d{2})?(?:-before-restore)?\.db$")


def check_backup_dir_allowed(raw):
    if raw.startswith(("\\\\", "//")) or Path(raw).drive.startswith(("\\\\", "//")):
        # ネットワーク共有を指定させると、接続時に認証情報が送られたり、細工した DB から復元させられたりするおそれがある。
        raise bad_request("BACKUP_DIR_NETWORK", "ネットワーク上のフォルダはバックアップ先に指定できません。",
                          "このPCのドライブ（例: D:\\restaurant-backup）や USB メモリを指定してください。")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise bad_request("BACKUP_DIR_RELATIVE", "バックアップ先は絶対パス（例: D:\\restaurant-backup）で指定してください。", "空欄にすると data/backups に保存します。")
    resolved = path.resolve()
    if resolved.drive.startswith(("\\\\", "//")) or _is_network_drive(resolved):
        raise bad_request("BACKUP_DIR_NETWORK", "ネットワーク上のフォルダはバックアップ先に指定できません。",
                          "このPCのドライブや USB メモリを指定してください。")
    if resolved == STATIC_DIR or STATIC_DIR in resolved.parents:
        raise bad_request("BACKUP_DIR_PUBLIC", "Web公開フォルダ（static）の中にはバックアップを保存できません。", "別のフォルダを指定してください。")
    return resolved


def _is_network_drive(path):
    """Windows の割り当て済みネットワークドライブ（例: Z:）かどうか。"""
    if os.name != "nt" or not path.drive or len(path.drive) != 2:
        return False
    import ctypes

    DRIVE_REMOTE = 4
    return ctypes.windll.kernel32.GetDriveTypeW(path.drive + "\\") == DRIVE_REMOTE


def resolve_backup_dir(config, setting_value):
    return check_backup_dir_allowed(setting_value) if setting_value else config.default_backup_dir


def _readonly_connect(path):
    return sqlite3.connect("file:" + pathname2url(str(path)) + "?mode=ro", uri=True)


def verify_database_file(path):
    """戻り値: (問題なしか, 理由)。"""
    try:
        conn = _readonly_connect(path)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                return False, f"整合性チェックに失敗しました（{result}）"
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return False, f"SQLiteのデータベースとして読み込めません（{exc}）"
    missing = REQUIRED_TABLES - tables
    if missing:
        return False, "このシステムのデータベースではありません（不足テーブル: " + ", ".join(sorted(missing)) + "）"
    return True, ""


def create_backup(db_path, backup_dir, suffix=""):
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = now().strftime("%Y%m%d-%H%M%S")
        target = backup_dir / f"restaurant-{stamp}{suffix}.db"
        counter = 1
        while target.exists():
            target = backup_dir / f"restaurant-{stamp}-{counter:02d}{suffix}.db"
            counter += 1
        temporary = backup_dir / (target.name + ".tmp")
        source = connect(db_path)
        destination = sqlite3.connect(str(temporary))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        ok, reason = verify_database_file(temporary)
        if not ok:
            temporary.unlink(missing_ok=True)
            raise AppError(500, "BACKUP_VERIFY_FAILED", "バックアップファイルの検証に失敗しました。", f"もう一度実行してください。（{reason}）")
        temporary.replace(target)
    except (OSError, sqlite3.Error) as exc:
        log.exception("バックアップの書き込みに失敗しました: %s", backup_dir)
        raise AppError(500, "BACKUP_WRITE_FAILED", "バックアップファイルを保存できませんでした。",
                       "バックアップ先のフォルダが存在し、書き込みできるか（USBメモリが挿さっているか、空き容量があるか）確認してください。") from exc
    log.info("バックアップを作成しました: %s", target)
    return target


def list_backups(backup_dir):
    if not backup_dir.exists():
        return []
    files = [p for p in backup_dir.iterdir() if p.is_file() and BACKUP_NAME_RE.match(p.name)]
    files.sort(key=lambda p: p.name, reverse=True)
    return [{"filename": p.name, "size_bytes": p.stat().st_size} for p in files]


def restore_backup(db_path, backup_dir, filename, actor="admin"):
    if not isinstance(filename, str) or not BACKUP_NAME_RE.match(filename):
        raise bad_request("BACKUP_NAME_INVALID", "復元するファイル名が正しくありません。", "バックアップ一覧から選んでください。")
    source_path = (backup_dir / filename).resolve()
    if source_path.parent != backup_dir.resolve() or not source_path.is_file():
        raise not_found("BACKUP_NOT_FOUND", "指定したバックアップファイルが見つかりません。", "バックアップ一覧を更新して選び直してください。")
    if source_path == Path(db_path).resolve():
        raise bad_request("BACKUP_IS_CURRENT_DB", "使用中のデータベースそのものは復元元にできません。", "バックアップ一覧から選んでください。")
    ok, reason = verify_database_file(source_path)
    if not ok:
        raise bad_request("BACKUP_INVALID", "このバックアップファイルは復元に使えません。", reason)

    safety_copy = create_backup(db_path, backup_dir, suffix="-before-restore")
    source = _readonly_connect(source_path)
    destination = connect(db_path)
    try:
        source.backup(destination)
    except sqlite3.DatabaseError as exc:
        log.exception("復元に失敗しました: %s", source_path)
        raise AppError(500, "RESTORE_FAILED", "復元中にエラーが発生しました。",
                       f"復元前のデータは {safety_copy.name} に保存されています。このファイルから復元し直してください。") from exc
    finally:
        destination.close()
        source.close()

    with open_db(db_path) as conn:
        migrate(conn)
        with transaction(conn):
            write_audit(conn, actor, "backup.restore", "database", None, {"from": filename, "safety_copy": safety_copy.name})
    log.warning("バックアップから復元しました: %s（復元前のデータ: %s）", filename, safety_copy.name)
    return {"restored_from": filename, "safety_copy": safety_copy.name}
