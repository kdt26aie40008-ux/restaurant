-- 商品にアレルギー情報（アレルゲンのコードを ; 区切り。例: egg;milk;wheat）を持たせる。
-- 使えるコードは app/allergens.py の ALLERGENS。サンプル商品の値は app/seed.py の upgrade_sample_menu で入れる。

ALTER TABLE products ADD COLUMN allergens TEXT NOT NULL DEFAULT '';
