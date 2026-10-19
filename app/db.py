"""SQLite 接続・トランザクション・マイグレーション・操作ログ。"""

import json
import logging
import re
import sqlite3
from contextlib import contextmanager

from .config import MIGRATIONS_DIR
from .timeutil import now_iso

log = logging.getLogger(__name__)

MIGRATION_FILE_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
REQUIRED_TABLES = {
    "schema_migrations", "stores", "operators", "verification_codes", "categories", "products",
    "orders", "order_items", "settings", "store_settings", "audit_logs", "visits",
}


class DatabaseCorruptedError(Exception):
    pass


class AppConnection(sqlite3.Connection):
    """認証コードの HMAC に使う秘密鍵を接続に持たせるための Connection。"""

    secret_key = None


def connect(db_path, secret_key=None):
    # isolation_level=None で暗黙のトランザクションを無効にし、transaction() で明示的に管理する。
    conn = sqlite3.connect(str(db_path), timeout=10, isolation_level=None, factory=AppConnection)
    conn.row_factory = sqlite3.Row
    conn.secret_key = secret_key
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def open_db(db_path, secret_key=None):
    conn = connect(db_path, secret_key)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn):
    """BEGIN IMMEDIATE で書き込みロックを先に取り、例外時は必ずロールバックする。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    try:
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def write_audit(conn, actor, action, target_type=None, target_id=None, detail=None, store_id=None):
    conn.execute(
        "INSERT INTO audit_logs (occurred_at, actor, action, target_type, target_id, detail, store_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            now_iso(),
            actor,
            action,
            target_type,
            None if target_id is None else str(target_id),
            None if detail is None else json.dumps(detail, ensure_ascii=False),
            store_id,
        ),
    )


def _split_statements(sql):
    statements, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            statements.append(buf.strip())
            buf = ""
    leftover = "\n".join(l for l in buf.splitlines() if l.strip() and not l.strip().startswith("--"))
    if leftover:
        raise ValueError("マイグレーションSQLの末尾が不完全です（; が不足しています）。")
    return statements


def migrate(conn, target_version=None):
    """migrations/NNNN_*.sql のうち未適用のものを番号順に1ファイル1トランザクションで適用する。

    テーブルの作り直しができるよう、適用中は外部キー検査を止め、コミット前に foreign_key_check で整合性を確かめる。
    target_version を指定すると、その番号までで止める（マイグレーションのテスト用）。
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
    newly_applied = []
    for path in sorted(MIGRATIONS_DIR.iterdir()):
        match = MIGRATION_FILE_RE.match(path.name)
        if not match or match.group(1) in applied:
            continue
        if target_version is not None and match.group(1) > target_version:
            break
        statements = _split_statements(path.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with transaction(conn):
                for statement in statements:
                    conn.execute(statement)
                problems = conn.execute("PRAGMA foreign_key_check").fetchall()
                if problems:
                    raise sqlite3.IntegrityError(f"マイグレーション {path.name} 後に外部キーの不整合があります: {[tuple(p) for p in problems[:5]]}")
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (match.group(1), path.name, now_iso()),
                )
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
        log.info("マイグレーションを適用しました: %s", path.name)
        newly_applied.append(path.name)
    return newly_applied


def check_integrity(conn):
    result = conn.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        raise DatabaseCorruptedError(f"SQLite quick_check の結果: {result}")


def init_database(db_path):
    """起動時に呼ぶ。整合性確認 → マイグレーション → 運営設定の既定値。店舗のサンプルデータは店舗の招待時に入れる。"""
    from .settings import ensure_platform_defaults

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with open_db(db_path) as conn:
        try:
            check_integrity(conn)
        except sqlite3.DatabaseError as exc:
            log.exception("データベースファイルを読み込めません: %s", db_path)
            raise DatabaseCorruptedError(str(exc)) from exc
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
        from .seed import upgrade_sample_menu

        with transaction(conn):
            ensure_platform_defaults(conn)
            for row in conn.execute("SELECT id FROM stores").fetchall():
                added = upgrade_sample_menu(conn, row["id"])
                if added:
                    log.info("サンプルメニューを追加しました: store_id=%s %s品", row["id"], added)
