"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class AaCheckService(BaseModuleService):
    MODULE = "aa_check"
    ENTITY = "AA检验"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["检验编码", "实验编码", "检验窗口", "检验结论", "P值分布"]
    REQUIRED_FIELDS = ["检验编码", "实验编码", "检验窗口"]
    STATUS_ORDER = ["待检验", "已通过", "未通过"]
    ACTION_RULES = {"执行AA检验": "已通过", "重跑检验": "未通过"}
    NEGATIVE_ACTIONS = []
