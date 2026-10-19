"""ルールベースのおすすめ。外部APIは使わず、商品の人気度とタグから理由付きで選ぶ。

好みの入力はサーバーに保存しない（推薦の計算にだけ使う）。
年齢層は参考程度の加点にとどめ、好みとタグの一致を優先する。
"""

from .allergens import parse_allergens
from .errors import bad_request
from .validation import strict_bool, strict_int

AGE_GROUPS = {"child": "お子さま", "youth": "10〜20代", "adult": "30〜50代", "senior": "60代以上"}
PREFERENCE_TAGS = {
    "likes_spicy": ("spicy", "辛いものがお好きな方に"),
    "likes_sweet": ("sweet", "甘いものがお好きな方に"),
    "prefers_light": ("light", "軽めのお食事に"),
}
# 年齢層に関係なく飲酒できない可能性がある層には、酒類タグの商品をおすすめしない。
ALCOHOL_BLOCKED_AGE_GROUPS = {"child", "youth"}

POPULARITY_WEIGHT = 0.3
PREFERENCE_POINTS = 40
CATEGORY_POINTS = 25
AGE_POINTS = 10
HEAVY_PENALTY = 15
POPULAR_THRESHOLD = 70
MAX_PER_CATEGORY = 2
DEFAULT_LIMIT = 6


def normalize_preferences(raw):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise bad_request("INVALID_PREFERENCES", "おすすめ条件の形式が正しくありません。", "条件を選び直してください。")
    age_group = raw.get("age_group") or None
    if age_group is not None and age_group not in AGE_GROUPS:
        raise bad_request("INVALID_PREFERENCES", "年齢層の指定が正しくありません。", "一覧から選んでください。")
    prefs = {"age_group": age_group, "preferred_category_id": None, "avoid_allergens": parse_allergens(raw.get("avoid_allergens") or [])}
    for key in PREFERENCE_TAGS:
        prefs[key] = strict_bool(raw[key], "好み") if raw.get(key) is not None else False
    if raw.get("preferred_category_id") not in (None, ""):
        prefs["preferred_category_id"] = strict_int(raw["preferred_category_id"], "好みのカテゴリ", 1, 2**31)
    return prefs


def has_preferences(prefs):
    return bool(prefs["age_group"] or prefs["preferred_category_id"] or any(prefs[key] for key in PREFERENCE_TAGS))


def _score(product, prefs):
    tags = set(product["tags"])
    score = product["popularity_score"] * POPULARITY_WEIGHT
    reasons = []
    matched = False
    for key, (tag, reason) in PREFERENCE_TAGS.items():
        if prefs[key] and tag in tags:
            score += PREFERENCE_POINTS
            reasons.append(reason)
            matched = True
    if prefs["prefers_light"] and "hearty" in tags:
        score -= HEAVY_PENALTY
    if prefs["preferred_category_id"] == product["category_id"]:
        score += CATEGORY_POINTS
        reasons.append(f"お好みの「{product['category_name']}」から")
        matched = True
    if prefs["age_group"] and f"age_{prefs['age_group']}" in tags:
        score += AGE_POINTS
        reasons.append(f"{AGE_GROUPS[prefs['age_group']]}に好評")
        matched = True
    if product["popularity_score"] >= POPULAR_THRESHOLD:
        reasons.append("人気商品")
    return score, reasons or ["定番メニュー"], matched


def recommend(products, raw_prefs=None, limit=DEFAULT_LIMIT):
    """戻り値: {"mode": "personalized" | "popular", "items": [{"product_id", "reasons", "score"}]}"""
    prefs = normalize_preferences(raw_prefs)
    candidates = [p for p in products if p["is_available"] and p["category_active"]]
    if prefs["avoid_allergens"]:
        # アレルギーを選んだ場合、そのアレルゲンを含む商品はおすすめしない。
        avoid = set(prefs["avoid_allergens"])
        candidates = [p for p in candidates if not avoid & set(p.get("allergens", []))]
    if prefs["age_group"] in ALCOHOL_BLOCKED_AGE_GROUPS:
        candidates = [p for p in candidates if "alcohol" not in p["tags"]]

    scored = []
    any_match = False
    for product in candidates:
        score, reasons, matched = _score(product, prefs)
        any_match = any_match or matched
        scored.append((score, product, reasons))
    scored.sort(key=lambda entry: (-entry[0], -entry[1]["popularity_score"], entry[1]["id"]))

    # 同じカテゴリばかりにならないよう、まずカテゴリごとに上限を設けて選び、足りなければ残りから補う。
    picked, per_category = [], {}
    for entry in scored:
        category = entry[1]["category_id"]
        if per_category.get(category, 0) < MAX_PER_CATEGORY:
            picked.append(entry)
            per_category[category] = per_category.get(category, 0) + 1
        if len(picked) == limit:
            break
    if len(picked) < limit:
        picked += [entry for entry in scored if entry not in picked][: limit - len(picked)]

    mode = "personalized" if has_preferences(prefs) and any_match else "popular"
    return {
        "mode": mode,
        "items": [{"product_id": p["id"], "reasons": reasons, "score": round(score, 1)} for score, p, reasons in picked],
    }
