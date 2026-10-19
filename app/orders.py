"""注文の確定・参照・会計・状態管理。金額は常に DB の注文時スナップショットから計算する。

すべての操作は店舗ID（store_id）で絞り込み、他の店舗の注文は「存在しない」ものとして扱う。
画面や API では、全店舗共通の内部ID（orders.id）ではなく店舗ごとの注文番号（order_number）で注文を指す
（内部IDを見せると、連番から他店舗の注文数が推測できてしまうため）。
"""

import hashlib
import json
import logging
import re
import sqlite3

from .db import transaction, write_audit
from .errors import AppError, bad_request, conflict, not_found
from .timeutil import now_iso, range_bounds
from .validation import clean_text, strict_int, validate_party_size, validate_product_id

log = logging.getLogger(__name__)

ORDER_STATUSES = {"received": "受付", "cooking": "調理中", "served": "提供済み", "cancelled": "取消"}
PAYMENT_STATUSES = {"unpaid": "未会計", "checked": "会計確認済み", "paid": "支払い済み", "cancelled": "取消"}
ORDER_STATUS_NEXT = {"received": "cooking", "cooking": "served"}
MAX_LINES = 50
MAX_QUANTITY = 99
REQUEST_KEY_RE = re.compile(r"^[A-Za-z0-9-]{16,64}$")
ORDER_NUMBER_RE = re.compile(r"^[0-9]{8}-[0-9]{4,6}$")
ROUNDING_RULE = "1人あたりの参考金額は1円未満を切り捨てて表示しています。"


# ---------- 入力検証 ----------

def validate_order_request(payload, max_party_size):
    if not isinstance(payload, dict):
        raise bad_request("INVALID_REQUEST", "注文データの形式が正しくありません。", "画面を再読み込みしてからやり直してください。")
    request_key = payload.get("request_key")
    if not isinstance(request_key, str) or not REQUEST_KEY_RE.match(request_key):
        raise bad_request("INVALID_REQUEST_KEY", "注文の識別情報が正しくありません。", "画面を再読み込みしてからやり直してください。")
    visit_ref = payload.get("visit_id")
    if visit_ref is not None and not isinstance(visit_ref, str):
        raise bad_request("INVALID_VISIT", "伝票の指定が正しくありません。", "注文履歴から伝票を選び直してください。")
    # 追加注文では人数・テーブル番号は伝票のものを使う。
    party_size = None if visit_ref else validate_party_size(payload.get("party_size"), max_party_size)
    table_label = "" if visit_ref else clean_text(payload.get("table_label") or "", "テーブル番号", 20, required=False)

    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise bad_request("EMPTY_ORDER", "注文する商品がありません。", "メニューから商品を選んでから確認してください。")
    if len(items) > MAX_LINES:
        raise bad_request("TOO_MANY_LINES", f"1回の注文で選べる商品は{MAX_LINES}種類までです。", "注文を分けてください。")
    merged = {}
    for item in items:
        if not isinstance(item, dict):
            raise bad_request("INVALID_ITEM", "注文商品の形式が正しくありません。", "画面を再読み込みしてからやり直してください。")
        product_id = validate_product_id(item.get("product_id"))
        quantity = strict_int(item.get("quantity"), "数量", 1, MAX_QUANTITY, code="INVALID_QUANTITY")
        if product_id in merged:
            raise bad_request("DUPLICATE_ITEM", "同じ商品が重複しています。", "画面を再読み込みしてからやり直してください。")
        merged[product_id] = quantity

    expected_total = payload.get("expected_total")
    if expected_total is not None:
        expected_total = strict_int(expected_total, "画面の合計金額", 0, 10**9)
    return {"request_key": request_key, "party_size": party_size, "table_label": table_label, "visit_ref": visit_ref,
            "items": merged, "expected_total": expected_total}


def _fingerprint(request):
    canonical = json.dumps({"party_size": request["party_size"], "table_label": request["table_label"],
                            "visit": request["visit_ref"], "items": sorted(request["items"].items())}, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------- 注文確定 ----------

def _price_lines(conn, store_id, items):
    """現在の DB の商品情報で単価・小計を決める。画面から送られた金額は使わない。"""
    ids = list(items)
    placeholders = ",".join("?" * len(ids))
    rows = {
        row["id"]: row
        for row in conn.execute(
            "SELECT p.id, p.name, p.price, p.is_available, c.is_active AS category_active "
            "FROM products p JOIN categories c ON c.id = p.category_id AND c.store_id = p.store_id "
            f"WHERE p.store_id = ? AND p.id IN ({placeholders})",
            [store_id, *ids],
        )
    }
    missing = [pid for pid in ids if pid not in rows]
    if missing:
        raise conflict(
            "PRODUCT_NOT_FOUND", "メニューに存在しない商品が含まれています。",
            "メニューを再読み込みし、カートの内容を確認してください。", {"product_ids": missing},
        )
    unavailable = [rows[pid] for pid in ids if not rows[pid]["is_available"] or not rows[pid]["category_active"]]
    if unavailable:
        names = "、".join(f"「{row['name']}」" for row in unavailable)
        raise conflict(
            "PRODUCT_UNAVAILABLE", f"{names}は販売停止になりました。",
            "カートから削除してから、もう一度注文を確定してください。",
            {"product_ids": [row["id"] for row in unavailable]},
        )
    return [
        {
            "product_id": pid,
            "product_name_snapshot": rows[pid]["name"],
            "unit_price": rows[pid]["price"],
            "quantity": quantity,
            "subtotal": rows[pid]["price"] * quantity,
        }
        for pid, quantity in items.items()
    ]


def _next_order_number(conn, store_id, ordered_at):
    prefix = ordered_at[:10].replace("-", "")
    row = conn.execute(
        "SELECT order_number FROM orders WHERE store_id = ? AND order_number LIKE ? ORDER BY order_number DESC LIMIT 1",
        (store_id, prefix + "-%"),
    ).fetchone()
    sequence = int(row["order_number"].split("-")[1]) + 1 if row else 1
    return f"{prefix}-{sequence:04d}"


def _insert_items(conn, store_id, order_id, lines):
    conn.executemany(
        "INSERT INTO order_items (order_id, store_id, product_id, product_name_snapshot, unit_price, quantity, subtotal) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(order_id, store_id, l["product_id"], l["product_name_snapshot"], l["unit_price"], l["quantity"], l["subtotal"]) for l in lines],
    )


def _find_by_request_key(conn, store_id, request_key):
    return conn.execute(
        "SELECT id, request_fingerprint FROM orders WHERE store_id = ? AND request_key = ?", (store_id, request_key)
    ).fetchone()


def _same_request_or_conflict(row, fingerprint):
    if row["request_fingerprint"] != fingerprint:
        raise conflict(
            "REQUEST_KEY_REUSED", "この注文はすでに別の内容で確定されています。",
            "注文履歴で確定済みの注文を確認してください。",
        )
    return row["id"]


def create_order(conn, store_id, payload, max_party_size):
    """注文と注文明細を1トランザクションで保存する。

    同じ request_key の再送（連打・再読み込み・通信のやり直し）には、新規登録せず既存の注文を返す。
    戻り値: (注文の dict, 新規作成したか)
    """
    from . import visits

    request = validate_order_request(payload, max_party_size)
    fingerprint = _fingerprint(request)
    try:
        with transaction(conn):
            existing = _find_by_request_key(conn, store_id, request["request_key"])
            if existing:
                return _get_order_by_id(conn, store_id, _same_request_or_conflict(existing, fingerprint)), False

            lines = _price_lines(conn, store_id, request["items"])
            total = sum(line["subtotal"] for line in lines)
            if request["expected_total"] is not None and request["expected_total"] != total:
                raise conflict(
                    "PRICE_CHANGED", "商品の価格が変更されたため、合計金額が変わりました。",
                    "最新の価格で注文内容を確認し直してから、もう一度確定してください。", {"current_total": total},
                )
            ts = now_iso()
            if request["visit_ref"]:
                visit = visits.open_visit_for_additional_order(conn, store_id, request["visit_ref"])
            else:
                visit = visits.create_visit(conn, store_id, request["party_size"], request["table_label"], ts)
            cursor = conn.execute(
                "INSERT INTO orders (store_id, visit_id, order_number, request_key, request_fingerprint, ordered_at, party_size, total_amount, "
                "order_status, payment_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', 'unpaid', ?, ?)",
                (store_id, visit["id"], _next_order_number(conn, store_id, ts), request["request_key"], fingerprint, ts,
                 visit["party_size"], total, ts, ts),
            )
            order_id = cursor.lastrowid
            _insert_items(conn, store_id, order_id, lines)
            saved_total = conn.execute(
                "SELECT COALESCE(SUM(subtotal), 0) FROM order_items WHERE order_id = ?", (order_id,)
            ).fetchone()[0]
            if saved_total != total:
                raise AppError(500, "TOTAL_MISMATCH", "注文金額の計算に不整合が見つかったため保存を中止しました。", "もう一度注文を確定してください。")
            write_audit(conn, "store", "order.create", "order", order_id,
                        {"total_amount": total, "visit": visit["visit_number"], "additional": bool(request["visit_ref"])},
                        store_id=store_id)
    except sqlite3.IntegrityError:
        # 別スレッドが同じ request_key を先に保存した場合は、その注文を返す。
        existing = _find_by_request_key(conn, store_id, request["request_key"])
        if existing is None:
            raise
        return _get_order_by_id(conn, store_id, _same_request_or_conflict(existing, fingerprint)), False
    log.info("注文を保存しました: store_id=%s order_id=%s total=%s", store_id, order_id, total)
    return _get_order_by_id(conn, store_id, order_id), True


# ---------- 参照 ----------

_ORDER_SELECT = (
    "SELECT o.*, v.visit_number, v.table_label FROM orders o LEFT JOIN visits v ON v.id = o.visit_id AND v.store_id = o.store_id"
)


def _order_to_dict(row, items):
    return {
        "id": row["order_number"],
        "order_number": row["order_number"],
        "visit_id": row["visit_number"],
        "table_label": row["table_label"] or "",
        "ordered_at": row["ordered_at"],
        "party_size": row["party_size"],
        "total_amount": row["total_amount"],
        "order_status": row["order_status"],
        "order_status_label": ORDER_STATUSES[row["order_status"]],
        "payment_status": row["payment_status"],
        "payment_status_label": PAYMENT_STATUSES[row["payment_status"]],
        "checked_at": row["checked_at"],
        "paid_at": row["paid_at"],
        "cancelled_at": row["cancelled_at"],
        "cancel_reason": row["cancel_reason"],
        "next_order_status": ORDER_STATUS_NEXT.get(row["order_status"]),
        "items": [
            {
                "product_id": item["product_id"],
                "product_name": item["product_name_snapshot"],
                "unit_price": item["unit_price"],
                "quantity": item["quantity"],
                "subtotal": item["subtotal"],
            }
            for item in items
        ],
    }


def _order_not_found():
    return not_found("ORDER_NOT_FOUND", "注文が見つかりません。", "注文番号を確認し、注文履歴から選び直してください。")


def _order_row(conn, store_id, order_number):
    if not isinstance(order_number, str) or not ORDER_NUMBER_RE.match(order_number):
        raise _order_not_found()
    row = conn.execute(_ORDER_SELECT + " WHERE o.store_id = ? AND o.order_number = ?", (store_id, order_number)).fetchone()
    if row is None:
        raise _order_not_found()
    return row


def _with_items(conn, store_id, row):
    items = conn.execute("SELECT * FROM order_items WHERE order_id = ? AND store_id = ? ORDER BY id", (row["id"], store_id)).fetchall()
    return _order_to_dict(row, items)


def _get_order_by_id(conn, store_id, order_id):
    row = conn.execute(_ORDER_SELECT + " WHERE o.id = ? AND o.store_id = ?", (order_id, store_id)).fetchone()
    if row is None:
        raise _order_not_found()
    return _with_items(conn, store_id, row)


def orders_with_items(conn, store_id, rows):
    """注文の行のリストを、明細つきの dict のリストにする（明細はまとめて1回で取得）。"""
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    items_by_order = {order_id: [] for order_id in ids}
    placeholders = ",".join("?" * len(ids))
    for item in conn.execute(
        f"SELECT * FROM order_items WHERE store_id = ? AND order_id IN ({placeholders}) ORDER BY id", [store_id, *ids]
    ):
        items_by_order[item["order_id"]].append(item)
    return [_order_to_dict(row, items_by_order[row["id"]]) for row in rows]


def get_order(conn, store_id, order_number):
    return _with_items(conn, store_id, _order_row(conn, store_id, order_number))


def list_orders(conn, store_id, date_from=None, date_to=None, keyword=None, limit=300):
    where, params = ["o.store_id = ?"], [store_id]
    if date_from is not None:
        start, end = range_bounds(date_from, date_to or date_from)
        where.append("o.ordered_at >= ? AND o.ordered_at < ?")
        params += [start, end]
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("o.order_number LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")
    sql = _ORDER_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY o.ordered_at DESC, o.id DESC LIMIT ?"
    return orders_with_items(conn, store_id, conn.execute(sql, (*params, limit)).fetchall())


# ---------- 会計 ----------

def split_amount(total, party_size):
    """割り勘の参考金額。人数が正の整数でなければ除算しない。"""
    if isinstance(party_size, bool) or not isinstance(party_size, int) or party_size <= 0:
        raise AppError(500, "INVALID_PARTY_SIZE", "利用人数が正しくないため、1人あたりの金額を計算できません。", "注文履歴で利用人数を確認してください。")
    per_person = total // party_size
    remainder = total - per_person * party_size
    return {
        "per_person": per_person,
        "remainder": remainder,
        "per_person_rounded_up": per_person + (1 if remainder else 0),
        "rule": ROUNDING_RULE + (f"端数の{remainder}円はどなたかの分に加えて調整してください。" if remainder else "端数はありません。"),
    }


def _recalculate_total(conn, store_id, order_id):
    total = 0
    for item in conn.execute(
        "SELECT unit_price, quantity, subtotal FROM order_items WHERE order_id = ? AND store_id = ?", (order_id, store_id)
    ):
        if item["unit_price"] * item["quantity"] != item["subtotal"]:
            raise AppError(500, "TOTAL_MISMATCH", "注文明細の金額に不整合があります。", "会計を中止し、運営者に連絡してください。")
        total += item["subtotal"]
    return total


def _conditional_update(conn, store_id, order_id, sql, params):
    """sql の WHERE には必ず「id = ? AND store_id = ?」を含めること。"""
    cursor = conn.execute(sql, params)
    if cursor.rowcount == 1:
        return True
    if conn.execute("SELECT 1 FROM orders WHERE id = ? AND store_id = ?", (order_id, store_id)).fetchone() is None:
        raise _order_not_found()
    return False


def advance_order_status(conn, store_id, order_number, payload):
    """受付 → 調理中 → 提供済み の順にだけ進める。current_status が一致しないと拒否する（他端末との競合防止）。"""
    if not isinstance(payload, dict):
        raise bad_request("INVALID_REQUEST", "リクエストの形式が正しくありません。", "画面を再読み込みしてください。")
    current_status, new_status = payload.get("current_status"), payload.get("new_status")
    if not isinstance(current_status, str) or ORDER_STATUS_NEXT.get(current_status) != new_status:
        raise bad_request("INVALID_STATUS_TRANSITION", "その状態には変更できません。", "受付 → 調理中 → 提供済み の順に進めてください。")
    with transaction(conn):
        order_id = _order_row(conn, store_id, order_number)["id"]
        ts = now_iso()
        updated = _conditional_update(
            conn, store_id, order_id,
            "UPDATE orders SET order_status = ?, updated_at = ? WHERE id = ? AND store_id = ? AND order_status = ?",
            (new_status, ts, order_id, store_id, current_status),
        )
        if not updated:
            raise conflict("STATUS_CHANGED", "ほかの操作で注文の状態が変わっています。", "画面を更新して最新の状態を確認してください。")
        write_audit(conn, "store", "order.status", "order", order_id, {"from": current_status, "to": new_status}, store_id=store_id)
    return _get_order_by_id(conn, store_id, order_id)


def cancel_order(conn, store_id, order_number, payload):
    """注文は削除せず、取消状態と理由・操作ログを残す。支払い済みの取消は仮版では不可。"""
    if not isinstance(payload, dict):
        raise bad_request("INVALID_REQUEST", "リクエストの形式が正しくありません。", "画面を再読み込みしてください。")
    reason = clean_text(payload.get("reason"), "取消理由", 200)
    with transaction(conn):
        order_id = _order_row(conn, store_id, order_number)["id"]
        ts = now_iso()
        updated = _conditional_update(
            conn, store_id, order_id,
            "UPDATE orders SET order_status = 'cancelled', payment_status = 'cancelled', cancelled_at = ?, "
            "cancel_reason = ?, updated_at = ? WHERE id = ? AND store_id = ? AND order_status != 'cancelled' AND payment_status != 'paid'",
            (ts, reason, ts, order_id, store_id),
        )
        if not updated:
            current = _get_order_by_id(conn, store_id, order_id)
            if current["payment_status"] == "paid":
                raise conflict("PAID_ORDER_LOCKED", "支払い済みの注文は取消できません。", "仮版では会計後の変更に対応していません。返金などは店舗の手順で対応してください。")
            raise conflict("ALREADY_CANCELLED", "この注文はすでに取消されています。", "画面を更新してください。")
        write_audit(conn, "store", "order.cancel", "order", order_id, {"reason": reason}, store_id=store_id)
        from . import visits

        visits.reset_check_after_change(conn, store_id, order_id)
    return _get_order_by_id(conn, store_id, order_id)


def sales_summary(conn, store_id, date_from, date_to):
    """取消注文は売上に含めない。"""
    start, end = range_bounds(date_from, date_to)
    row = conn.execute(
        "SELECT "
        "  COUNT(CASE WHEN order_status != 'cancelled' THEN 1 END) AS order_count, "
        "  COALESCE(SUM(CASE WHEN order_status != 'cancelled' THEN total_amount END), 0) AS sales_total, "
        "  COALESCE(SUM(CASE WHEN payment_status = 'paid' THEN total_amount END), 0) AS paid_total, "
        "  COUNT(CASE WHEN order_status != 'cancelled' AND payment_status != 'paid' THEN 1 END) AS unpaid_count, "
        "  COUNT(CASE WHEN order_status = 'cancelled' THEN 1 END) AS cancelled_count "
        "FROM orders WHERE store_id = ? AND ordered_at >= ? AND ordered_at < ?",
        (store_id, start, end),
    ).fetchone()
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "order_count": row["order_count"],
        "sales_total": row["sales_total"],
        "paid_total": row["paid_total"],
        "unpaid_count": row["unpaid_count"],
        "cancelled_count": row["cancelled_count"],
        "note": "売上合計は取消注文を除いた注文合計です。支払い済み合計は実際に代金を受け取った記録のある注文だけを集計しています。",
    }
