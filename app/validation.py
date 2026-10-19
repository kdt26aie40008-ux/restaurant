"""サーバー側の入力検証。画面側でも同じ規則で検証するが、最終判断は常にここで行う。"""

import re
import unicodedata

from .errors import bad_request

_FULLWIDTH_TABLE = str.maketrans("０１２３４５６７８９－ー−．", "0123456789---.")
_INT_RE = re.compile(r"^[0-9]+$")
_NEGATIVE_RE = re.compile(r"^-\s*\d+(\.\d+)?$")
_DECIMAL_RE = re.compile(r"^-?\d*\.\d+$|^-?\d+\.\d*$")
PRODUCT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
TAG_RE = re.compile(r"^[a-z0-9_]{1,24}$")
IMAGE_PATH_RE = re.compile(r"^images/products/[A-Za-z0-9_-]{1,64}\.(png|jpg|jpeg|webp|svg)$")
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
MAX_TAGS = 10


def validate_party_size(value, max_party_size):
    hint = f"1〜{max_party_size}の整数（例: 2）で入力してください。"
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise bad_request("PARTY_SIZE_REQUIRED", "利用人数が入力されていません。", hint)
    if isinstance(value, bool):
        raise bad_request("PARTY_SIZE_INVALID", "利用人数の形式が正しくありません。", hint)
    if isinstance(value, float):
        raise bad_request("PARTY_SIZE_DECIMAL", "利用人数に小数は入力できません。", hint)
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        text = value.strip().translate(_FULLWIDTH_TABLE)
        if _NEGATIVE_RE.match(text):
            raise bad_request("PARTY_SIZE_NEGATIVE", "利用人数にマイナスの値は入力できません。", hint)
        if _DECIMAL_RE.match(text):
            raise bad_request("PARTY_SIZE_DECIMAL", "利用人数に小数は入力できません。", hint)
        if not _INT_RE.match(text):
            raise bad_request("PARTY_SIZE_NOT_NUMBER", "利用人数は数字で入力してください。", hint)
        number = int(text)
    else:
        raise bad_request("PARTY_SIZE_INVALID", "利用人数の形式が正しくありません。", hint)

    if number < 0:
        raise bad_request("PARTY_SIZE_NEGATIVE", "利用人数にマイナスの値は入力できません。", hint)
    if number == 0:
        raise bad_request("PARTY_SIZE_ZERO", "利用人数は1人以上にしてください。", hint)
    if number > max_party_size:
        raise bad_request(
            "PARTY_SIZE_TOO_LARGE",
            f"利用人数が多すぎます（最大{max_party_size}人）。",
            f"入力内容を確認してください。{max_party_size}人を超える場合は、店舗設定の「最大人数」を変更してください。",
        )
    return number


def strict_int(value, label, min_value, max_value, code="INVALID_NUMBER"):
    """bool・小数・数字以外の文字を拒否して整数に変換する。"""
    hint = f"{label}は{min_value}〜{max_value}の整数で指定してください。"
    if isinstance(value, bool) or value is None:
        raise bad_request(code, f"{label}が正しくありません。", hint)
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,15}", value.strip()):
        number = int(value.strip())
    else:
        raise bad_request(code, f"{label}が正しくありません。", hint)
    if not min_value <= number <= max_value:
        raise bad_request(code, f"{label}が範囲外です。", hint)
    return number


def strict_bool(value, label):
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise bad_request("INVALID_FLAG", f"{label}の値が正しくありません。", "true / false で指定してください。")


def clean_text(value, label, max_length, required=True, forbid_formula=True):
    """前後の空白を除き、制御文字・長さ・CSV数式になり得る先頭文字を検証する。"""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise bad_request("INVALID_TEXT", f"{label}の形式が正しくありません。", "文字列で入力してください。")
    text = unicodedata.normalize("NFC", value).strip()
    if required and not text:
        raise bad_request("TEXT_REQUIRED", f"{label}が入力されていません。", f"{label}を入力してください。")
    if len(text) > max_length:
        raise bad_request("TEXT_TOO_LONG", f"{label}が長すぎます（最大{max_length}文字）。", f"{max_length}文字以内にしてください。")
    if any(unicodedata.category(ch) == "Cc" for ch in text):
        raise bad_request("TEXT_INVALID_CHAR", f"{label}に使用できない制御文字が含まれています。", "改行やタブを取り除いてください。")
    if forbid_formula and text.startswith(FORMULA_PREFIXES):
        raise bad_request(
            "TEXT_FORMULA_PREFIX",
            f"{label}の先頭に = + - @ は使えません。",
            "Excelで数式として誤動作するのを防ぐためです。先頭の記号を取り除いてください。",
        )
    return text


def validate_product_id(value):
    if not isinstance(value, str) or not PRODUCT_ID_RE.match(value):
        raise bad_request(
            "INVALID_PRODUCT_ID",
            "商品IDの形式が正しくありません。",
            "商品IDは半角英数字・ハイフン・アンダースコアの1〜32文字（例: P001）にしてください。",
        )
    return value


def parse_tags(value):
    """'spicy;sweet' または ['spicy', 'sweet'] を検証済みの重複なしリストにする。"""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(";")]
    elif isinstance(value, list):
        parts = value
    else:
        raise bad_request("INVALID_TAGS", "おすすめタグの形式が正しくありません。", "タグは ; 区切りの文字列で指定してください。")
    tags = []
    for part in parts:
        if not isinstance(part, str) or not TAG_RE.match(part):
            raise bad_request(
                "INVALID_TAGS",
                f"おすすめタグ「{part}」の形式が正しくありません。",
                "タグは半角小文字英数字とアンダースコア（例: spicy;age_child）で、; で区切ってください。",
            )
        if part not in tags:
            tags.append(part)
    if len(tags) > MAX_TAGS:
        raise bad_request("INVALID_TAGS", f"おすすめタグが多すぎます（最大{MAX_TAGS}個）。", "タグの数を減らしてください。")
    return tags


def validate_image_path(value):
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or not IMAGE_PATH_RE.match(value.strip()):
        raise bad_request(
            "INVALID_IMAGE_PATH",
            "画像パスの形式が正しくありません。",
            "images/products/P001.jpg のように、images/products/ 以下の png・jpg・jpeg・webp・svg ファイルを指定してください（空欄も可）。",
        )
    return value.strip()
