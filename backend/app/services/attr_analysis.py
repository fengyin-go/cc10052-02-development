"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class AttrAnalysisService(BaseModuleService):
    MODULE = "attr_analysis"
    ENTITY = "归因结果"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["归因批次", "实验编码", "贡献因子", "贡献权重", "归因方法"]
    REQUIRED_FIELDS = ["归因批次", "实验编码", "贡献因子"]
    STATUS_ORDER = ["排队中", "已归因", "归因失败"]
    ACTION_RULES = {"开始归因": "已归因", "重试归因": "归因失败"}
    NEGATIVE_ACTIONS = []
