"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class WindowSchedulerService(BaseModuleService):
    MODULE = "window_scheduler"
    ENTITY = "调度计划"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["计划名称", "指标编码", "窗口粒度", "下次窗口", "上次状态"]
    REQUIRED_FIELDS = ["计划名称", "指标编码", "窗口粒度"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "执行失败"]
    ACTION_RULES = {"立即执行": "执行中", "暂停计划": "待执行"}
    NEGATIVE_ACTIONS = []
