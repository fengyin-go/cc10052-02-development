"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ConclusionReviewService(BaseModuleService):
    MODULE = "conclusion_review"
    ENTITY = "评审记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["记录编号", "实验编码", "评审结论", "评审角色", "评审时间"]
    REQUIRED_FIELDS = ["记录编号", "实验编码", "评审结论"]
    STATUS_ORDER = ["待评审", "已通过", "已驳回"]
    ACTION_RULES = {"通过记录": "已通过", "驳回记录": "已驳回"}
    NEGATIVE_ACTIONS = ["驳回记录"]
