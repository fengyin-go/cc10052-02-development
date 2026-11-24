"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ReportDistService(BaseModuleService):
    MODULE = "report_dist"
    ENTITY = "分发记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["分发批次", "报告编码", "接收方", "分发渠道", "送达状态"]
    REQUIRED_FIELDS = ["分发批次", "报告编码", "接收方"]
    STATUS_ORDER = ["排队中", "已送达", "送达失败"]
    ACTION_RULES = {"开始分发": "已送达", "重试分发": "送达失败"}
    NEGATIVE_ACTIONS = []
