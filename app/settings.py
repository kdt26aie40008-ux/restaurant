"""設定。店舗ごとの設定（store_settings）と、運営者用の全体設定（settings）を分けて持つ。"""

from .db import transaction, write_audit
from .errors import bad_request, not_found
from .timeutil import now_iso
from .validation import clean_text, strict_int

STORE_DEFAULTS = {"max_party_size": "30", "csv_bom_default": "1"}
PLATFORM_DEFAULTS = {"backup_dir": ""}


# ---------- 店舗ごとの設定 ----------

def ensure_store_defaults(conn, store_id):
    ts = now_iso()
    for key, value in STORE_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO store_settings (store_id, key, value, updated_at) VALUES (?, ?, ?, ?)",
            (store_id, key, value, ts),
        )


def get_store_settings(conn, store_id):
    store = conn.execute("SELECT name FROM stores WHERE id = ?", (store_id,)).fetchone()
    if store is None:
        raise not_found("STORE_NOT_FOUND", "店舗が見つかりません。", "もう一度ログインしてください。")
    values = dict(STORE_DEFAULTS)
    for row in conn.execute("SELECT key, value FROM store_settings WHERE store_id = ?", (store_id,)):
        if row["key"] in STORE_DEFAULTS:
            values[row["key"]] = row["value"]
    values["store_name"] = store["name"]
    return values


def get_max_party_size(conn, store_id):
    return int(get_store_settings(conn, store_id)["max_party_size"])


def public_settings(conn, store_id):
    values = get_store_settings(conn, store_id)
    return {"store_name": values["store_name"], "max_party_size": int(values["max_party_size"])}


def validate_store_setting(key, value):
    if key == "store_name":
        return clean_text(value, "店舗名", 40)
    if key == "max_party_size":
        return str(strict_int(value, "最大人数", 1, 1000))
    if key == "csv_bom_default":
        if value in (True, "1", 1):
            return "1"
        if value in (False, "0", 0):
            return "0"
        raise bad_request("INVALID_SETTING", "CSVのBOM設定が正しくありません。", "オン・オフのどちらかを選んでください。")
    raise bad_request("UNKNOWN_SETTING", "変更できない設定項目が含まれています。", "画面を再読み込みしてください。")


def update_store_settings(conn, store_id, changes, actor="store"):
    if not isinstance(changes, dict) or not changes:
        raise bad_request("INVALID_SETTINGS", "変更する設定がありません。", "設定値を入力してから保存してください。")
    validated = {key: validate_store_setting(key, value) for key, value in changes.items()}
    with transaction(conn):
        ts = now_iso()
        for key, value in validated.items():
            if key == "store_name":
                conn.execute("UPDATE stores SET name = ?, updated_at = ? WHERE id = ?", (value, ts, store_id))
                continue
            conn.execute(
                "INSERT INTO store_settings (store_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(store_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (store_id, key, value, ts),
            )
        write_audit(conn, actor, "settings.update", "settings", None, validated, store_id=store_id)
    return get_store_settings(conn, store_id)


# ---------- 運営者用の全体設定 ----------

def ensure_platform_defaults(conn):
    ts = now_iso()
    for key, value in PLATFORM_DEFAULTS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)", (key, value, ts))


def get_platform_settings(conn):
    values = dict(PLATFORM_DEFAULTS)
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] in PLATFORM_DEFAULTS:
            values[row["key"]] = row["value"]
    return values


def update_platform_settings(conn, changes, actor="operator"):
    if not isinstance(changes, dict) or set(changes) - set(PLATFORM_DEFAULTS) or not changes:
        raise bad_request("INVALID_SETTINGS", "変更できない設定項目が含まれています。", "画面を再読み込みしてください。")
    backup_dir = clean_text(changes.get("backup_dir", ""), "バックアップ先", 260, required=False, forbid_formula=False)
    if backup_dir:
        from .backup import check_backup_dir_allowed

        check_backup_dir_allowed(backup_dir)
    with transaction(conn):
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('backup_dir', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (backup_dir, now_iso()),
        )
        write_audit(conn, actor, "platform_settings.update", "settings", None, {"backup_dir": backup_dir})
    return get_platform_settings(conn)
