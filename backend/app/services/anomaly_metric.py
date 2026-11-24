"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class AnomalyMetricService(BaseModuleService):
    MODULE = "anomaly_metric"
    ENTITY = "异常事件"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["异常编号", "指标编码", "基线区间", "观测值", "异常评分"]
    REQUIRED_FIELDS = ["异常编号", "指标编码", "基线区间"]
    STATUS_ORDER = ["待确认", "已确认", "误报"]
    ACTION_RULES = {"确认异常": "已确认", "标记误报": "误报"}
    NEGATIVE_ACTIONS = []
