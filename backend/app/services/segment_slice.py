"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class SegmentSliceService(BaseModuleService):
    MODULE = "segment_slice"
    ENTITY = "切片结果"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["切片批次", "实验编码", "切片维度", "切片值", "分组差异"]
    REQUIRED_FIELDS = ["切片批次", "实验编码", "切片维度"]
    STATUS_ORDER = ["待计算", "已计算", "计算失败"]
    ACTION_RULES = {"开始计算": "已计算", "重试计算": "计算失败"}
    NEGATIVE_ACTIONS = []
