"""日本時間の扱い。日本は夏時間がないため固定オフセット +09:00 を使う（Windows で tzdata 不要）。"""

import re
from datetime import date, datetime, timedelta, timezone

from .errors import bad_request

JST = timezone(timedelta(hours=9), "JST")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def now():
    return datetime.now(JST)


def now_iso():
    return now().isoformat(timespec="seconds")


def today():
    return now().date()


def parse_date(value, label="日付"):
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise bad_request("INVALID_DATE", f"{label}の形式が正しくありません。", "YYYY-MM-DD の形式（例: 2026-10-19）で指定してください。")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise bad_request("INVALID_DATE", f"{label}が存在しない日付です。", "正しい日付を指定してください。") from None


def day_start_iso(d):
    return f"{d.isoformat()}T00:00:00+09:00"


def range_bounds(date_from, date_to):
    """date_from〜date_to（両端を含む）を ordered_at の比較用文字列に変換する。"""
    return day_start_iso(date_from), day_start_iso(date_to + timedelta(days=1))
