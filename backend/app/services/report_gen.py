"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ReportGenService(BaseModuleService):
    MODULE = "report_gen"
    ENTITY = "分析报告"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["报告编码", "实验编码", "报告模板", "数据窗口", "生成状态"]
    REQUIRED_FIELDS = ["报告编码", "实验编码", "报告模板"]
    STATUS_ORDER = ["排队中", "生成中", "已完成", "生成失败"]
    ACTION_RULES = {"开始生成": "生成中", "重试生成": "生成失败"}
    NEGATIVE_ACTIONS = []
