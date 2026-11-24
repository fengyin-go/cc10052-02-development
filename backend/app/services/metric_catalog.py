"""指标口径中心领域服务：管理实验指标的计算口径、聚合窗口、版本生效与废弃。

数据链路位置：口径治理层，是指标计算作业的唯一口径来源，负责把生效口径
下发给计算作业与指标查询服务，并阻断对已废弃口径的引用。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class MetricCatalogService(BaseModuleService):
    MODULE = "metric_catalog"
    ENTITY = "指标定义"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["指标编码", "指标名称", "计算口径", "聚合窗口", "口径版本"]
    REQUIRED_FIELDS = ["指标编码", "指标名称", "计算口径"]
    STATUS_ORDER = ["草稿", "已生效", "已废弃"]
    ACTION_RULES = {"生效口径": "已生效", "废弃口径": "已废弃"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：口径治理与版本下发 ----

    def activate_metric(self, entry_id: int) -> dict[str, Any]:
        """生效指标口径并自动创建指标计算作业。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "草稿":
            return {"ok": False, "message": "仅草稿口径可生效"}
        entry["status"] = "已生效"
        entry["pending"] = False
        compute_rows = store.rows("metric_compute")
        compute_rows.append({
            "id": max((int(r.get("id", 0)) for r in compute_rows), default=0) + 1,
            "作业名称": f"{entry.get('指标名称', '指标')}日作业",
            "指标编码": entry.get("指标编码", ""),
            "计算窗口": entry.get("聚合窗口", "日窗口"),
            "窗口水位": datetime.now(timezone.utc).isoformat(),
            "作业状态": "待执行",
            "status": "待执行",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"口径 {entry.get('指标编码')} 已生效并创建计算作业"}

    def deprecate_metric(self, entry_id: int) -> dict[str, Any]:
        """废弃指标口径并联动下线引用该口径的查询接口。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "口径不存在"}
        metric_code = str(entry.get("指标编码", ""))
        entry["status"] = "已废弃"
        entry["pending"] = False
        api_rows = [
            a for a in store.rows("metric_api")
            if a.get("指标编码") == metric_code and a.get("status") == "已上线"
        ]
        for api in api_rows:
            api["status"] = "已下线"
            api["pending"] = False
        return {"ok": True, "message": f"口径已废弃，联动下线 {len(api_rows)} 个查询接口"}

    def new_version(self, entry_id: int, new_formula: str) -> dict[str, Any]:
        """基于当前口径升版本，保留历史版本供追溯。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or not new_formula.strip():
            return {"ok": False, "message": "口径不存在或新公式为空"}
        old_version = str(entry.get("口径版本", "v1"))
        version_num = int(old_version.lstrip("v")) if old_version.lstrip("v").isdigit() else 1
        rows = store.rows(self.MODULE)
        rows.append({
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "指标编码": entry.get("指标编码", ""),
            "指标名称": entry.get("指标名称", ""),
            "计算口径": new_formula,
            "聚合窗口": entry.get("聚合窗口", "日窗口"),
            "口径版本": f"v{version_num + 1}",
            "status": "草稿",
            "pending": True,
            "abnormal": False,
        })
        entry["status"] = "已废弃"
        entry["pending"] = False
        return {"ok": True, "message": f"新版本 v{version_num + 1} 已创建，旧版本 {old_version} 归档"}

    def consume_from_event_stream(self) -> list[dict[str, Any]]:
        """读取事件接入层已验证事件，生成口径候选建议。"""
        suggestions = []
        for evt in store.rows("event_stream"):
            if evt.get("status") != "已验证":
                continue
            code = str(evt.get("事件编码", ""))
            if not code:
                continue
            suggestions.append({
                "建议指标编码": f"M-{code}",
                "建议口径": f"count({code})/曝光量",
                "来源事件": evt.get("事件名称", ""),
            })
        return suggestions

    def produce_to_window_scheduler(self, entry_id: int) -> dict[str, Any]:
        """把生效口径注册到窗口调度器，建立滚动计算计划。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已生效":
            return {"ok": False, "message": "仅生效口径可注册调度"}
        scheduler_rows = store.rows("window_scheduler")
        scheduler_rows.append({
            "id": max((int(r.get("id", 0)) for r in scheduler_rows), default=0) + 1,
            "计划名称": f"{entry.get('指标名称', '指标')}滚动计划",
            "指标编码": entry.get("指标编码", ""),
            "窗口粒度": entry.get("聚合窗口", "日窗口"),
            "下次窗口": datetime.now(timezone.utc).isoformat(),
            "上次状态": "待执行",
            "status": "待执行",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"调度计划已创建（{entry.get('聚合窗口', '日窗口')}）"}

    def list_effective_metrics(self) -> list[dict[str, Any]]:
        """列出全部生效口径，供指标查询服务上线接口引用。"""
        return [
            {
                "指标编码": r.get("指标编码", ""),
                "指标名称": r.get("指标名称", ""),
                "计算口径": r.get("计算口径", ""),
                "口径版本": r.get("口径版本", ""),
            }
            for r in store.rows(self.MODULE) if r.get("status") == "已生效"
        ]

    def check_formula_usage(self, formula_keyword: str) -> list[dict[str, Any]]:
        """检索包含关键字的口径，供指标异常检测定位异常口径。"""
        if not formula_keyword.strip():
            return []
        return [
            {"指标编码": r.get("指标编码", ""), "计算口径": r.get("计算口径", ""), "状态": r.get("status", "")}
            for r in store.rows(self.MODULE)
            if formula_keyword in str(r.get("计算口径", ""))
        ]
