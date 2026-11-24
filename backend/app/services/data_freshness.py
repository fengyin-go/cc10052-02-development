"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class DataFreshnessService(BaseModuleService):
    MODULE = "data_freshness"
    ENTITY = "时效记录"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["监控对象", "数据域", "最新水位", "延迟分钟", "时效等级"]
    REQUIRED_FIELDS = ["监控对象", "数据域", "最新水位"]
    STATUS_ORDER = ["新鲜", "延迟", "过期"]
    ACTION_RULES = {"重算水位": "延迟", "忽略告警": "过期"}
    NEGATIVE_ACTIONS = ["忽略告警"]
