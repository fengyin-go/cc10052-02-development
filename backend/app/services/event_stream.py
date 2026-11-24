"""事件接入领域服务：管理业务埋点事件的定义、验证、下线与上报量接入。

数据链路位置：数据接入层，是实验评估数据流入平台的第一入口，负责把已验证事件
的上报数据写入曝光日志与指标口径，形成后续计算可用的原始事件数据。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class EventStreamService(BaseModuleService):
    MODULE = "event_stream"
    ENTITY = "事件定义"
    OPERATION_TERM = "接入"
    LIST_FIELDS = ["事件编码", "事件名称", "属性字段", "来源应用", "上报协议"]
    REQUIRED_FIELDS = ["事件编码", "事件名称", "属性字段"]
    STATUS_ORDER = ["待验证", "已验证", "已下线"]
    ACTION_RULES = {"验证事件": "已验证", "下线事件": "已下线"}
    NEGATIVE_ACTIONS = ["下线事件"]

    # ---- 业务扩展：事件接入与日志投递 ----

    def verify_event(self, entry_id: int) -> dict[str, Any]:
        """验证事件定义的属性字段完整性，通过后写入曝光日志接入队列。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        props = str(entry.get("属性字段", ""))
        if not props.strip():
            return {"ok": False, "message": "属性字段为空，无法验证"}
        exposure_rows = store.rows("exposure_log")
        batch_id = f"evt-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}"
        exposure_rows.append({
            "id": max((int(r.get("id", 0)) for r in exposure_rows), default=0) + 1,
            "日志批次": batch_id,
            "实验编码": entry.get("来源应用", ""),
            "用户标识": f"evt-user-{entry_id}",
            "分组名称": "事件接入组",
            "曝光时间": datetime.now(timezone.utc).isoformat(),
            "status": "已接收",
            "pending": True,
            "abnormal": False,
        })
        entry["status"] = "已验证"
        entry["pending"] = False
        return {"ok": True, "message": f"事件 {entry_id} 已验证并投递日志批次 {batch_id}"}

    def offline_event(self, entry_id: int) -> dict[str, Any]:
        """下线事件并通知指标口径中心停止引用该事件。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "事件不存在"}
        event_code = entry.get("事件编码", "")
        entry["status"] = "已下线"
        entry["pending"] = False
        entry["abnormal"] = True
        affected = [
            m for m in store.rows("metric_catalog")
            if event_code and event_code in str(m.get("计算口径", ""))
        ]
        for metric in affected:
            metric["status"] = "已废弃"
            metric["pending"] = False
        return {"ok": True, "message": f"事件已下线，联动废弃 {len(affected)} 条指标口径"}

    def ingest_today_volume(self, entry_id: int, volume: int) -> dict[str, Any]:
        """记录事件今日上报量并推送到数据时效监控。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or volume < 0:
            return {"ok": False, "message": "事件不存在或上报量为负"}
        entry["今日上报量"] = str(volume)
        freshness_rows = store.rows("data_freshness")
        freshness_rows.append({
            "id": max((int(r.get("id", 0)) for r in freshness_rows), default=0) + 1,
            "监控对象": f"event_stream:{entry.get('事件编码', entry_id)}",
            "数据域": "事件接入",
            "最新水位": datetime.now(timezone.utc).isoformat(),
            "延迟分钟": "0",
            "时效等级": "新鲜",
            "status": "新鲜",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"今日上报量 {volume} 已记录并刷新时效监控"}

    def produce_to_metric_catalog(self, entry_id: int) -> dict[str, Any]:
        """把已验证事件注册为指标口径中心的候选指标定义。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已验证":
            return {"ok": False, "message": "仅已验证事件可注册指标口径"}
        catalog_rows = store.rows("metric_catalog")
        metric_code = f"M-{entry.get('事件编码', entry_id)}"
        if any(m.get("指标编码") == metric_code for m in catalog_rows):
            return {"ok": False, "message": f"指标口径 {metric_code} 已存在"}
        catalog_rows.append({
            "id": max((int(m.get("id", 0)) for m in catalog_rows), default=0) + 1,
            "指标编码": metric_code,
            "指标名称": f"{entry.get('事件名称', '事件')}转化率",
            "计算口径": f"count({entry.get('事件编码', '')})/曝光量",
            "聚合窗口": "日窗口",
            "口径版本": "v1",
            "status": "草稿",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"指标口径 {metric_code} 已创建为草稿"}

    def consume_from_metric_catalog(self, entry_id: int) -> list[dict[str, Any]]:
        """读取引用当前事件的全部生效指标口径，返回依赖清单。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return []
        event_code = str(entry.get("事件编码", ""))
        return [
            {
                "指标编码": m.get("指标编码", ""),
                "计算口径": m.get("计算口径", ""),
                "口径版本": m.get("口径版本", ""),
            }
            for m in store.rows("metric_catalog")
            if event_code and event_code in str(m.get("计算口径", ""))
            and m.get("status") == "已生效"
        ]

    def batch_verify(self, only_pending: bool = True) -> dict[str, Any]:
        """批量验证待验证事件并逐条投递曝光日志。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("status") == "待验证"] if only_pending else list(store.rows(self.MODULE))
        done = 0
        for row in rows:
            if str(row.get("属性字段", "")).strip():
                result = self.verify_event(int(row.get("id", 0)))
                if result.get("ok"):
                    done += 1
        return {"ok": True, "message": f"批量验证完成 {done} 条", "total": len(rows), "verified": done}

    def export_verified_events(self) -> list[dict[str, Any]]:
        """导出全部已验证事件清单供指标查询服务上线接口使用。"""
        return [
            {
                "事件编码": r.get("事件编码", ""),
                "事件名称": r.get("事件名称", ""),
                "上报协议": r.get("上报协议", ""),
                "今日上报量": r.get("今日上报量", "0"),
            }
            for r in store.rows(self.MODULE) if r.get("status") == "已验证"
        ]
