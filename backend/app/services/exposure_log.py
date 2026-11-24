"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ExposureLogService(BaseModuleService):
    MODULE = "exposure_log"
    ENTITY = "曝光记录"
    OPERATION_TERM = "接入"
    LIST_FIELDS = ["日志批次", "实验编码", "用户标识", "分组名称", "曝光时间"]
    REQUIRED_FIELDS = ["日志批次", "实验编码", "用户标识"]
    STATUS_ORDER = ["已接收", "已去重", "异常丢弃"]
    ACTION_RULES = {"执行去重": "已去重", "丢弃异常": "异常丢弃"}
    NEGATIVE_ACTIONS = []
