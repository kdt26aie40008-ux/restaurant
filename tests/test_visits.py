"""伝票（追加注文・まとめ表示・伝票単位の会計）のテスト。"""

from app import orders, visits
from app.db import connect, migrate, transaction
from app.errors import AppError
from app.timeutil import now_iso, today
from tests.helpers import DbTestCase, order_payload


class VisitTest(DbTestCase):
    def first_order(self, items=(("P001", 2),), party_size=3, table_label="5番"):
        payload = {**order_payload(list(items), party_size), "table_label": table_label}
        order, _ = orders.create_order(self.conn, self.store_id, payload, 30)
        return order

    def add_order(self, visit_id, items):
        order, created = orders.create_order(self.conn, self.store_id, {**order_payload(list(items), None), "visit_id": visit_id}, 30)
        return order, created

    def test_additional_orders_are_grouped_into_one_visit(self):
        first = self.first_order()
        second, _ = self.add_order(first["visit_id"], [("P006", 3), ("P001", 1)])
        self.assertEqual(second["visit_id"], first["visit_id"])
        self.assertEqual(second["party_size"], 3)  # 人数・テーブルは伝票のもの
        self.assertEqual(second["table_label"], "5番")
        visit = visits.get_visit(self.conn, self.store_id, first["visit_id"])
        self.assertEqual(visit["order_count"], 2)
        self.assertEqual(visit["total"], 680 * 3 + 320 * 3)
        summary = {(i["product_name"], i["unit_price"]): (i["quantity"], i["subtotal"]) for i in visit["items_summary"]}
        self.assertEqual(summary, {("ハンバーガー", 680): (3, 2040), ("ポテト", 320): (3, 960)})
        self.assertEqual([o["order_number"] for o in visit["orders"]], [first["order_number"], second["order_number"]])

    def test_price_change_between_orders_shows_separate_lines(self):
        from app import menu

        first = self.first_order([("P001", 1)])
        menu.update_product(self.conn, self.store_id, "P001", {"price": 700})
        self.add_order(first["visit_id"], [("P001", 1)])
        visit = visits.compute_bill(self.conn, self.store_id, first["visit_id"])
        self.assertEqual(sorted((i["unit_price"], i["quantity"]) for i in visit["items_summary"]), [(680, 1), (700, 1)])
        self.assertEqual(visit["bill"]["total"], 1380)

    def test_bill_covers_all_orders_and_excludes_cancelled(self):
        first = self.first_order([("P001", 1)], party_size=2)
        second, _ = self.add_order(first["visit_id"], [("P015", 2)])
        third, _ = self.add_order(first["visit_id"], [("P011", 1)])
        orders.cancel_order(self.conn, self.store_id, third["id"], {"reason": "出し間違い"})
        bill = visits.compute_bill(self.conn, self.store_id, first["visit_id"])
        self.assertEqual(bill["bill"]["total"], 680 + 760)
        self.assertEqual(bill["bill"]["split"]["per_person"], 720)
        self.assertEqual(bill["active_order_count"], 2)

    def test_adding_or_cancelling_after_check_requires_check_again(self):
        first = self.first_order([("P001", 1)])
        visits.mark_checked(self.conn, self.store_id, first["visit_id"])
        second, _ = self.add_order(first["visit_id"], [("P006", 1)])
        self.assertEqual(visits.get_visit(self.conn, self.store_id, first["visit_id"])["payment_status"], "unpaid")
        with self.assertRaises(AppError) as ctx:
            visits.record_payment(self.conn, self.store_id, first["visit_id"], {"confirmed": True, "expected_total": 680})
        self.assertEqual(ctx.exception.code, "NOT_CHECKED")

        visits.mark_checked(self.conn, self.store_id, first["visit_id"])
        orders.cancel_order(self.conn, self.store_id, second["id"], {"reason": "取り消し"})
        self.assertEqual(visits.get_visit(self.conn, self.store_id, first["visit_id"])["payment_status"], "unpaid")
        visits.mark_checked(self.conn, self.store_id, first["visit_id"])
        paid = visits.record_payment(self.conn, self.store_id, first["visit_id"], {"confirmed": True, "expected_total": 680})
        self.assertEqual(paid["payment_status"], "paid")
        statuses = {o["order_number"]: o["payment_status"] for o in paid["orders"]}
        self.assertEqual(statuses, {first["order_number"]: "paid", second["order_number"]: "cancelled"})

    def test_paid_visit_cannot_take_more_orders(self):
        first = self.first_order([("P001", 1)])
        visits.mark_checked(self.conn, self.store_id, first["visit_id"])
        visits.record_payment(self.conn, self.store_id, first["visit_id"], {"confirmed": True, "expected_total": 680})
        with self.assertRaises(AppError) as ctx:
            self.add_order(first["visit_id"], [("P006", 1)])
        self.assertEqual(ctx.exception.code, "VISIT_CLOSED")
        self.assertFalse(visits.get_visit(self.conn, self.store_id, first["visit_id"])["can_add_order"])

    def test_additional_order_is_idempotent(self):
        first = self.first_order([("P001", 1)])
        payload = {**order_payload([("P006", 1)], None), "visit_id": first["visit_id"]}
        a, created_a = orders.create_order(self.conn, self.store_id, payload, 30)
        b, created_b = orders.create_order(self.conn, self.store_id, payload, 30)
        self.assertEqual((a["id"], created_a, created_b), (b["id"], True, False))
        self.assertEqual(visits.get_visit(self.conn, self.store_id, first["visit_id"])["order_count"], 2)

    def test_invalid_visit_references(self):
        for bad in ["V20261019-999", "20261019-0001", "../x", 123]:  # 空文字は「新しいお客様」として扱う
            with self.subTest(visit=bad):
                with self.assertRaises(AppError):
                    orders.create_order(self.conn, self.store_id, {**order_payload([("P001", 1)], 2), "visit_id": bad}, 30)
        with self.assertRaises(AppError):
            self.first_order(table_label="x" * 21)
        with self.assertRaises(AppError):
            self.first_order(table_label="=cmd")

    def test_history_lists_visits_with_filters(self):
        a = self.first_order([("P001", 1)], table_label="1番")
        self.add_order(a["visit_id"], [("P006", 1)])
        b = self.first_order([("P002", 1)], table_label="カウンター")
        visits.mark_checked(self.conn, self.store_id, b["visit_id"])
        visits.record_payment(self.conn, self.store_id, b["visit_id"], {"confirmed": True, "expected_total": 780})
        listed = visits.list_visits(self.conn, self.store_id, today(), today())
        self.assertEqual([v["id"] for v in listed], [b["visit_id"], a["visit_id"]])
        self.assertEqual([v["id"] for v in visits.list_visits(self.conn, self.store_id, today(), today(), status="unpaid")], [a["visit_id"]])
        self.assertEqual([v["id"] for v in visits.list_visits(self.conn, self.store_id, today(), today(), status="paid")], [b["visit_id"]])
        self.assertEqual([v["id"] for v in visits.list_visits(self.conn, self.store_id, today(), today(), keyword="カウンター")], [b["visit_id"]])
        self.assertEqual([v["id"] for v in visits.list_visits(self.conn, self.store_id, today(), today(), keyword=a["order_number"])], [a["visit_id"]])
        self.assertEqual(visits.list_visits(self.conn, self.other_store_id, today(), today()), [])
        with self.assertRaises(AppError):
            visits.list_visits(self.conn, self.store_id, today(), today(), status="bogus")

    def test_visit_numbers_are_per_store(self):
        a = self.first_order()
        other, _ = orders.create_order(self.conn, self.other_store_id, order_payload([("P001", 1)]), 30)
        self.assertEqual(a["visit_id"], other["visit_id"])
        self.assertTrue(a["visit_id"].endswith("-001"))


class VisitMigrationTest(DbTestCase):
    def test_existing_orders_become_one_visit_each(self):
        import shutil
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp()
        conn = connect(Path(tmp) / "old.db")
        try:
            migrate(conn, target_version="0002")
            ts = now_iso()
            with transaction(conn):
                conn.execute("INSERT INTO stores (id, name, status, created_at, updated_at) VALUES (1, 'S', 'unclaimed', ?, ?)", (ts, ts))
                for i, status in enumerate(["paid", "unpaid"], start=1):
                    conn.execute(
                        "INSERT INTO orders (id, store_id, order_number, request_key, request_fingerprint, ordered_at, party_size, total_amount, "
                        "payment_status, created_at, updated_at) VALUES (?, 1, ?, ?, 'f', ?, 2, 500, ?, ?, ?)",
                        (i, f"20261019-000{i}", f"key{i}", ts, status, ts, ts),
                    )
            migrate(conn)
            rows = conn.execute("SELECT o.order_number, v.visit_number, v.payment_status FROM orders o JOIN visits v ON v.id = o.visit_id ORDER BY o.id").fetchall()
            self.assertEqual([tuple(r) for r in rows], [("20261019-0001", "V20261019-001", "paid"), ("20261019-0002", "V20261019-002", "unpaid")])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)
