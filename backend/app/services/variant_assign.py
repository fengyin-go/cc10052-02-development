"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class VariantAssignService(BaseModuleService):
    MODULE = "variant_assign"
    ENTITY = "分组结果"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["分配批次", "用户标识", "实验编码", "分组名称", "分配时间"]
    REQUIRED_FIELDS = ["分配批次", "用户标识", "实验编码"]
    STATUS_ORDER = ["已分配", "重新分配", "已剔除"]
    ACTION_RULES = {"重新分配": "重新分配", "剔除用户": "已剔除"}
    NEGATIVE_ACTIONS = []
