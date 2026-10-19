"""注文・会計・状態管理のテスト（仕様のテスト項目 1, 2, 7〜14, 21, 22 に対応）。"""

import unittest
from unittest import mock

from app import menu, orders, visits
from app.db import init_database, transaction
from app.seed import seed_store
from app.errors import AppError
from app.timeutil import today
from app.validation import validate_party_size
from tests.helpers import DbTestCase, order_payload


class PartySizeValidationTest(unittest.TestCase):
    def test_accepts_valid_values(self):
        self.assertEqual(validate_party_size("3", 30), 3)
        self.assertEqual(validate_party_size(" ３ ", 30), 3)  # 全角数字も受け付ける
        self.assertEqual(validate_party_size(30, 30), 30)

    def test_rejects_invalid_values(self):
        cases = {
            "": "PARTY_SIZE_REQUIRED",
            "   ": "PARTY_SIZE_REQUIRED",
            None: "PARTY_SIZE_REQUIRED",
            "0": "PARTY_SIZE_ZERO",
            0: "PARTY_SIZE_ZERO",
            "-2": "PARTY_SIZE_NEGATIVE",
            -1: "PARTY_SIZE_NEGATIVE",
            "1.5": "PARTY_SIZE_DECIMAL",
            2.0: "PARTY_SIZE_DECIMAL",
            "abc": "PARTY_SIZE_NOT_NUMBER",
            "3人": "PARTY_SIZE_NOT_NUMBER",
            "31": "PARTY_SIZE_TOO_LARGE",
            "99999999999": "PARTY_SIZE_TOO_LARGE",
            True: "PARTY_SIZE_INVALID",
        }
        for value, code in cases.items():
            with self.subTest(value=value):
                with self.assertRaises(AppError) as ctx:
                    validate_party_size(value, 30)
                self.assertEqual(ctx.exception.code, code)
                self.assertTrue(ctx.exception.hint)


class OrderTest(DbTestCase):
    def create(self, items, party_size=2, **kwargs):
        return orders.create_order(self.conn, self.store_id, order_payload(items, party_size, **kwargs), 30)

    def test_order_is_saved_with_correct_totals(self):
        order, created = self.create([("P001", 2), ("P006", 3)], party_size=3)
        self.assertTrue(created)
        self.assertEqual(order["total_amount"], 680 * 2 + 320 * 3)
        self.assertEqual([i["subtotal"] for i in order["items"]], [1360, 960])
        self.assertEqual(order["order_status"], "received")
        self.assertEqual(order["payment_status"], "unpaid")
        self.assertRegex(order["order_number"], r"^\d{8}-0001$")
        self.assertEqual(self.count("orders"), 1)
        self.assertEqual(self.count("order_items"), 2)

    def test_order_numbers_are_unique_and_sequential(self):
        first, _ = self.create([("P001", 1)])
        second, _ = self.create([("P001", 1)])
        self.assertNotEqual(first["order_number"], second["order_number"])
        self.assertTrue(second["order_number"].endswith("-0002"))

    def test_same_request_key_does_not_create_second_order(self):
        payload = order_payload([("P001", 1)], 2)
        first, created_first = orders.create_order(self.conn, self.store_id, payload, 30)
        second, created_second = orders.create_order(self.conn, self.store_id, payload, 30)
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.count("orders"), 1)

    def test_reused_request_key_with_different_content_is_rejected(self):
        payload = order_payload([("P001", 1)], 2)
        orders.create_order(self.conn, self.store_id, payload, 30)
        payload["items"][0]["quantity"] = 5
        with self.assertRaises(AppError) as ctx:
            orders.create_order(self.conn, self.store_id, payload, 30)
        self.assertEqual(ctx.exception.code, "REQUEST_KEY_REUSED")
        self.assertEqual(self.count("orders"), 1)

    def test_failure_while_saving_items_leaves_no_partial_order(self):
        with mock.patch("app.orders._insert_items", side_effect=RuntimeError("disk error")):
            with self.assertRaises(RuntimeError):
                self.create([("P001", 1), ("P006", 1)])
        self.assertEqual(self.count("orders"), 0)
        self.assertEqual(self.count("order_items"), 0)
        self.assertFalse(self.conn.in_transaction)
        # 失敗後も通常どおり注文できる
        order, _ = self.create([("P001", 1)])
        self.assertEqual(order["total_amount"], 680)

    def test_price_change_does_not_affect_past_orders(self):
        order, _ = self.create([("P001", 2)])
        menu.update_product(self.conn, self.store_id, "P001", {"price": 1000})
        reloaded = visits.compute_bill(self.conn, self.store_id, order["visit_id"])
        self.assertEqual(reloaded["items_summary"][0]["unit_price"], 680)
        self.assertEqual(reloaded["bill"]["total"], 1360)
        new_order, _ = self.create([("P001", 2)])
        self.assertEqual(new_order["total_amount"], 2000)

    def test_price_change_after_viewing_is_detected(self):
        menu.update_product(self.conn, self.store_id, "P001", {"price": 700})
        with self.assertRaises(AppError) as ctx:
            self.create([("P001", 1)], expected_total=680)
        self.assertEqual(ctx.exception.code, "PRICE_CHANGED")
        self.assertEqual(self.count("orders"), 0)

    def test_unavailable_product_cannot_be_ordered(self):
        with self.assertRaises(AppError) as ctx:
            self.create([("P010", 1)])  # サンプルの販売停止商品
        self.assertEqual(ctx.exception.code, "PRODUCT_UNAVAILABLE")
        menu.update_product(self.conn, self.store_id, "P001", {"is_available": False})  # 注文途中で販売停止になった場合
        with self.assertRaises(AppError) as ctx:
            self.create([("P006", 1), ("P001", 1)])
        self.assertEqual(ctx.exception.code, "PRODUCT_UNAVAILABLE")
        self.assertEqual(self.count("orders"), 0)

    def test_invalid_items_are_rejected(self):
        bad_cases = [
            ([], "EMPTY_ORDER"),
            ([("P001", 0)], "INVALID_QUANTITY"),
            ([("P001", -1)], "INVALID_QUANTITY"),
            ([("P001", 100)], "INVALID_QUANTITY"),
            ([("NOPE", 1)], "PRODUCT_NOT_FOUND"),
            ([("P001", 1), ("P001", 2)], "DUPLICATE_ITEM"),
        ]
        for items, code in bad_cases:
            with self.subTest(items=items):
                with self.assertRaises(AppError) as ctx:
                    self.create(items)
                self.assertEqual(ctx.exception.code, code)
        with self.assertRaises(AppError) as ctx:
            orders.create_order(self.conn, self.store_id, {**order_payload([("P001", 1)]), "items": [{"product_id": "P001", "quantity": 1.5}]}, 30)
        self.assertEqual(ctx.exception.code, "INVALID_QUANTITY")
        self.assertEqual(self.count("orders"), 0)

    def test_order_rejects_invalid_party_size(self):
        for party_size in ["", 0, -3, "2.5", "abc", 31]:
            with self.subTest(party_size=party_size):
                with self.assertRaises(AppError):
                    self.create([("P001", 1)], party_size=party_size)
        self.assertEqual(self.count("orders"), 0)

    def test_confirmed_order_values_cannot_be_rewritten(self):
        order, _ = self.create([("P001", 1)])
        import sqlite3

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE orders SET total_amount = 1 WHERE order_number = ?", (order["id"],))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE order_items SET unit_price = 1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM order_items")


class BillTest(DbTestCase):
    def test_split_amount(self):
        self.assertEqual(orders.split_amount(3000, 3)["per_person"], 1000)
        uneven = orders.split_amount(1000, 3)
        self.assertEqual((uneven["per_person"], uneven["remainder"], uneven["per_person_rounded_up"]), (333, 1, 334))
        self.assertIn("1円", uneven["rule"])

    def test_split_never_divides_by_zero(self):
        for party_size in [0, -1, None, 1.5, True]:
            with self.subTest(party_size=party_size):
                with self.assertRaises(AppError):
                    orders.split_amount(1000, party_size)

    def test_bill_is_computed_from_saved_items(self):
        order, _ = orders.create_order(self.conn, self.store_id, order_payload([("P001", 1), ("P011", 2)], 3), 30)
        bill = visits.compute_bill(self.conn, self.store_id, order["visit_id"])["bill"]
        self.assertEqual(bill["total"], 680 + 250 * 2)
        self.assertEqual(bill["split"]["per_person"], (680 + 500) // 3)
        self.assertEqual(bill["item_count"], 3)

    def test_payment_flow_requires_check_and_confirmation(self):
        order, _ = orders.create_order(self.conn, self.store_id, order_payload([("P001", 1)]), 30)
        oid = order["visit_id"]
        with self.assertRaises(AppError) as ctx:
            visits.record_payment(self.conn, self.store_id, oid, {"confirmed": True, "expected_total": 680})
        self.assertEqual(ctx.exception.code, "NOT_CHECKED")
        self.assertEqual(visits.mark_checked(self.conn, self.store_id, oid)["payment_status"], "checked")
        self.assertEqual(visits.mark_checked(self.conn, self.store_id, oid)["payment_status"], "checked")  # 2回押しても問題ない
        with self.assertRaises(AppError) as ctx:
            visits.record_payment(self.conn, self.store_id, oid, {"expected_total": 680})
        self.assertEqual(ctx.exception.code, "CONFIRMATION_REQUIRED")
        with self.assertRaises(AppError) as ctx:
            visits.record_payment(self.conn, self.store_id, oid, {"confirmed": True, "expected_total": 1})
        self.assertEqual(ctx.exception.code, "AMOUNT_MISMATCH")
        paid = visits.record_payment(self.conn, self.store_id, oid, {"confirmed": True, "expected_total": 680})
        self.assertEqual(paid["payment_status"], "paid")
        self.assertEqual(orders.get_order(self.conn, self.store_id, order["id"])["payment_status"], "paid")
        with self.assertRaises(AppError) as ctx:
            visits.record_payment(self.conn, self.store_id, oid, {"confirmed": True, "expected_total": 680})
        self.assertEqual(ctx.exception.code, "ALREADY_PAID")

    def test_status_advances_in_order_and_detects_conflicts(self):
        order, _ = orders.create_order(self.conn, self.store_id, order_payload([("P001", 1)]), 30)
        oid = order["id"]
        with self.assertRaises(AppError):
            orders.advance_order_status(self.conn, self.store_id, oid, {"current_status": "received", "new_status": "served"})
        orders.advance_order_status(self.conn, self.store_id, oid, {"current_status": "received", "new_status": "cooking"})
        with self.assertRaises(AppError) as ctx:  # 古い画面からの操作
            orders.advance_order_status(self.conn, self.store_id, oid, {"current_status": "received", "new_status": "cooking"})
        self.assertEqual(ctx.exception.code, "STATUS_CHANGED")
        self.assertEqual(orders.advance_order_status(self.conn, self.store_id, oid, {"current_status": "cooking", "new_status": "served"})["order_status"], "served")


class HistoryAndSalesTest(DbTestCase):
    def test_history_lists_orders_with_items(self):
        first, _ = orders.create_order(self.conn, self.store_id, order_payload([("P001", 1)], 1), 30)
        second, _ = orders.create_order(self.conn, self.store_id, order_payload([("P006", 2), ("P011", 1)], 4), 30)
        listed = orders.list_orders(self.conn, self.store_id, today(), today())
        self.assertEqual([o["id"] for o in listed], [second["id"], first["id"]])
        self.assertEqual(listed[0]["party_size"], 4)
        self.assertEqual(len(listed[0]["items"]), 2)
        self.assertEqual(listed[0]["order_status_label"], "受付")
        by_number = orders.list_orders(self.conn, self.store_id, today(), today(), keyword=first["order_number"][-4:])
        self.assertEqual([o["id"] for o in by_number], [first["id"]])
        self.assertEqual(orders.list_orders(self.conn, self.store_id, today(), today(), keyword="%"), [])

    def test_cancelled_orders_are_kept_but_excluded_from_sales(self):
        kept, _ = orders.create_order(self.conn, self.store_id, order_payload([("P001", 1)]), 30)
        cancelled, _ = orders.create_order(self.conn, self.store_id, order_payload([("P002", 2)]), 30)
        with self.assertRaises(AppError):
            orders.cancel_order(self.conn, self.store_id, cancelled["id"], {"reason": ""})
        result = orders.cancel_order(self.conn, self.store_id, cancelled["id"], {"reason": "入力ミス"})
        self.assertEqual((result["order_status"], result["payment_status"]), ("cancelled", "cancelled"))
        self.assertEqual(self.count("orders"), 2)  # 削除していない
        self.assertEqual(self.count("audit_logs", "action = 'order.cancel'"), 1)
        summary = orders.sales_summary(self.conn, self.store_id, today(), today())
        self.assertEqual(summary["sales_total"], kept["total_amount"])
        self.assertEqual(summary["order_count"], 1)
        self.assertEqual(summary["cancelled_count"], 1)
        with self.assertRaises(AppError) as ctx:
            visits.mark_checked(self.conn, self.store_id, cancelled["visit_id"])
        self.assertEqual(ctx.exception.code, "NOTHING_TO_PAY")

    def test_paid_order_cannot_be_cancelled(self):
        order, _ = orders.create_order(self.conn, self.store_id, order_payload([("P001", 1)]), 30)
        visits.mark_checked(self.conn, self.store_id, order["visit_id"])
        visits.record_payment(self.conn, self.store_id, order["visit_id"], {"confirmed": True, "expected_total": 680})
        with self.assertRaises(AppError) as ctx:
            orders.cancel_order(self.conn, self.store_id, order["id"], {"reason": "間違い"})
        self.assertEqual(ctx.exception.code, "PAID_ORDER_LOCKED")


class SeedTest(DbTestCase):
    def test_old_sample_menu_is_upgraded_once(self):
        from app.seed import upgrade_sample_menu

        with transaction(self.conn):
            self.conn.execute("DELETE FROM store_settings WHERE store_id = ? AND key = 'sample_menu_version'", (self.store_id,))
            self.conn.execute("UPDATE products SET image_path = 'images/products/P001.jpg' WHERE store_id = ? AND id = 'P001'", (self.store_id,))
            self.conn.execute("UPDATE products SET image_path = 'images/products/custom.png' WHERE store_id = ? AND id = 'P002'", (self.store_id,))
            self.assertEqual(upgrade_sample_menu(self.conn, self.store_id), 0)  # 商品はすでにある
            self.assertIsNone(upgrade_sample_menu(self.conn, self.store_id))  # 2回目は何もしない
        paths = dict(self.conn.execute("SELECT id, image_path FROM products WHERE store_id = ? AND id IN ('P001', 'P002')", (self.store_id,)).fetchall())
        self.assertEqual(paths, {"P001": "images/products/P001.svg", "P002": "images/products/custom.png"})

    def test_every_sample_product_has_an_image_file(self):
        from app.config import STATIC_DIR
        from app.seed import SAMPLE_PRODUCTS

        for pid, *_ in SAMPLE_PRODUCTS:
            self.assertTrue((STATIC_DIR / "images" / "products" / f"{pid}.svg").is_file(), pid)

    def test_sample_data_is_inserted_only_once_per_store(self):
        where = ("store_id = ?", (self.store_id,))
        products_before = self.count("products", *where)
        init_database(self.config.db_path)
        with transaction(self.conn):
            self.assertFalse(seed_store(self.conn, self.store_id))
        self.assertEqual(self.count("products", *where), products_before)
        self.assertEqual(self.count("categories", *where), 5)
        names = {row[0] for row in self.conn.execute("SELECT name FROM products WHERE store_id = ?", (self.store_id,))}
        self.assertTrue({"ハンバーガー", "ポテト", "コーラ", "プリン"} <= names)

    def test_stores_do_not_share_menus_or_orders(self):
        menu.update_product(self.conn, self.store_id, "P001", {"price": 999})
        self.assertEqual(menu.get_product(self.conn, self.other_store_id, "P001")["price"], 680)
        order, _ = orders.create_order(self.conn, self.store_id, order_payload([("P001", 1)]), 30)
        with self.assertRaises(AppError) as ctx:
            orders.get_order(self.conn, self.other_store_id, order["id"])
        self.assertEqual(ctx.exception.code, "ORDER_NOT_FOUND")
        for action in (lambda: visits.mark_checked(self.conn, self.other_store_id, order["visit_id"]),
                       lambda: visits.get_visit(self.conn, self.other_store_id, order["visit_id"]),
                       lambda: orders.create_order(self.conn, self.other_store_id,
                                                   {**order_payload([("P001", 1)]), "visit_id": order["visit_id"]}, 30),
                       lambda: orders.cancel_order(self.conn, self.other_store_id, order["id"], {"reason": "x"}),
                       lambda: orders.advance_order_status(self.conn, self.other_store_id, order["id"],
                                                           {"current_status": "received", "new_status": "cooking"})):
            with self.assertRaises(AppError):
                action()
        self.assertEqual(orders.list_orders(self.conn, self.other_store_id, today(), today()), [])
        self.assertEqual(orders.sales_summary(self.conn, self.other_store_id, today(), today())["sales_total"], 0)
        other_category = menu.list_categories(self.conn, self.other_store_id)[0]["id"]
        with self.assertRaises(AppError):
            menu.update_category(self.conn, self.store_id, other_category, {"name": "乗っ取り"})
        with self.assertRaises(AppError):
            menu.update_product(self.conn, self.store_id, "P001", {"category_id": other_category})
        # 2店舗とも同じ日付の連番で注文番号が振られる
        other, _ = orders.create_order(self.conn, self.other_store_id, order_payload([("P001", 1)]), 30)
        self.assertEqual(order["order_number"], other["order_number"])

    def test_category_filter_returns_only_that_category(self):
        categories = {c["name"]: c["id"] for c in menu.list_categories(self.conn, self.store_id)}
        drinks = menu.list_products(self.conn, self.store_id, categories["ドリンク"])
        self.assertTrue(drinks)
        self.assertTrue(all(p["category_name"] == "ドリンク" for p in drinks))
        menu.update_category(self.conn, self.store_id, categories["ドリンク"], {"is_active": False})
        self.assertNotIn("ドリンク", [c["name"] for c in menu.list_categories(self.conn, self.store_id)])
        self.assertFalse(any(p["category_name"] == "ドリンク" for p in menu.list_products(self.conn, self.store_id)))


if __name__ == "__main__":
    unittest.main()
