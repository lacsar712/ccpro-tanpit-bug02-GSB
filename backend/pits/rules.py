"""鞣坑规矩：浸液酸碱度登记与放液门槛。"""

import math

from pits.models import Pit

MIN_PH = 3.5
MAX_PH = 5.0

# 登记浸液时可接受的物理酸碱区间；3.5～5.0 只是放液门槛，不是登记门槛。
MIN_SAMPLE_PH = 0.0
MAX_SAMPLE_PH = 14.0


class RuleError(ValueError):
    pass


def latest_ph(pit: Pit) -> float | None:
    sample = pit.samples.order_by("-taken_at", "-id").first()
    return None if sample is None else sample.ph


def assert_valid_ph(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleError("酸碱度必须是数字")
    if not math.isfinite(value):
        raise RuleError("酸碱度必须是有效数字，不能为无穷或空值")
    if value < MIN_SAMPLE_PH or value > MAX_SAMPLE_PH:
        raise RuleError(f"酸碱度须在 {MIN_SAMPLE_PH:g}～{MAX_SAMPLE_PH:g} 之间")


def assert_can_set_status(pit: Pit, new_status: str) -> None:
    allowed = {Pit.STATUS_FILL, Pit.STATUS_TANNING, Pit.STATUS_DRAINED}
    if new_status not in allowed:
        raise RuleError(f"无效状态：{new_status}")
    if new_status != Pit.STATUS_DRAINED:
        return
    ph = latest_ph(pit)
    if ph is None:
        raise RuleError("该坑尚无浸液酸碱记录，不能放液")
    if ph < MIN_PH or ph > MAX_PH:
        raise RuleError(f"最近酸碱度 {ph} 不在 {MIN_PH}～{MAX_PH}，不能放液")
