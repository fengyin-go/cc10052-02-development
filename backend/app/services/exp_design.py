"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ExpDesignService(BaseModuleService):
    MODULE = "exp_design"
    ENTITY = "实验方案"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["实验编码", "实验名称", "分组策略", "流量比例", "假设指标"]
    REQUIRED_FIELDS = ["实验编码", "实验名称", "分组策略"]
    STATUS_ORDER = ["设计中", "待审批", "运行中", "已结束"]
    ACTION_RULES = {"提交审批": "待审批", "启动实验": "运行中", "结束实验": "已结束"}
    NEGATIVE_ACTIONS = []
