"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class MetricComputeService(BaseModuleService):
    MODULE = "metric_compute"
    ENTITY = "计算作业"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["作业名称", "指标编码", "计算窗口", "窗口水位", "作业状态"]
    REQUIRED_FIELDS = ["作业名称", "指标编码", "计算窗口"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "计算失败"]
    ACTION_RULES = {"启动作业": "执行中", "重试计算": "计算失败"}
    NEGATIVE_ACTIONS = []
