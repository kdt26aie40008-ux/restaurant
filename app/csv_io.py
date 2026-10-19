"""CSV の取り込み（メニュー）と書き出し（メニュー・売上・注文履歴）。

CSV はあくまで入出力用で、正式なデータは SQLite に置く。
取り込みは「検証 → 全件問題なしのときだけ1トランザクションで反映」とし、途中まで反映された状態を作らない。
"""

import csv
import io

from .allergens import parse_allergens, to_text
from .db import transaction, write_audit
from .errors import AppError, bad_request
from .orders import ORDER_STATUSES, PAYMENT_STATUSES
from .timeutil import now_iso, range_bounds
from .validation import FORMULA_PREFIXES, clean_text, parse_tags, strict_int, validate_image_path, validate_product_id

MENU_COLUMNS = [
    "product_id", "category_name", "product_name", "price",
    "image_path", "is_available", "popularity_score", "recommendation_tags",
]
OPTIONAL_MENU_COLUMNS = ["description", "allergens"]
MAX_CSV_BYTES = 1_000_000
MAX_CSV_ROWS = 2000
MAX_REPORTED_ERRORS = 200

SALES_HEADER = ["注文ID", "伝票番号", "テーブル", "注文日時", "商品ID", "商品名", "数量", "注文時単価", "小計", "注文合計", "利用人数", "注文状態", "支払い状態"]
ORDERS_HEADER = SALES_HEADER + ["取消理由"]


# ---------- 書き出し ----------

def sanitize_cell(value):
    """Excel で開いたときに数式として実行されないよう、記号で始まる文字列の先頭に ' を付ける。"""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def to_csv_bytes(header, rows, with_bom):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([sanitize_cell(cell) for cell in row])
    return (("﻿" if with_bom else "") + buffer.getvalue()).encode("utf-8")


def export_menu(conn, store_id, with_bom):
    rows = conn.execute(
        "SELECT p.*, c.name AS category_name FROM products p JOIN categories c ON c.id = p.category_id AND c.store_id = p.store_id "
        "WHERE p.store_id = ? ORDER BY c.sort_order, c.id, p.id",
        (store_id,),
    )
    return to_csv_bytes(
        MENU_COLUMNS + OPTIONAL_MENU_COLUMNS,
        (
            [r["id"], r["category_name"], r["name"], r["price"], r["image_path"], r["is_available"],
             r["popularity_score"], r["recommendation_tags"], r["description"], r["allergens"]]
            for r in rows
        ),
        with_bom,
    )


def _order_item_rows(conn, store_id, date_from, date_to, include_cancelled):
    start, end = range_bounds(date_from, date_to)
    sql = (
        "SELECT o.order_number, v.visit_number, v.table_label, o.ordered_at, o.total_amount, o.party_size, o.order_status, o.payment_status, "
        "o.cancel_reason, i.product_id, i.product_name_snapshot, i.quantity, i.unit_price, i.subtotal "
        "FROM orders o JOIN order_items i ON i.order_id = o.id AND i.store_id = o.store_id "
        "LEFT JOIN visits v ON v.id = o.visit_id AND v.store_id = o.store_id "
        "WHERE o.store_id = ? AND o.ordered_at >= ? AND o.ordered_at < ?"
    )
    if not include_cancelled:
        sql += " AND o.order_status != 'cancelled'"
    sql += " ORDER BY o.ordered_at, o.id, i.id"
    return conn.execute(sql, (store_id, start, end))


def export_sales(conn, store_id, date_from, date_to, with_bom):
    """売上 CSV。取消注文は含めない。"""
    return to_csv_bytes(
        SALES_HEADER,
        (
            [r["order_number"], r["visit_number"] or "", r["table_label"] or "", r["ordered_at"], r["product_id"], r["product_name_snapshot"], r["quantity"], r["unit_price"],
             r["subtotal"], r["total_amount"], r["party_size"], ORDER_STATUSES[r["order_status"]], PAYMENT_STATUSES[r["payment_status"]]]
            for r in _order_item_rows(conn, store_id, date_from, date_to, include_cancelled=False)
        ),
        with_bom,
    )


def export_orders(conn, store_id, date_from, date_to, with_bom):
    """注文履歴 CSV。取消注文も含め、取消理由を出力する。"""
    return to_csv_bytes(
        ORDERS_HEADER,
        (
            [r["order_number"], r["visit_number"] or "", r["table_label"] or "", r["ordered_at"], r["product_id"], r["product_name_snapshot"], r["quantity"], r["unit_price"],
             r["subtotal"], r["total_amount"], r["party_size"], ORDER_STATUSES[r["order_status"]],
             PAYMENT_STATUSES[r["payment_status"]], r["cancel_reason"] or ""]
            for r in _order_item_rows(conn, store_id, date_from, date_to, include_cancelled=True)
        ),
        with_bom,
    )


# ---------- 取り込み ----------

def _decode(data):
    if not data:
        raise bad_request("CSV_EMPTY", "CSVファイルが空です。", "内容のあるCSVファイルを選んでください。")
    if len(data) > MAX_CSV_BYTES:
        raise AppError(413, "CSV_TOO_LARGE", f"CSVファイルが大きすぎます（上限 {MAX_CSV_BYTES // 1000}KB）。", "ファイルを分割して取り込んでください。")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise bad_request(
            "CSV_ENCODING", "CSVファイルの文字コードがUTF-8ではありません。",
            "Excelの場合は「名前を付けて保存」で「CSV UTF-8（コンマ区切り）」を選んで保存し直してください。",
        ) from None
    if "\x00" in text:
        raise bad_request("CSV_BINARY", "CSVではないファイルが選ばれています。", "メニューCSVファイルを選び直してください。")
    return text


def _validate_row(raw, line_number, errors):
    """1行を検証し、問題があれば errors に追記して None を返す。"""
    row_errors = []

    def check(column, func):
        try:
            return func(raw.get(column, ""))
        except AppError as exc:
            row_errors.append({"line": line_number, "column": column, "message": exc.message, "hint": exc.hint})
            return None

    record = {
        "product_id": check("product_id", lambda v: validate_product_id(v.strip())),
        "category_name": check("category_name", lambda v: clean_text(v, "カテゴリ名", 30)),
        "product_name": check("product_name", lambda v: clean_text(v, "商品名", 60)),
        "price": check("price", lambda v: strict_int(v, "価格", 0, 1_000_000, code="INVALID_PRICE")),
        "image_path": check("image_path", validate_image_path),
        "is_available": check("is_available", lambda v: strict_int(v, "販売状態（is_available）", 0, 1)),
        "popularity_score": check("popularity_score", lambda v: strict_int(v, "人気度（popularity_score）", 0, 100)),
        "recommendation_tags": check("recommendation_tags", lambda v: ";".join(parse_tags(v.strip()))),
    }
    if "description" in raw:
        record["description"] = check("description", lambda v: clean_text(v, "説明", 200, required=False))
    if "allergens" in raw:
        record["allergens"] = check("allergens", lambda v: to_text(parse_allergens(v.strip())))
    errors.extend(row_errors)
    return None if row_errors else record


def _add_error(errors, line, column, message, hint=""):
    errors.append({"line": line, "column": column, "message": message, "hint": hint})


def analyze_menu_csv(conn, store_id, data):
    """CSV を検証し、追加・更新件数とエラー一覧を返す。DB は変更しない。"""
    text = _decode(data)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    errors = []
    try:
        header = next(reader, None)
        if header is None:
            raise bad_request("CSV_EMPTY", "CSVファイルが空です。", "1行目に列名を入れてください。")
        header = [name.strip() for name in header]
        missing = [name for name in MENU_COLUMNS if name not in header]
        unknown = [name for name in header if name not in MENU_COLUMNS + OPTIONAL_MENU_COLUMNS]
        duplicated = sorted({name for name in header if header.count(name) > 1})
        if missing or unknown or duplicated:
            details = []
            if missing:
                details.append("不足している列: " + ", ".join(missing))
            if unknown:
                details.append("想定外の列: " + ", ".join(unknown))
            if duplicated:
                details.append("重複している列: " + ", ".join(duplicated))
            raise bad_request(
                "CSV_HEADER", "CSVの1行目（列名）が正しくありません。" + " / ".join(details),
                "サンプルCSV（sample/menu_sample.csv）と同じ列名にしてください。",
            )

        records, data_rows = [], 0
        for raw_row in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in raw_row):
                continue
            data_rows += 1
            if data_rows > MAX_CSV_ROWS:
                raise bad_request("CSV_TOO_MANY_ROWS", f"CSVの行数が多すぎます（上限 {MAX_CSV_ROWS} 行）。", "ファイルを分割してください。")
            if len(raw_row) != len(header):
                _add_error(errors, line, None, f"列の数が一致しません（{len(header)}列のはずが{len(raw_row)}列）。",
                           "値に , を含む場合は \" で囲んでください。")
                continue
            record = _validate_row(dict(zip(header, raw_row)), line, errors)
            if record:
                record["line"] = line
                records.append(record)
    except csv.Error as exc:
        raise bad_request("CSV_FORMAT", f"CSVの形式が正しくありません（{reader.line_num}行目付近）。", "\" の閉じ忘れなどがないか確認してください。") from exc

    if not records and not errors:
        raise bad_request("CSV_NO_ROWS", "取り込む商品の行がありません。", "2行目以降に商品データを入れてください。")

    # ファイル内の重複
    seen_ids, seen_names = {}, {}
    for record in records:
        if record["product_id"] in seen_ids:
            _add_error(errors, record["line"], "product_id", f"商品ID {record['product_id']} が {seen_ids[record['product_id']]} 行目と重複しています。", "1つの商品は1行にまとめてください。")
        seen_ids.setdefault(record["product_id"], record["line"])
        if record["product_name"] in seen_names:
            _add_error(errors, record["line"], "product_name", f"商品名「{record['product_name']}」が {seen_names[record['product_name']]} 行目と重複しています。", "商品名を変えてください。")
        seen_names.setdefault(record["product_name"], record["line"])

    # カテゴリの存在
    categories = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM categories WHERE store_id = ?", (store_id,))}
    for record in records:
        if record["category_name"] not in categories:
            _add_error(errors, record["line"], "category_name", f"カテゴリ「{record['category_name']}」は登録されていません。",
                       "管理画面の「カテゴリ」で先に追加するか、既存のカテゴリ名に直してください。")
        record["category_id"] = categories.get(record["category_name"])

    # 既存商品との衝突（取り込み後に別IDの商品と名前が重なるもの）
    existing = {row["id"]: row for row in conn.execute("SELECT * FROM products WHERE store_id = ?", (store_id,))}
    final_names = {pid: row["name"] for pid, row in existing.items()}
    final_names.update({r["product_id"]: r["product_name"] for r in records})
    owners = {}
    for pid, name in final_names.items():
        owners.setdefault(name, []).append(pid)
    for record in records:
        others = [pid for pid in owners.get(record["product_name"], []) if pid != record["product_id"] and pid in existing and pid not in seen_ids]
        if others:
            _add_error(errors, record["line"], "product_name", f"商品名「{record['product_name']}」は既存の商品（ID: {others[0]}）と重複しています。",
                       "商品名を変えるか、既存商品のIDで更新してください。")

    added, updated, unchanged, preview = 0, 0, 0, []
    for record in records:
        before = existing.get(record["product_id"])
        if before is None:
            action = "add"
            added += 1
        elif _differs(before, record):
            action = "update"
            updated += 1
        else:
            action = "unchanged"
            unchanged += 1
        if len(preview) < 100:
            preview.append({"line": record["line"], "action": action, "product_id": record["product_id"],
                            "product_name": record["product_name"], "category_name": record["category_name"],
                            "price": record["price"], "is_available": record["is_available"]})

    errors.sort(key=lambda e: (e["line"] or 0))
    return {
        "valid": not errors,
        "summary": {"rows": data_rows, "add": added, "update": updated, "unchanged": unchanged, "error_count": len(errors)},
        "errors": errors[:MAX_REPORTED_ERRORS],
        "preview": preview,
        "records": records,
    }


def _differs(before, record):
    pairs = [
        ("category_id", "category_id"), ("name", "product_name"), ("price", "price"), ("image_path", "image_path"),
        ("is_available", "is_available"), ("popularity_score", "popularity_score"), ("recommendation_tags", "recommendation_tags"),
    ]
    for optional in OPTIONAL_MENU_COLUMNS:
        if optional in record:
            pairs.append((optional, optional))
    return any(before[db_key] != record[csv_key] for db_key, csv_key in pairs)


def public_result(analysis):
    return {key: value for key, value in analysis.items() if key != "records"}


def preview_menu_csv(conn, store_id, data):
    return public_result(analyze_menu_csv(conn, store_id, data))


def apply_menu_csv(conn, store_id, data, actor="store"):
    """検証と反映を同じトランザクション内で行う。1件でも問題があれば何も反映しない。"""
    with transaction(conn):
        analysis = analyze_menu_csv(conn, store_id, data)
        if not analysis["valid"]:
            raise bad_request("CSV_INVALID", "CSVに問題があるため取り込みを中止しました。データは変更していません。",
                              "エラー一覧を確認してCSVを修正してください。", public_result(analysis))
        ts = now_iso()
        for r in analysis["records"]:
            description = r.get("description")
            allergens = r.get("allergens")
            conn.execute(
                "INSERT INTO products (store_id, id, category_id, name, description, price, image_path, is_available, popularity_score, "
                "recommendation_tags, allergens, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(store_id, id) DO UPDATE SET category_id = excluded.category_id, name = excluded.name, "
                "description = CASE WHEN ? THEN excluded.description ELSE products.description END, "
                "allergens = CASE WHEN ? THEN excluded.allergens ELSE products.allergens END, "
                "price = excluded.price, image_path = excluded.image_path, is_available = excluded.is_available, "
                "popularity_score = excluded.popularity_score, recommendation_tags = excluded.recommendation_tags, "
                "updated_at = excluded.updated_at",
                (store_id, r["product_id"], r["category_id"], r["product_name"], description or "", r["price"], r["image_path"],
                 r["is_available"], r["popularity_score"], r["recommendation_tags"], allergens or "", ts, ts,
                 int(description is not None), int(allergens is not None)),
            )
        write_audit(conn, actor, "menu.import", "products", None, analysis["summary"], store_id=store_id)
    return public_result(analysis)
