"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class MetricCatalogService(BaseModuleService):
    MODULE = "metric_catalog"
    ENTITY = "指标定义"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["指标编码", "指标名称", "计算口径", "聚合窗口", "口径版本"]
    REQUIRED_FIELDS = ["指标编码", "指标名称", "计算口径"]
    STATUS_ORDER = ["草稿", "已生效", "已废弃"]
    ACTION_RULES = {"生效口径": "已生效", "废弃口径": "已废弃"}
    NEGATIVE_ACTIONS = []
