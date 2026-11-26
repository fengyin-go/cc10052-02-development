"""路由汇总：扩展语义路由先注册，通用资源路由最后兜底。"""
from app.routers import extensions, resource

ROUTERS = [extensions, resource]
