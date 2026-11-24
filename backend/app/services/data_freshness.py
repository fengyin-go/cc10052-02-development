"""数据时效监控领域服务：管理指标计算的数据新鲜度、延迟分钟统计与告警处置。

数据链路位置：质量监控层，全链路的时效哨兵，消费各计算环节回写的水位数据，
把延迟与过期告警推送给指标异常检测联动处置。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DataFreshnessService(BaseModuleService):
    MODULE = "data_freshness"
    ENTITY = "时效记录"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["监控对象", "数据域", "最新水位", "延迟分钟", "时效等级"]
    REQUIRED_FIELDS = ["监控对象", "数据域", "最新水位"]
    STATUS_ORDER = ["新鲜", "延迟", "过期"]
    ACTION_RULES = {"重算水位": "延迟", "忽略告警": "过期"}
    NEGATIVE_ACTIONS = ["忽略告警"]

    # ---- 业务扩展：水位重算与延迟告警 ----

    def recalc_watermark(self, entry_id: int) -> dict[str, Any]:
        """重算监控对象水位，按延迟分钟重新分级时效等级。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "监控对象不存在"}
        delay_str = str(entry.get("延迟分钟", "0"))
        try:
            delay = int(delay_str) if delay_str.isdigit() else 0
        except ValueError:
            delay = 0
        if delay <= 30:
            level = "新鲜"
        elif delay <= 120:
            level = "延迟"
        else:
            level = "过期"
        entry["时效等级"] = level
        entry["status"] = level
        entry["pending"] = level != "新鲜"
        entry["最新水位"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": f"水位已重算，时效等级 {level}（延迟 {delay} 分钟）"}

    def ignore_alert(self, entry_id: int, reason: str) -> dict[str, Any]:
        """忽略时效告警并把处置记录同步给指标异常检测。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") == "新鲜":
            return {"ok": False, "message": "新鲜对象无需忽略告警"}
        if not reason.strip():
            return {"ok": False, "message": "忽略告警必须填写原因"}
        entry["status"] = "过期"
        entry["pending"] = False
        entry["abnormal"] = True
        entry["忽略原因"] = reason
        anomaly_rows = store.rows("anomaly_metric")
        anomaly_rows.append({
            "id": max((int(r.get("id", 0)) for r in anomaly_rows), default=0) + 1,
            "异常编号": f"AN-DF-{entry_id}",
            "指标编码": str(entry.get("监控对象", "")).split(":")[-1],
            "基线区间": str(entry.get("数据域", "")),
            "观测值": f"延迟 {entry.get('延迟分钟', '0')} 分钟被忽略",
            "异常评分": "0.6",
            "status": "待确认",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "告警已忽略并登记异常事件待确认"}

    def batch_recalc_domain(self, domain: str) -> dict[str, Any]:
        """按数据域批量重算水位。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("数据域") == domain]
        if not rows:
            return {"ok": False, "message": f"数据域 {domain} 无监控对象"}
        done = sum(1 for r in rows if self.recalc_watermark(int(r.get("id", 0))).get("ok"))
        return {"ok": True, "message": f"数据域 {domain} 重算完成 {done}/{len(rows)}"}

    def consume_from_metric_compute(self, metric_code: str) -> list[dict[str, Any]]:
        """读取指标计算层的窗口水位，作为时效判定输入。"""
        return [
            {"作业名称": r.get("作业名称", ""), "窗口水位": r.get("窗口水位", ""), "作业状态": r.get("status", "")}
            for r in store.rows("metric_compute")
            if r.get("指标编码") == metric_code
        ]

    def produce_to_anomaly_metric(self, domain: str) -> dict[str, Any]:
        """把数据域内全部过期对象打包登记为异常事件。"""
        expired = [
            r for r in store.rows(self.MODULE)
            if r.get("数据域") == domain and r.get("status") == "过期"
        ]
        if not expired:
            return {"ok": False, "message": f"数据域 {domain} 无过期对象"}
        anomaly_rows = store.rows("anomaly_metric")
        anomaly_rows.append({
            "id": max((int(r.get("id", 0)) for r in anomaly_rows), default=0) + 1,
            "异常编号": f"AN-DF-B-{domain}-{datetime.now(timezone.utc).timestamp():.0f}",
            "指标编码": domain,
            "基线区间": domain,
            "观测值": f"过期对象 {len(expired)} 个",
            "异常评分": "0.9",
            "status": "待确认",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"已打包登记 {len(expired)} 个过期对象"}

    def check_domain_health(self) -> dict[str, Any]:
        """汇总各数据域的健康度，供指标查询服务返回数据质量说明。"""
        rows = store.rows(self.MODULE)
        domains: dict[str, dict[str, int]] = {}
        for row in rows:
            domain = str(row.get("数据域", "未知"))
            bucket = domains.setdefault(domain, {"新鲜": 0, "延迟": 0, "过期": 0})
            status = str(row.get("status", "新鲜"))
            if status in bucket:
                bucket[status] += 1
        return {"ok": True, "数据域健康度": domains}

    def export_delay_report(self) -> list[dict[str, Any]]:
        """导出全部延迟与过期监控对象，供报告生成器写入数据质量附注。"""
        return [
            {
                "监控对象": r.get("监控对象", ""),
                "数据域": r.get("数据域", ""),
                "延迟分钟": r.get("延迟分钟", "0"),
                "时效等级": r.get("时效等级", ""),
            }
            for r in store.rows(self.MODULE) if r.get("status") in ("延迟", "过期")
        ]
