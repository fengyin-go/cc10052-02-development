"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class CohortBuilderService(BaseModuleService):
    MODULE = "cohort_builder"
    ENTITY = "人群包"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["人群编码", "人群名称", "圈选条件", "人群规模", "刷新周期"]
    REQUIRED_FIELDS = ["人群编码", "人群名称", "圈选条件"]
    STATUS_ORDER = ["草稿", "已生成", "已过期"]
    ACTION_RULES = {"生成人群": "已生成", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = []
