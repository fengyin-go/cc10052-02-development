"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class EventStreamService(BaseModuleService):
    MODULE = "event_stream"
    ENTITY = "事件定义"
    OPERATION_TERM = "接入"
    LIST_FIELDS = ["事件编码", "事件名称", "属性字段", "来源应用", "上报协议"]
    REQUIRED_FIELDS = ["事件编码", "事件名称", "属性字段"]
    STATUS_ORDER = ["待验证", "已验证", "已下线"]
    ACTION_RULES = {"验证事件": "已验证", "下线事件": "已下线"}
    NEGATIVE_ACTIONS = ["下线事件"]
