"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ExpArchiveService(BaseModuleService):
    MODULE = "exp_archive"
    ENTITY = "归档实验"
    OPERATION_TERM = "归档"
    LIST_FIELDS = ["实验编码", "结束时间", "结论摘要", "归档位置", "保留等级"]
    REQUIRED_FIELDS = ["实验编码", "结束时间", "结论摘要"]
    STATUS_ORDER = ["待归档", "已归档", "恢复中"]
    ACTION_RULES = {"执行归档": "已归档", "恢复实验": "恢复中"}
    NEGATIVE_ACTIONS = []
