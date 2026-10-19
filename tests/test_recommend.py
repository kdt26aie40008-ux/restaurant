"""おすすめ機能のテスト（仕様のテスト項目 15, 16 に対応）。"""

from app import menu, orders, recommend
from app.errors import AppError
from tests.helpers import DbTestCase, order_payload


class RecommendTest(DbTestCase):
    def products(self):
        return menu.list_products(self.conn, self.store_id)

    def test_works_without_any_input(self):
        for prefs in (None, {}):
            result = recommend.recommend(self.products(), prefs)
            self.assertEqual(result["mode"], "popular")
            self.assertEqual(len(result["items"]), recommend.DEFAULT_LIMIT)
            self.assertTrue(all(item["reasons"] for item in result["items"]))

    def test_never_recommends_unavailable_products(self):
        menu.update_product(self.conn, self.store_id, "P006", {"is_available": False})  # 最も人気の商品
        ids = [i["product_id"] for i in recommend.recommend(self.products(), {"likes_sweet": True})["items"]]
        self.assertNotIn("P006", ids)
        self.assertNotIn("P010", ids)  # サンプルの販売停止商品

    def test_preferences_are_prioritized_and_explained(self):
        result = recommend.recommend(self.products(), {"likes_spicy": True})
        self.assertEqual(result["mode"], "personalized")
        top = result["items"][0]
        self.assertIn(top["product_id"], {"P003", "P008", "P023"})  # spicy タグの商品
        self.assertIn("辛いものがお好きな方に", top["reasons"])

    def test_results_are_diverse_across_categories(self):
        products = self.products()
        category_of = {p["id"]: p["category_id"] for p in products}
        items = recommend.recommend(products, {})["items"]
        per_category = {}
        for item in items:
            per_category[category_of[item["product_id"]]] = per_category.get(category_of[item["product_id"]], 0) + 1
        self.assertLessEqual(max(per_category.values()), recommend.MAX_PER_CATEGORY)
        self.assertEqual(len({i["product_id"] for i in items}), len(items))

    def test_age_group_alone_does_not_override_preferences(self):
        result = recommend.recommend(self.products(), {"age_group": "senior", "likes_sweet": True})
        top_ids = [i["product_id"] for i in result["items"][:3]]
        sweet_ids = {p["id"] for p in self.products() if "sweet" in p["tags"]}
        self.assertTrue(set(top_ids) <= sweet_ids)

    def test_alcohol_is_not_recommended_to_young_groups(self):
        menu.update_product(self.conn, self.store_id, "P013", {"recommendation_tags": "alcohol;age_child", "popularity_score": 100})
        ids = [i["product_id"] for i in recommend.recommend(self.products(), {"age_group": "child"})["items"]]
        self.assertNotIn("P013", ids)

    def test_invalid_preferences_are_rejected(self):
        for prefs in ({"age_group": "baby"}, {"likes_spicy": "yes"}, "spicy"):
            with self.subTest(prefs=prefs):
                with self.assertRaises(AppError):
                    recommend.recommend(self.products(), prefs)

    def test_recommended_product_can_be_ordered_normally(self):
        item = recommend.recommend(self.products(), {"likes_sweet": True})["items"][0]
        order, created = orders.create_order(self.conn, self.store_id, order_payload([(item["product_id"], 2)]), 30)
        self.assertTrue(created)
        self.assertEqual(order["items"][0]["product_id"], item["product_id"])
