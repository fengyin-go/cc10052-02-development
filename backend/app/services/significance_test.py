"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class SignificanceTestService(BaseModuleService):
    MODULE = "significance_test"
    ENTITY = "检验结果"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["检验批次", "实验编码", "指标编码", "P值", "置信区间"]
    REQUIRED_FIELDS = ["检验批次", "实验编码", "指标编码"]
    STATUS_ORDER = ["待检验", "已检验", "检验异常"]
    ACTION_RULES = {"执行检验": "已检验", "重试检验": "检验异常"}
    NEGATIVE_ACTIONS = []
