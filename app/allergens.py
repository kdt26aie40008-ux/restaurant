"""アレルギー（アレルゲン）の定義と検証。

表示義務のある特定原材料8品目に、問い合わせの多い大豆・ごまを加えた10品目を扱う。
画面には日本語名を出し、DB・CSV にはコード（; 区切り）で保存する。CSV では日本語名でも受け付ける。
"""

from .errors import bad_request

# コード: 表示名（並び順は画面の表示順）
ALLERGENS = {
    "egg": "卵",
    "milk": "乳",
    "wheat": "小麦",
    "shrimp": "えび",
    "crab": "かに",
    "buckwheat": "そば",
    "peanut": "落花生",
    "walnut": "くるみ",
    "soy": "大豆",
    "sesame": "ごま",
}
_BY_NAME = {name: code for code, name in ALLERGENS.items()}


def parse_allergens(value):
    """'egg;milk' や ['卵', '乳'] を、定義順に並んだ重複なしのコードのリストにする。"""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("、", ";").replace(",", ";").split(";")]
    elif isinstance(value, list):
        parts = value
    else:
        raise bad_request("INVALID_ALLERGENS", "アレルギー情報の形式が正しくありません。", "egg;milk のように ; 区切りで指定してください。")
    codes = set()
    for part in parts:
        if part == "":
            continue
        if not isinstance(part, str):
            raise bad_request("INVALID_ALLERGENS", "アレルギー情報の形式が正しくありません。", "egg;milk のように ; 区切りで指定してください。")
        code = part if part in ALLERGENS else _BY_NAME.get(part)
        if code is None:
            raise bad_request(
                "INVALID_ALLERGENS", f"アレルギー「{part}」は登録できない項目です。",
                "使える項目: " + "、".join(f"{name}({code})" for code, name in ALLERGENS.items()),
            )
        codes.add(code)
    return [code for code in ALLERGENS if code in codes]


def to_text(codes):
    return ";".join(codes)
