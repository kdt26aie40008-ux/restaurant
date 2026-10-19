"""アレルギー情報（商品・CSV・おすすめ）のテスト。"""

from app import csv_io, menu, recommend
from app.allergens import parse_allergens
from app.errors import AppError
from tests.helpers import DbTestCase

HEADER = "product_id,category_name,product_name,price,image_path,is_available,popularity_score,recommendation_tags,allergens\n"


class AllergenTest(DbTestCase):
    def test_parse_accepts_codes_and_japanese_names(self):
        self.assertEqual(parse_allergens("milk;卵、小麦"), ["egg", "milk", "wheat"])
        self.assertEqual(parse_allergens(["そば", "buckwheat"]), ["buckwheat"])
        self.assertEqual(parse_allergens(""), [])
        for bad in ["fish", "卵;だいず", 123, ["egg", 1]]:
            with self.subTest(value=bad):
                with self.assertRaises(AppError):
                    parse_allergens(bad)

    def test_sample_products_have_allergens(self):
        products = {p["id"]: p for p in menu.list_products(self.conn, self.store_id)}
        self.assertEqual(products["P015"]["allergens"], ["egg", "milk"])  # プリン
        self.assertEqual(products["P006"]["allergens"], [])  # ポテト

    def test_recommendations_exclude_selected_allergens(self):
        products = menu.list_products(self.conn, self.store_id)
        result = recommend.recommend(products, {"likes_sweet": True, "avoid_allergens": ["egg", "milk"]})
        by_id = {p["id"]: p for p in products}
        self.assertTrue(result["items"])
        for item in result["items"]:
            self.assertFalse({"egg", "milk"} & set(by_id[item["product_id"]]["allergens"]), item)
        with self.assertRaises(AppError):
            recommend.recommend(products, {"avoid_allergens": ["poison"]})

    def test_csv_import_and_export_allergens(self):
        data = (HEADER + "P006,サイドメニュー,ポテト,320,,1,95,,小麦;大豆\n").encode("utf-8")
        csv_io.apply_menu_csv(self.conn, self.store_id, data)
        self.assertEqual(menu.get_product(self.conn, self.store_id, "P006")["allergens"], ["wheat", "soy"])
        self.assertIn("wheat;soy", csv_io.export_menu(self.conn, self.store_id, False).decode("utf-8"))
        bad = (HEADER + "P006,サイドメニュー,ポテト,320,,1,95,,毒\n").encode("utf-8")
        preview = csv_io.preview_menu_csv(self.conn, self.store_id, bad)
        self.assertFalse(preview["valid"])
        # allergens 列が無い CSV では既存のアレルギー情報を消さない
        no_column = ("product_id,category_name,product_name,price,image_path,is_available,popularity_score,recommendation_tags\n"
                     "P006,サイドメニュー,ポテト,330,,1,95,\n").encode("utf-8")
        csv_io.apply_menu_csv(self.conn, self.store_id, no_column)
        self.assertEqual(menu.get_product(self.conn, self.store_id, "P006")["allergens"], ["wheat", "soy"])

    def test_admin_can_update_allergens(self):
        updated = menu.update_product(self.conn, self.store_id, "P006", {"allergens": ["乳"]})
        self.assertEqual(updated["allergens"], ["milk"])
