"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class MetricApiService(BaseModuleService):
    MODULE = "metric_api"
    ENTITY = "查询接口"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["接口编码", "指标编码", "服务对象", "刷新策略", "调用限额"]
    REQUIRED_FIELDS = ["接口编码", "指标编码", "服务对象"]
    STATUS_ORDER = ["待上线", "已上线", "已下线"]
    ACTION_RULES = {"上线接口": "已上线", "下线接口": "已下线"}
    NEGATIVE_ACTIONS = ["下线接口"]
