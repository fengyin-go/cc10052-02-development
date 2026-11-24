"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class SampleRatioService(BaseModuleService):
    MODULE = "sample_ratio"
    ENTITY = "核对结果"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["核对批次", "实验编码", "计划比例", "实际比例", "偏移量"]
    REQUIRED_FIELDS = ["核对批次", "实验编码", "计划比例"]
    STATUS_ORDER = ["待核对", "已核对", "偏移超标"]
    ACTION_RULES = {"执行核对": "已核对", "告警偏移": "偏移超标"}
    NEGATIVE_ACTIONS = []
