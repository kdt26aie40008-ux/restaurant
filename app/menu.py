"""カテゴリと商品の参照・管理。すべての操作は店舗ID（store_id）で絞り込む。"""

from .allergens import ALLERGENS, parse_allergens, to_text
from .db import transaction, write_audit
from .errors import bad_request, conflict, not_found
from .timeutil import now_iso
from .validation import clean_text, parse_tags, strict_bool, strict_int, validate_image_path


def category_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "sort_order": row["sort_order"],
        "is_active": bool(row["is_active"]),
    }


def product_to_dict(row):
    return {
        "id": row["id"],
        "category_id": row["category_id"],
        "category_name": row["category_name"],
        "category_active": bool(row["category_active"]),
        "name": row["name"],
        "description": row["description"],
        "price": row["price"],
        "image_path": row["image_path"],
        "is_available": bool(row["is_available"]),
        "popularity_score": row["popularity_score"],
        "tags": [t for t in row["recommendation_tags"].split(";") if t],
        "allergens": [a for a in row["allergens"].split(";") if a in ALLERGENS],
    }


def list_categories(conn, store_id, include_inactive=False):
    sql = "SELECT * FROM categories WHERE store_id = ?"
    if not include_inactive:
        sql += " AND is_active = 1"
    sql += " ORDER BY sort_order, id"
    return [category_to_dict(row) for row in conn.execute(sql, (store_id,))]


_PRODUCT_SELECT = (
    "SELECT p.*, c.name AS category_name, c.is_active AS category_active "
    "FROM products p JOIN categories c ON c.id = p.category_id AND c.store_id = p.store_id"
)


def list_products(conn, store_id, category_id=None, include_inactive_categories=False):
    """販売停止の商品も「販売停止」と表示するために返す。非表示カテゴリの商品は既定で除く。"""
    where, params = ["p.store_id = ?"], [store_id]
    if not include_inactive_categories:
        where.append("c.is_active = 1")
    if category_id is not None:
        where.append("p.category_id = ?")
        params.append(category_id)
    sql = _PRODUCT_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY c.sort_order, c.id, p.id"
    return [product_to_dict(row) for row in conn.execute(sql, params)]


def get_product(conn, store_id, product_id):
    row = conn.execute(_PRODUCT_SELECT + " WHERE p.store_id = ? AND p.id = ?", (store_id, product_id)).fetchone()
    if row is None:
        raise not_found("PRODUCT_NOT_FOUND", "商品が見つかりません。", "画面を再読み込みしてください。")
    return product_to_dict(row)


def _get_category_row(conn, store_id, category_id):
    return conn.execute("SELECT * FROM categories WHERE id = ? AND store_id = ?", (category_id, store_id)).fetchone()


def _ensure_category_name_free(conn, store_id, name, exclude_id=None):
    row = conn.execute("SELECT id FROM categories WHERE store_id = ? AND name = ?", (store_id, name)).fetchone()
    if row and row["id"] != exclude_id:
        raise conflict("CATEGORY_NAME_EXISTS", f"カテゴリ「{name}」はすでに存在します。", "別の名前にしてください。")


def create_category(conn, store_id, payload, actor="store"):
    if not isinstance(payload, dict):
        raise bad_request("INVALID_REQUEST", "リクエストの形式が正しくありません。", "画面を再読み込みしてください。")
    name = clean_text(payload.get("name"), "カテゴリ名", 30)
    sort_order = strict_int(payload.get("sort_order", 0), "並び順", 0, 100000)
    with transaction(conn):
        if conn.execute("SELECT COUNT(*) FROM categories WHERE store_id = ?", (store_id,)).fetchone()[0] >= 100:
            raise bad_request("TOO_MANY_CATEGORIES", "カテゴリは100件までです。", "使っていないカテゴリを整理してください。")
        _ensure_category_name_free(conn, store_id, name)
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO categories (store_id, name, sort_order, is_active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
            (store_id, name, sort_order, ts, ts),
        )
        write_audit(conn, actor, "category.create", "category", cur.lastrowid, {"name": name, "sort_order": sort_order}, store_id=store_id)
    return category_to_dict(_get_category_row(conn, store_id, cur.lastrowid))


def update_category(conn, store_id, category_id, payload, actor="store"):
    if not isinstance(payload, dict) or not payload:
        raise bad_request("INVALID_REQUEST", "変更内容がありません。", "変更する項目を入力してください。")
    changes = {}
    if "name" in payload:
        changes["name"] = clean_text(payload["name"], "カテゴリ名", 30)
    if "sort_order" in payload:
        changes["sort_order"] = strict_int(payload["sort_order"], "並び順", 0, 100000)
    if "is_active" in payload:
        changes["is_active"] = int(strict_bool(payload["is_active"], "表示状態"))
    if not changes:
        raise bad_request("INVALID_REQUEST", "変更できる項目がありません。", "名前・並び順・表示状態のいずれかを指定してください。")
    with transaction(conn):
        if _get_category_row(conn, store_id, category_id) is None:
            raise not_found("CATEGORY_NOT_FOUND", "カテゴリが見つかりません。", "画面を再読み込みしてください。")
        if "name" in changes:
            _ensure_category_name_free(conn, store_id, changes["name"], exclude_id=category_id)
        assignments = ", ".join(f"{column} = ?" for column in changes)  # 列名は上で固定した集合のみ
        conn.execute(
            f"UPDATE categories SET {assignments}, updated_at = ? WHERE id = ? AND store_id = ?",
            (*changes.values(), now_iso(), category_id, store_id),
        )
        write_audit(conn, actor, "category.update", "category", category_id, changes, store_id=store_id)
    return category_to_dict(_get_category_row(conn, store_id, category_id))


def update_product(conn, store_id, product_id, payload, actor="store"):
    """価格を変えても order_items のスナップショットは変わらないため、過去の注文金額には影響しない。"""
    if not isinstance(payload, dict) or not payload:
        raise bad_request("INVALID_REQUEST", "変更内容がありません。", "変更する項目を入力してください。")
    changes = {}
    if "name" in payload:
        changes["name"] = clean_text(payload["name"], "商品名", 60)
    if "description" in payload:
        changes["description"] = clean_text(payload["description"], "説明", 200, required=False)
    if "price" in payload:
        changes["price"] = strict_int(payload["price"], "価格", 0, 1_000_000, code="INVALID_PRICE")
    if "is_available" in payload:
        changes["is_available"] = int(strict_bool(payload["is_available"], "販売状態"))
    if "popularity_score" in payload:
        changes["popularity_score"] = strict_int(payload["popularity_score"], "人気度", 0, 100)
    if "recommendation_tags" in payload:
        changes["recommendation_tags"] = ";".join(parse_tags(payload["recommendation_tags"]))
    if "image_path" in payload:
        changes["image_path"] = validate_image_path(payload["image_path"])
    if "category_id" in payload:
        changes["category_id"] = strict_int(payload["category_id"], "カテゴリ", 1, 2**31)
    if "allergens" in payload:
        changes["allergens"] = to_text(parse_allergens(payload["allergens"]))
    if not changes:
        raise bad_request("INVALID_REQUEST", "変更できる項目がありません。", "変更する項目を指定してください。")

    with transaction(conn):
        before = conn.execute("SELECT * FROM products WHERE store_id = ? AND id = ?", (store_id, product_id)).fetchone()
        if before is None:
            raise not_found("PRODUCT_NOT_FOUND", "商品が見つかりません。", "画面を再読み込みしてください。")
        if "category_id" in changes and _get_category_row(conn, store_id, changes["category_id"]) is None:
            raise bad_request("CATEGORY_NOT_FOUND", "指定したカテゴリが存在しません。", "カテゴリを選び直してください。")
        if "name" in changes:
            other = conn.execute(
                "SELECT id FROM products WHERE store_id = ? AND name = ? AND id != ?", (store_id, changes["name"], product_id)
            ).fetchone()
            if other:
                raise conflict("PRODUCT_NAME_EXISTS", f"商品名「{changes['name']}」は商品ID {other['id']} で使われています。", "別の商品名にしてください。")
        assignments = ", ".join(f"{column} = ?" for column in changes)  # 列名は上で固定した集合のみ
        conn.execute(
            f"UPDATE products SET {assignments}, updated_at = ? WHERE store_id = ? AND id = ?",
            (*changes.values(), now_iso(), store_id, product_id),
        )
        write_audit(
            conn, actor, "product.update", "product", product_id,
            {key: {"before": before[key], "after": value} for key, value in changes.items()}, store_id=store_id,
        )
    return get_product(conn, store_id, product_id)
