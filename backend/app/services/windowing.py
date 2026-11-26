"""曝光时间与计算窗口的共享解析工具。

这里只处理日期/窗口匹配，避免接入服务和指标计算服务各自实现一套不一致的口径。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

_DATE_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})日?")
_ISO_DATETIME_RE = re.compile(
    r"(\d{4}-\d{1,2}-\d{1,2})[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.(\d+))?"
)


def _relative_day(value: str) -> date | None:
    text = value.strip().lower()
    today = date.today()
    mapping = {
        "today": 0,
        "now": 0,
        "今天": 0,
        "今日": 0,
        "yesterday": -1,
        "昨天": -1,
        "昨日": -1,
        "day before yesterday": -2,
        "前天": -2,
    }
    if text in mapping:
        return today + timedelta(days=mapping[text])
    return None


def parse_exposure_time(value: Any) -> tuple[str | None, str | None, str | None]:
    """把 CSV 中的曝光时间解析为标准文本和窗口日期。

    返回 ``(normalized, day_key, error)``。无法解析时前两项为 None，error 给中文原因。
    空值也按不可解析处理；调用方负责决定是否整行拒收。
    """
    if isinstance(value, datetime):
        normalized = value.replace(microsecond=0).isoformat(sep=" ")
        return normalized, value.date().isoformat(), None

    raw = str(value if value is not None else "").strip()
    if not raw:
        return None, None, "曝光时间为空或无法解析"

    relative = _relative_day(raw)
    if relative is not None:
        return f"{relative.isoformat()} 00:00:00", relative.isoformat(), None

    # 纯日期或常见日期时间格式。
    date_match = _DATE_RE.search(raw)
    if date_match:
        try:
            year, month, day = (int(part) for part in date_match.groups())
            day_value = date(year, month, day)
        except ValueError as exc:
            return None, None, f"曝光时间无法解析：{raw}（{exc}）"

        time_part = raw[date_match.end():].strip()
        if not time_part:
            return f"{day_value.isoformat()} 00:00:00", day_value.isoformat(), None

        clock = re.search(r"(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", time_part)
        if clock:
            try:
                hour = int(clock.group(1))
                minute = int(clock.group(2))
                second = int(clock.group(3) or 0)
                dt = datetime(year, month, day, hour, minute, second)
                return dt.isoformat(sep=" "), day_value.isoformat(), None
            except ValueError as exc:
                return None, None, f"曝光时间无法解析：{raw}（{exc}）"
        # 日期合法但后面的时钟片段看不懂，不能静默吞掉。
        return None, None, f"曝光时间无法解析：{raw}"

    # Unix 秒/毫秒时间戳。
    if re.fullmatch(r"\d{10}(?:\.\d+)?", raw):
        dt = datetime.fromtimestamp(float(raw))
        return dt.replace(microsecond=0).isoformat(sep=" "), dt.date().isoformat(), None
    if re.fullmatch(r"\d{13}", raw):
        dt = datetime.fromtimestamp(int(raw) / 1000)
        return dt.replace(microsecond=0).isoformat(sep=" "), dt.date().isoformat(), None

    # 最后交给 fromisoformat，兼容 2026-11-25T10:00:00Z。
    candidate = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        normalized = dt.replace(tzinfo=None, microsecond=0).isoformat(sep=" ")
        return normalized, dt.date().isoformat(), None
    except ValueError:
        pass

    return None, None, f"曝光时间无法解析：{raw}"


def parse_window_days(window: Any) -> tuple[str | None, str | None]:
    """从作业的“计算窗口”文本识别起止日期。

    单个日期按当天窗口处理；包含两个日期时按闭区间处理。识别不到时返回
    ``(None, None)``，调用方按“无法核对窗口”处理而不是误匹配日期。
    """
    text = str(window or "")
    matches = list(_DATE_RE.finditer(text))
    if not matches:
        return None, None

    days: list[str] = []
    for match in matches:
        try:
            year, month, day = (int(part) for part in match.groups())
            days.append(date(year, month, day).isoformat())
        except ValueError:
            continue
    if not days:
        return None, None
    if len(days) == 1:
        return days[0], days[0]
    return min(days[0], days[-1]), max(days[0], days[-1])


def day_in_window(day_key: str | None, window: Any) -> bool:
    start, end = parse_window_days(window)
    if start is None or end is None:
        # 没有明确日期边界的历史作业保持“读取全部相关曝光”的兼容行为。
        return True
    if not day_key:
        return False
    return start <= day_key <= end


def windows_for_day(day_key: str | None, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """返回日期能落入的、且带有可核对日期边界的作业。"""
    if not day_key:
        return []
    result: list[dict[str, Any]] = []
    for job in jobs:
        start, end = parse_window_days(job.get("计算窗口"))
        if start and end and start <= day_key <= end:
            result.append(job)
    return result
