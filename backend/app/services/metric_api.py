"""指标查询服务领域服务：管理指标查询接口的上线、调用限额控制与访问审计。

数据链路位置：数据服务层，是实验指标对外提供的受控出口，消费指标口径中心的
生效口径与各环节产出数据，按刷新策略和调用限额对外提供查询。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class MetricApiService(BaseModuleService):
    MODULE = "metric_api"
    ENTITY = "查询接口"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["接口编码", "指标编码", "服务对象", "刷新策略", "调用限额"]
    REQUIRED_FIELDS = ["接口编码", "指标编码", "服务对象"]
    STATUS_ORDER = ["待上线", "已上线", "已下线"]
    ACTION_RULES = {"上线接口": "已上线", "下线接口": "已下线"}
    NEGATIVE_ACTIONS = ["下线接口"]

    # ---- 业务扩展：接口治理与限额控制 ----

    def online_api(self, entry_id: int) -> dict[str, Any]:
        """上线查询接口：校验口径生效并初始化调用限额。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待上线":
            return {"ok": False, "message": "仅待上线接口可上线"}
        metric_code = str(entry.get("指标编码", ""))
        metric = next((m for m in store.rows("metric_catalog") if m.get("指标编码") == metric_code), None)
        if metric is None or metric.get("status") != "已生效":
            return {"ok": False, "message": f"口径 {metric_code} 未生效，接口禁止上线"}
        if not str(entry.get("调用限额", "")).strip():
            entry["调用限额"] = "1000"
        entry["status"] = "已上线"
        entry["pending"] = False
        entry["上线时间"] = datetime.now(timezone.utc).isoformat()
        entry["今日调用"] = "0"
        return {"ok": True, "message": f"接口 {entry.get('接口编码')} 已上线（限额 {entry['调用限额']}）"}

    def offline_api(self, entry_id: int, reason: str) -> dict[str, Any]:
        """下线查询接口并记录下线原因供审计追溯。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已上线":
            return {"ok": False, "message": "仅已上线接口可下线"}
        if not reason.strip():
            return {"ok": False, "message": "下线必须填写原因"}
        entry["status"] = "已下线"
        entry["pending"] = False
        entry["abnormal"] = True
        entry["下线原因"] = reason
        return {"ok": True, "message": f"接口已下线（{reason}）"}

    def handle_query(self, entry_id: int) -> dict[str, Any]:
        """处理一次指标查询：扣减限额并返回口径与数据来源。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已上线":
            return {"ok": False, "message": "接口不可用或未上线"}
        limit_str = str(entry.get("调用限额", "1000"))
        used = int(str(entry.get("今日调用", "0")) or 0)
        try:
            limit = int(float(limit_str))
        except ValueError:
            limit = 1000
        if used >= limit:
            entry["abnormal"] = True
            return {"ok": False, "message": f"已达调用限额 {limit}"}
        entry["今日调用"] = str(used + 1)
        metric_code = str(entry.get("指标编码", ""))
        result_rows = [
            {"作业名称": r.get("作业名称", ""), "窗口水位": r.get("窗口水位", "")}
            for r in store.rows("metric_compute")
            if r.get("指标编码") == metric_code and r.get("status") == "已完成"
        ]
        return {
            "ok": True,
            "message": f"查询成功（剩余 {limit - used - 1} 次）",
            "指标编码": metric_code,
            "刷新策略": entry.get("刷新策略", ""),
            "数据结果": result_rows,
        }

    def batch_online(self) -> dict[str, Any]:
        """批量上线全部待上线接口。"""
        pending = [r for r in store.rows(self.MODULE) if r.get("status") == "待上线"]
        done = sum(1 for r in pending if self.online_api(int(r.get("id", 0))).get("ok"))
        return {"ok": True, "message": f"批量上线完成 {done}/{len(pending)}"}

    def consume_from_metric_catalog(self, metric_code: str) -> dict[str, Any]:
        """读取指标口径中心的口径定义，确定接口返回口径。"""
        metric = next((m for m in store.rows("metric_catalog") if m.get("指标编码") == metric_code), None)
        if metric is None:
            return {"ok": False, "message": f"口径 {metric_code} 不存在"}
        return {
            "ok": True,
            "计算口径": metric.get("计算口径", ""),
            "口径版本": metric.get("口径版本", ""),
            "状态": metric.get("status", ""),
        }

    def consume_from_exp_archive(self, exp_code: str) -> list[dict[str, Any]]:
        """读取实验归档库的历史结论，作为接口的历史数据说明。"""
        return [
            {"实验编码": r.get("实验编码", ""), "结论摘要": r.get("结论摘要", ""), "保留等级": r.get("保留等级", "")}
            for r in store.rows("exp_archive")
            if r.get("实验编码") == exp_code and r.get("status") == "已归档"
        ]

    def reset_daily_quota(self) -> dict[str, Any]:
        """重置全部上线接口的今日调用计数。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("status") == "已上线"]
        for row in rows:
            row["今日调用"] = "0"
            row["abnormal"] = False
        return {"ok": True, "message": f"已重置 {len(rows)} 个接口的今日调用"}
