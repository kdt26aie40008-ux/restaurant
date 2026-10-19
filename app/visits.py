"""伝票（1組のお客様の来店）。

最初の注文を確定したときに伝票を作り、追加注文は同じ伝票にまとめる。会計は伝票単位で、
取消以外のすべての注文の合計を DB に保存された注文時の単価・数量から計算する。
画面・API では伝票番号（例: V20261019-001）を id として使う。
"""

import re

from .db import transaction, write_audit
from .errors import AppError, bad_request, conflict, not_found
from .orders import _ORDER_SELECT, _recalculate_total, orders_with_items, split_amount
from .timeutil import now_iso, range_bounds
from .validation import strict_int

VISIT_NUMBER_RE = re.compile(r"^V[0-9]{8}-[0-9]{3,5}$")
PAYMENT_LABELS = {"unpaid": "未会計", "checked": "会計確認済み", "paid": "会計済み"}
STATUS_FILTERS = ("all", "unpaid", "paid")


def _visit_not_found():
    return not_found("VISIT_NOT_FOUND", "伝票が見つかりません。", "注文履歴から選び直してください。")


def _next_visit_number(conn, store_id, ts):
    prefix = "V" + ts[:10].replace("-", "")
    row = conn.execute(
        "SELECT visit_number FROM visits WHERE store_id = ? AND visit_number LIKE ? "
        "ORDER BY length(visit_number) DESC, visit_number DESC LIMIT 1",
        (store_id, prefix + "-%"),
    ).fetchone()
    sequence = int(row["visit_number"].split("-")[1]) + 1 if row else 1
    return f"{prefix}-{sequence:03d}"


def _visit_row(conn, store_id, visit_number):
    if not isinstance(visit_number, str) or not VISIT_NUMBER_RE.match(visit_number):
        raise _visit_not_found()
    row = conn.execute("SELECT * FROM visits WHERE store_id = ? AND visit_number = ?", (store_id, visit_number)).fetchone()
    if row is None:
        raise _visit_not_found()
    return row


# ---------- 注文確定から呼ばれる（呼び出し側のトランザクション内） ----------

def create_visit(conn, store_id, party_size, table_label, ts):
    cursor = conn.execute(
        "INSERT INTO visits (store_id, visit_number, table_label, party_size, payment_status, opened_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'unpaid', ?, ?, ?)",
        (store_id, _next_visit_number(conn, store_id, ts), table_label, party_size, ts, ts, ts),
    )
    return conn.execute("SELECT * FROM visits WHERE id = ?", (cursor.lastrowid,)).fetchone()


def _reopen_if_checked(conn, row):
    """会計確認後に金額が変わったら、会計確認をやり直してもらうため未会計に戻す。"""
    if row["payment_status"] != "checked":
        return
    ts = now_iso()
    conn.execute("UPDATE visits SET payment_status = 'unpaid', checked_at = NULL, updated_at = ? WHERE id = ?", (ts, row["id"]))
    conn.execute(
        "UPDATE orders SET payment_status = 'unpaid', checked_at = NULL, updated_at = ? WHERE visit_id = ? AND payment_status = 'checked'",
        (ts, row["id"]),
    )
    write_audit(conn, "store", "visit.check_reset", "visit", row["id"], store_id=row["store_id"])


def open_visit_for_additional_order(conn, store_id, visit_number):
    row = _visit_row(conn, store_id, visit_number)
    if row["payment_status"] == "paid":
        raise conflict("VISIT_CLOSED", "この伝票は会計済みのため、追加注文できません。", "新しいお客様として「最初の画面へ」から受付してください。")
    _reopen_if_checked(conn, row)
    return row


def reset_check_after_change(conn, store_id, order_id):
    row = conn.execute(
        "SELECT v.* FROM visits v JOIN orders o ON o.visit_id = v.id WHERE o.id = ? AND o.store_id = ? AND v.store_id = ?",
        (order_id, store_id, store_id),
    ).fetchone()
    if row is not None:
        _reopen_if_checked(conn, row)


# ---------- 参照 ----------

def _orders_by_visit(conn, store_id, visit_ids):
    if not visit_ids:
        return {}
    placeholders = ",".join("?" * len(visit_ids))
    rows = conn.execute(
        _ORDER_SELECT + f" WHERE o.store_id = ? AND o.visit_id IN ({placeholders}) ORDER BY o.ordered_at, o.id",
        [store_id, *visit_ids],
    ).fetchall()
    grouped = {visit_id: [] for visit_id in visit_ids}
    for row, order in zip(rows, orders_with_items(conn, store_id, rows)):
        grouped[row["visit_id"]].append(order)
    return grouped


def _visit_to_dict(row, orders):
    active = [o for o in orders if o["order_status"] != "cancelled"]
    summary = {}
    for order in active:
        for item in order["items"]:
            key = (item["product_id"], item["unit_price"])
            entry = summary.setdefault(key, {"product_id": item["product_id"], "product_name": item["product_name"],
                                             "unit_price": item["unit_price"], "quantity": 0, "subtotal": 0})
            entry["quantity"] += item["quantity"]
            entry["subtotal"] += item["subtotal"]
    waiting = [o for o in active if o["order_status"] != "served"]
    return {
        "id": row["visit_number"],
        "visit_number": row["visit_number"],
        "table_label": row["table_label"],
        "party_size": row["party_size"],
        "payment_status": row["payment_status"],
        "payment_status_label": PAYMENT_LABELS[row["payment_status"]],
        "opened_at": row["opened_at"],
        "last_ordered_at": orders[-1]["ordered_at"] if orders else row["opened_at"],
        "checked_at": row["checked_at"],
        "paid_at": row["paid_at"],
        "order_count": len(orders),
        "active_order_count": len(active),
        "waiting_order_count": len(waiting),
        "total": sum(o["total_amount"] for o in active),
        "item_count": sum(entry["quantity"] for entry in summary.values()),
        "items_summary": list(summary.values()),
        "orders": orders,
        "can_add_order": row["payment_status"] != "paid",
    }


def get_visit(conn, store_id, visit_number):
    row = _visit_row(conn, store_id, visit_number)
    return _visit_to_dict(row, _orders_by_visit(conn, store_id, [row["id"]])[row["id"]])


def list_visits(conn, store_id, date_from, date_to, keyword=None, status="all", limit=200):
    if status not in STATUS_FILTERS:
        raise bad_request("INVALID_FILTER", "絞り込みの指定が正しくありません。", "画面を再読み込みしてください。")
    start, end = range_bounds(date_from, date_to)
    where, params = ["v.store_id = ?", "v.opened_at >= ? AND v.opened_at < ?"], [store_id, start, end]
    if status == "unpaid":
        where.append("v.payment_status != 'paid'")
    elif status == "paid":
        where.append("v.payment_status = 'paid'")
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        where.append(
            "(v.visit_number LIKE ? ESCAPE '\\' OR v.table_label LIKE ? ESCAPE '\\' OR EXISTS ("
            "SELECT 1 FROM orders o WHERE o.visit_id = v.id AND o.store_id = v.store_id AND o.order_number LIKE ? ESCAPE '\\'))"
        )
        params += [like, like, like]
    rows = conn.execute(
        "SELECT v.* FROM visits v WHERE " + " AND ".join(where) + " ORDER BY v.opened_at DESC, v.id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    grouped = _orders_by_visit(conn, store_id, [row["id"] for row in rows])
    return [_visit_to_dict(row, grouped[row["id"]]) for row in rows]


# ---------- 会計 ----------

def _verified_total(conn, store_id, visit):
    """注文ごとに明細から再計算し、保存されている合計と一致することを確かめる。"""
    total = 0
    for order in visit["orders"]:
        if order["order_status"] == "cancelled":
            continue
        order_id = conn.execute("SELECT id FROM orders WHERE store_id = ? AND order_number = ?", (store_id, order["order_number"])).fetchone()["id"]
        recalculated = _recalculate_total(conn, store_id, order_id)
        if recalculated != order["total_amount"]:
            raise AppError(500, "TOTAL_MISMATCH", "注文データの合計金額に不整合があります。", "会計を中止し、運営者に連絡してください。")
        total += recalculated
    return total


def compute_bill(conn, store_id, visit_number):
    visit = get_visit(conn, store_id, visit_number)
    total = _verified_total(conn, store_id, visit)
    visit["bill"] = {
        "total": total,
        "item_count": visit["item_count"],
        "split": split_amount(total, visit["party_size"]),
    }
    return visit


def mark_checked(conn, store_id, visit_number):
    """会計内容（金額）を確認した記録。代金の受け取りではない。"""
    with transaction(conn):
        row = _visit_row(conn, store_id, visit_number)
        visit = _visit_to_dict(row, _orders_by_visit(conn, store_id, [row["id"]])[row["id"]])
        if row["payment_status"] == "paid":
            raise conflict("ALREADY_PAID", "この伝票はすでに会計済みです。", "画面を更新してください。")
        if not visit["active_order_count"]:
            raise conflict("NOTHING_TO_PAY", "会計する注文がありません（すべて取消されています）。", "注文履歴を確認してください。")
        if row["payment_status"] == "unpaid":
            ts = now_iso()
            conn.execute("UPDATE visits SET payment_status = 'checked', checked_at = ?, updated_at = ? WHERE id = ?", (ts, ts, row["id"]))
            conn.execute(
                "UPDATE orders SET payment_status = 'checked', checked_at = ?, updated_at = ? "
                "WHERE visit_id = ? AND order_status != 'cancelled' AND payment_status = 'unpaid'",
                (ts, ts, row["id"]),
            )
            write_audit(conn, "store", "payment.checked", "visit", row["id"], {"total": visit["total"]}, store_id=store_id)
    return compute_bill(conn, store_id, visit_number)


def record_payment(conn, store_id, visit_number, payload):
    """実際に代金を受け取ったことを記録する。確認フラグと金額の一致を必須にする。"""
    if not isinstance(payload, dict) or payload.get("confirmed") is not True:
        raise bad_request("CONFIRMATION_REQUIRED", "支払いの確認が完了していません。", "確認画面で「受け取りました」を押してください。")
    expected_total = strict_int(payload.get("expected_total"), "支払い金額", 0, 10**9)
    with transaction(conn):
        row = _visit_row(conn, store_id, visit_number)
        visit = _visit_to_dict(row, _orders_by_visit(conn, store_id, [row["id"]])[row["id"]])
        total = _verified_total(conn, store_id, visit)
        if row["payment_status"] == "paid":
            raise conflict("ALREADY_PAID", "この伝票はすでに会計済みとして記録されています。", "二重に記録する必要はありません。画面を更新してください。")
        if row["payment_status"] == "unpaid":
            raise conflict("NOT_CHECKED", "会計内容の確認がまだ済んでいません。",
                           "先に「会計内容を確認しました」を押してください。追加注文や取消があった場合も確認し直しが必要です。")
        if expected_total != total:
            raise conflict("AMOUNT_MISMATCH", "画面の金額と注文データの金額が一致しません。", "画面を更新して、最新の合計金額で会計し直してください。")
        ts = now_iso()
        updated = conn.execute(
            "UPDATE visits SET payment_status = 'paid', paid_at = ?, paid_amount = ?, updated_at = ? WHERE id = ? AND payment_status = 'checked'",
            (ts, total, ts, row["id"]),
        )
        if updated.rowcount != 1:
            raise conflict("PAYMENT_STATE_MISMATCH", "ほかの操作で伝票の状態が変わりました。", "画面を更新してください。")
        conn.execute(
            "UPDATE orders SET payment_status = 'paid', paid_at = ?, updated_at = ? WHERE visit_id = ? AND order_status != 'cancelled'",
            (ts, ts, row["id"]),
        )
        write_audit(conn, "store", "payment.paid", "visit", row["id"], {"amount": total}, store_id=store_id)
    return compute_bill(conn, store_id, visit_number)

