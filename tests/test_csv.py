"""CSV 入出力のテスト（仕様のテスト項目 17〜19, 22 に対応）。"""

import csv
import io
from pathlib import Path

from app import csv_io, orders
from app.config import BASE_DIR
from app.errors import AppError
from app.timeutil import today
from tests.helpers import DbTestCase, order_payload

HEADER = "product_id,category_name,product_name,price,image_path,is_available,popularity_score,recommendation_tags\n"


def csv_bytes(*rows, header=HEADER, bom=False):
    return (("﻿" if bom else "") + header + "".join(r + "\n" for r in rows)).encode("utf-8")


class MenuImportTest(DbTestCase):
    def snapshot(self):
        return self.conn.execute("SELECT * FROM products WHERE store_id = ? ORDER BY id", (self.store_id,)).fetchall()

    def assert_rejected(self, data, code=None, error_fragment=None):
        before = [tuple(r) for r in self.snapshot()]
        preview = None
        try:
            preview = csv_io.preview_menu_csv(self.conn, self.store_id, data)
        except AppError as exc:
            if code:
                self.assertEqual(exc.code, code)
        if preview is not None:
            self.assertFalse(preview["valid"])
            if error_fragment:
                self.assertTrue(any(error_fragment in e["message"] for e in preview["errors"]), preview["errors"])
        with self.assertRaises(AppError):
            csv_io.apply_menu_csv(self.conn, self.store_id, data)
        self.assertEqual([tuple(r) for r in self.snapshot()], before, "DBが変更されてしまった")
        self.assertFalse(self.conn.in_transaction)

    def test_sample_file_imports(self):
        data = (BASE_DIR / "sample" / "menu_sample.csv").read_bytes()
        preview = csv_io.preview_menu_csv(self.conn, self.store_id, data)
        self.assertTrue(preview["valid"], preview["errors"])
        self.assertEqual(preview["summary"]["add"], 2)
        result = csv_io.apply_menu_csv(self.conn, self.store_id, data)
        self.assertEqual(result["summary"]["add"], 2)
        row = self.conn.execute("SELECT * FROM products WHERE store_id = ? AND id = 'P101'", (self.store_id,)).fetchone()
        self.assertEqual(row["name"], "チキン南蛮バーガー")
        self.assertEqual(row["description"], "甘酢だれとタルタル, ボリューム満点")
        self.assertEqual(row["recommendation_tags"], "hearty;sweet")

    def test_update_and_add_with_bom(self):
        data = csv_bytes(
            "P001,フード,ハンバーガー,700,,1,90,hearty",
            "N001,デザート,抹茶アイス,350,images/products/N001.webp,0,40,sweet;age_senior",
            bom=True,
        )
        result = csv_io.apply_menu_csv(self.conn, self.store_id, data)
        self.assertEqual((result["summary"]["add"], result["summary"]["update"]), (1, 1))
        self.assertEqual(self.conn.execute("SELECT price FROM products WHERE store_id = ? AND id = 'P001'", (self.store_id,)).fetchone()[0], 700)
        # description 列が無い CSV では既存の説明を消さない
        self.assertEqual(self.conn.execute("SELECT description FROM products WHERE store_id = ? AND id = 'P001'", (self.store_id,)).fetchone()[0], "ビーフパティのシンプルなバーガー")
        self.assertEqual(self.conn.execute("SELECT is_available FROM products WHERE store_id = ? AND id = 'N001'", (self.store_id,)).fetchone()[0], 0)

    def test_rejects_missing_column(self):
        self.assert_rejected(csv_bytes("P001,フード,ハンバーガー,700", header="product_id,category_name,product_name,price\n"), "CSV_HEADER")

    def test_rejects_duplicate_ids(self):
        self.assert_rejected(csv_bytes("X1,フード,A,100,,1,1,", "X1,フード,B,100,,1,1,"), error_fragment="重複")

    def test_rejects_unknown_category(self):
        self.assert_rejected(csv_bytes("X1,存在しない,A,100,,1,1,"), error_fragment="登録されていません")

    def test_rejects_negative_or_invalid_numbers(self):
        self.assert_rejected(csv_bytes("X1,フード,A,-100,,1,1,"), error_fragment="価格")
        self.assert_rejected(csv_bytes("X1,フード,A,1.5,,1,1,"), error_fragment="価格")
        self.assert_rejected(csv_bytes("X1,フード,A,100,,2,1,"), error_fragment="販売状態")
        self.assert_rejected(csv_bytes("X1,フード,A,100,,1,101,"), error_fragment="人気度")

    def test_rejects_column_count_mismatch(self):
        self.assert_rejected(csv_bytes("X1,フード,A,100,,1,1"), error_fragment="列の数")

    def test_rejects_non_utf8(self):
        self.assert_rejected((HEADER + "X1,フード,A,100,,1,1,\n").encode("cp932"), "CSV_ENCODING")

    def test_rejects_too_large_file(self):
        big = csv_bytes(*["X%d,フード,A%d,100,,1,1," % (i, i) for i in range(1)]) + b"#" * csv_io.MAX_CSV_BYTES
        self.assert_rejected(big, "CSV_TOO_LARGE")

    def test_rejects_bad_tags_and_paths(self):
        self.assert_rejected(csv_bytes("X1,フード,A,100,,1,1,Spicy!"), error_fragment="タグ")
        self.assert_rejected(csv_bytes("X1,フード,A,100,../../data/restaurant.db,1,1,"), error_fragment="画像パス")

    def test_rejects_name_conflict_with_existing_product(self):
        self.assert_rejected(csv_bytes("X1,フード,ハンバーガー,100,,1,1,"), error_fragment="既存の商品")

    def test_rejects_formula_like_names(self):
        self.assert_rejected(csv_bytes("X1,フード,=CMD(),100,,1,1,"), error_fragment="先頭")

    def test_import_only_touches_own_store(self):
        csv_io.apply_menu_csv(self.conn, self.store_id, csv_bytes("P001,フード,ハンバーガー,1,,0,90,"))
        other = self.conn.execute("SELECT price, is_available FROM products WHERE store_id = ? AND id = 'P001'", (self.other_store_id,)).fetchone()
        self.assertEqual(tuple(other), (680, 1))
        # 他店舗のカテゴリ名は自店舗のカテゴリとして扱われない
        self.conn.execute("UPDATE categories SET name = 'B店だけのカテゴリ' WHERE store_id = ? AND name = 'その他'", (self.other_store_id,))
        self.assert_rejected(csv_bytes("X9,B店だけのカテゴリ,新商品,100,,1,1,"), error_fragment="登録されていません")

    def test_one_bad_row_blocks_whole_import(self):
        self.assert_rejected(csv_bytes("X1,フード,OK商品,100,,1,1,", "X2,フード,NG商品,abc,,1,1,"))
        self.assertEqual(self.count("products", "id = 'X1'"), 0)


class ExportTest(DbTestCase):
    def test_sales_export_keeps_japanese_and_excludes_cancelled(self):
        kept, _ = orders.create_order(self.conn, self.store_id, order_payload([("P001", 2), ("P015", 1)], 3), 30)
        cancelled, _ = orders.create_order(self.conn, self.store_id, order_payload([("P002", 1)]), 30)
        orders.cancel_order(self.conn, self.store_id, cancelled["id"], {"reason": "テスト"})

        orders.create_order(self.conn, self.other_store_id, order_payload([("P016", 1)]), 30)  # 他店舗の注文は出力されない
        data = csv_io.export_sales(self.conn, self.store_id, today(), today(), with_bom=True)
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))
        self.assertEqual(rows[0], csv_io.SALES_HEADER)
        self.assertEqual([r[5] for r in rows[1:]], ["ハンバーガー", "プリン"])
        self.assertEqual(rows[1][1], kept["visit_id"])
        self.assertEqual(rows[1][6:11], ["2", "680", "1360", str(kept["total_amount"]), "3"])
        self.assertNotIn(cancelled["order_number"], data.decode("utf-8-sig"))

        history = csv_io.export_orders(self.conn, self.store_id, today(), today(), with_bom=False)
        self.assertFalse(history.startswith(b"\xef\xbb\xbf"))
        text = history.decode("utf-8")
        self.assertIn(cancelled["order_number"], text)
        self.assertIn("取消", text)

    def test_formula_cells_are_neutralized(self):
        self.assertEqual(csv_io.sanitize_cell("=1+1"), "'=1+1")
        self.assertEqual(csv_io.sanitize_cell("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(csv_io.sanitize_cell("-2"), "'-2")
        self.assertEqual(csv_io.sanitize_cell("ポテト"), "ポテト")
        self.assertEqual(csv_io.sanitize_cell(-2), -2)  # 数値はそのまま
        self.conn.execute("UPDATE products SET description = '=HYPERLINK(\"x\")' WHERE store_id = ? AND id = 'P001'", (self.store_id,))
        self.assertIn("'=HYPERLINK", csv_io.export_menu(self.conn, self.store_id, False).decode("utf-8"))

    def test_menu_export_can_be_reimported(self):
        data = csv_io.export_menu(self.conn, self.store_id, with_bom=True)
        preview = csv_io.preview_menu_csv(self.conn, self.store_id, data)
        self.assertTrue(preview["valid"], preview["errors"])
        self.assertEqual(preview["summary"]["unchanged"], self.count("products", "store_id = ?", (self.store_id,)))
