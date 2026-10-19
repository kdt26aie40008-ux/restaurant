"""店舗を招待したときに、その店舗へ入れるサンプルメニュー。"""

from .settings import ensure_store_defaults
from .timeutil import now_iso

SAMPLE_CATEGORIES = [
    ("フード", 10),
    ("サイドメニュー", 20),
    ("ドリンク", 30),
    ("デザート", 40),
    ("その他", 50),
]

# (商品ID, カテゴリ名, 商品名, 説明, 価格, 人気度, おすすめタグ, 販売中)
SAMPLE_PRODUCTS = [
    ("P001", "フード", "ハンバーガー", "ビーフパティのシンプルなバーガー", 680, 90, "hearty;age_youth;age_adult", 1),
    ("P002", "フード", "チーズバーガー", "とろけるチェダーチーズ入り", 780, 85, "hearty;age_child;age_youth", 1),
    ("P003", "フード", "スパイシーチキンバーガー", "ハラペーニョ入りのピリ辛ソース", 820, 70, "spicy;hearty;age_youth;age_adult", 1),
    ("P004", "フード", "グリルチキンサラダ", "野菜たっぷりの軽めの一皿", 880, 55, "light;age_adult;age_senior", 1),
    ("P005", "フード", "キッズプレート", "小さめハンバーグとポテトのセット", 650, 60, "kids;age_child", 1),
    ("P006", "サイドメニュー", "ポテト", "揚げたてのフライドポテト", 320, 95, "age_child;age_youth", 1),
    ("P007", "サイドメニュー", "チキンナゲット（5個）", "", 420, 75, "age_child;age_youth", 1),
    ("P008", "サイドメニュー", "チリポテト", "チリパウダーをかけたピリ辛ポテト", 420, 40, "spicy", 1),
    ("P009", "サイドメニュー", "コーンスープ", "やさしい甘さのスープ", 300, 45, "light;sweet;age_child;age_senior", 1),
    ("P010", "サイドメニュー", "季節限定パンプキンスープ", "現在は販売を休止しています", 380, 30, "light;sweet", 0),
    ("P011", "ドリンク", "コーラ", "", 250, 88, "age_youth", 1),
    ("P012", "ドリンク", "オレンジジュース", "", 250, 60, "sweet;age_child", 1),
    ("P013", "ドリンク", "アイスコーヒー", "", 280, 65, "light;age_adult;age_senior", 1),
    ("P014", "ドリンク", "ホットティー", "", 280, 35, "light;age_senior", 1),
    ("P015", "デザート", "プリン", "なめらかな自家製プリン", 380, 80, "sweet;light;age_child;age_senior", 1),
    ("P016", "デザート", "チョコレートパフェ", "", 620, 70, "sweet;age_child;age_youth", 1),
    ("P017", "デザート", "バニラアイス", "", 300, 50, "sweet;light;age_child", 1),
    ("P018", "その他", "追加ソース（ケチャップ）", "", 50, 20, "", 1),
    ("P019", "フード", "てりやきバーガー", "甘辛だれとマヨネーズ", 720, 78, "sweet;hearty;age_child;age_youth", 1),
    ("P020", "フード", "ダブルチーズバーガー", "パティ2枚の食べごたえ", 980, 82, "hearty;age_youth;age_adult", 1),
    ("P021", "フード", "フィッシュバーガー", "白身魚のフライとタルタル", 690, 50, "light;age_adult;age_senior", 1),
    ("P022", "フード", "ホットドッグ", "あらびきソーセージとマスタード", 520, 45, "age_youth", 1),
    ("P023", "フード", "カレーライス", "スパイス香る中辛カレー", 850, 72, "spicy;hearty;age_youth;age_adult", 1),
    ("P024", "フード", "ナポリタン", "昔ながらのケチャップ味", 820, 58, "hearty;age_child;age_senior", 1),
    ("P025", "サイドメニュー", "オニオンリング", "サクサク衣の玉ねぎフライ", 380, 52, "age_youth", 1),
    ("P026", "サイドメニュー", "フライドチキン（3ピース）", "ジューシーな骨付きチキン", 580, 76, "hearty;age_youth;age_adult", 1),
    ("P027", "ドリンク", "メロンクリームソーダ", "バニラアイスのせ", 450, 74, "sweet;age_child;age_youth", 1),
    ("P028", "ドリンク", "アップルジュース", "", 250, 48, "sweet;age_child", 1),
    ("P029", "ドリンク", "ウーロン茶", "", 220, 55, "light;age_adult;age_senior", 1),
    ("P030", "ドリンク", "カフェラテ", "ラテアートつき", 380, 62, "light;age_adult;age_senior", 1),
    ("P031", "ドリンク", "いちごミルクシェイク", "", 480, 57, "sweet;age_child;age_youth", 1),
    ("P032", "ドリンク", "生ビール", "20歳未満の方にはお出しできません", 550, 70, "alcohol;age_adult", 1),
    ("P033", "ドリンク", "レモンサワー", "20歳未満の方にはお出しできません", 480, 60, "alcohol;light;age_adult", 1),
    ("P034", "デザート", "パンケーキ", "メープルシロップとバター", 680, 68, "sweet;age_child;age_youth", 1),
    ("P035", "デザート", "ベイクドチーズケーキ", "", 480, 55, "sweet;age_adult;age_senior", 1),
    ("P036", "デザート", "フルーツパフェ", "季節のフルーツたっぷり", 720, 60, "sweet;age_child;age_youth", 1),
]
SAMPLE_MENU_VERSION_KEY = "sample_menu_version"
SAMPLE_MENU_VERSION = "3"

# サンプル商品のアレルギー情報（仮の値。実際の商品に合わせて CSV で修正すること）
SAMPLE_ALLERGENS = {
    "P001": "egg;milk;wheat;soy;sesame", "P002": "egg;milk;wheat;soy;sesame", "P003": "egg;wheat;soy;sesame",
    "P004": "egg;soy", "P005": "egg;milk;wheat;soy", "P007": "wheat;soy", "P009": "milk;wheat", "P010": "milk",
    "P015": "egg;milk", "P016": "egg;milk;wheat;soy", "P017": "egg;milk", "P019": "egg;wheat;soy;sesame",
    "P020": "egg;milk;wheat;soy;sesame", "P021": "egg;milk;wheat;soy", "P022": "milk;wheat;soy", "P023": "milk;wheat;soy",
    "P024": "milk;wheat", "P025": "egg;milk;wheat", "P026": "wheat;soy", "P027": "egg;milk", "P030": "milk",
    "P031": "milk", "P034": "egg;milk;wheat", "P035": "egg;milk;wheat", "P036": "egg;milk;wheat;soy",
}


def _image_path(product_id):
    return f"images/products/{product_id}.svg"


def seed_store(conn, store_id):
    """呼び出し側のトランザクション内で実行する。すでにカテゴリがある店舗には何もしない。"""
    ensure_store_defaults(conn, store_id)
    if conn.execute("SELECT 1 FROM categories WHERE store_id = ?", (store_id,)).fetchone():
        return False
    _mark_sample_version(conn, store_id)
    ts = now_iso()
    for name, sort_order in SAMPLE_CATEGORIES:
        conn.execute(
            "INSERT INTO categories (store_id, name, sort_order, is_active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
            (store_id, name, sort_order, ts, ts),
        )
    category_ids = {
        row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM categories WHERE store_id = ?", (store_id,))
    }
    for pid, category, name, description, price, popularity, tags, available in SAMPLE_PRODUCTS:
        conn.execute(
            "INSERT INTO products (store_id, id, category_id, name, description, price, image_path, is_available, "
            "popularity_score, recommendation_tags, allergens, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (store_id, pid, category_ids[category], name, description, price, _image_path(pid),
             available, popularity, tags, SAMPLE_ALLERGENS.get(pid, ""), ts, ts),
        )
    return True


def _mark_sample_version(conn, store_id):
    conn.execute(
        "INSERT INTO store_settings (store_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(store_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (store_id, SAMPLE_MENU_VERSION_KEY, SAMPLE_MENU_VERSION, now_iso()),
    )


def upgrade_sample_menu(conn, store_id):
    """以前のサンプルメニューで作った店舗に、1回だけ新しいサンプル商品と画像を追加する。

    店舗が名前や画像を変えた商品・同じ ID や名前の商品がある場合は触らない。呼び出し側のトランザクション内で実行する。
    戻り値: 追加した商品の数（すでに適用済みなら None）。
    """
    row = conn.execute("SELECT value FROM store_settings WHERE store_id = ? AND key = ?", (store_id, SAMPLE_MENU_VERSION_KEY)).fetchone()
    if row is not None and row["value"] >= SAMPLE_MENU_VERSION:
        return None
    ts = now_iso()
    added = 0
    categories = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories WHERE store_id = ?", (store_id,))}
    for pid, category, name, description, price, popularity, tags, available in SAMPLE_PRODUCTS:
        # 以前の仮画像パス（.jpg）のままのサンプル商品だけ、新しいイラストに差し替える。
        conn.execute(
            "UPDATE products SET image_path = ?, updated_at = ? WHERE store_id = ? AND id = ? AND image_path = ?",
            (_image_path(pid), ts, store_id, pid, f"images/products/{pid}.jpg"),
        )
        # アレルギー情報が未登録で、名前もサンプルのままの商品にだけ仮の値を入れる。
        conn.execute(
            "UPDATE products SET allergens = ?, updated_at = ? WHERE store_id = ? AND id = ? AND name = ? AND allergens = ''",
            (SAMPLE_ALLERGENS.get(pid, ""), ts, store_id, pid, name),
        )
        if category not in categories:
            continue
        exists = conn.execute("SELECT 1 FROM products WHERE store_id = ? AND (id = ? OR name = ?)", (store_id, pid, name)).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO products (store_id, id, category_id, name, description, price, image_path, is_available, "
            "popularity_score, recommendation_tags, allergens, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (store_id, pid, categories[category], name, description, price, _image_path(pid), available, popularity, tags,
             SAMPLE_ALLERGENS.get(pid, ""), ts, ts),
        )
        added += 1
    _mark_sample_version(conn, store_id)
    return added
