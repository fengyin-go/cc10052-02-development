"""曝光日志接入领域服务：管理实验曝光日志的批量接入、去重清洗与异常丢弃。

数据链路位置：日志接入层，是实验侧原始数据的第一入口，消费分组分配器的
分组标签，把去重后的曝光数据供给指标计算作为分母。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ExposureLogService(BaseModuleService):
    MODULE = "exposure_log"
    ENTITY = "曝光记录"
    OPERATION_TERM = "接入"
    LIST_FIELDS = ["日志批次", "实验编码", "用户标识", "分组名称", "曝光时间"]
    REQUIRED_FIELDS = ["日志批次", "实验编码", "用户标识"]
    STATUS_ORDER = ["已接收", "已去重", "异常丢弃"]
    ACTION_RULES = {"执行去重": "已去重", "丢弃异常": "异常丢弃"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：日志去重与异常清洗 ----

    def dedup_batch(self, batch_id: str) -> dict[str, Any]:
        """按批次执行曝光去重：同一用户同批次只保留首条曝光。"""
        if not batch_id:
            return {"ok": False, "message": "日志批次为空"}
        rows = [r for r in store.rows(self.MODULE) if r.get("日志批次") == batch_id and r.get("status") == "已接收"]
        if not rows:
            return {"ok": False, "message": f"批次 {batch_id} 无已接收记录"}
        seen_users = set()
        deduped, dropped = 0, 0
        for row in rows:
            uid = str(row.get("用户标识", ""))
            if uid in seen_users:
                row["status"] = "异常丢弃"
                row["pending"] = False
                row["abnormal"] = True
                row["丢弃原因"] = "批次内重复曝光"
                dropped += 1
            else:
                seen_users.add(uid)
                row["status"] = "已去重"
                row["pending"] = False
                deduped += 1
        return {"ok": True, "message": f"批次 {batch_id} 去重完成：保留 {deduped}，丢弃 {dropped}"}

    def drop_abnormal(self, entry_id: int, reason: str) -> dict[str, Any]:
        """丢弃异常曝光记录并把质量事件同步给时效监控。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已接收":
            return {"ok": False, "message": "仅已接收记录可丢弃"}
        if not reason.strip():
            return {"ok": False, "message": "丢弃必须填写原因"}
        entry["status"] = "异常丢弃"
        entry["pending"] = False
        entry["abnormal"] = True
        entry["丢弃原因"] = reason
        freshness_rows = store.rows("data_freshness")
        freshness_rows.append({
            "id": max((int(r.get("id", 0)) for r in freshness_rows), default=0) + 1,
            "监控对象": f"exposure_log:{entry_id}",
            "数据域": "日志接入",
            "最新水位": datetime.now(timezone.utc).isoformat(),
            "延迟分钟": "0",
            "时效等级": "新鲜",
            "status": "新鲜",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"记录已丢弃（{reason}），质量事件已同步"}

    def batch_dedup_all(self) -> dict[str, Any]:
        """批量去重全部已接收批次。"""
        batches = {str(r.get("日志批次", "")) for r in store.rows(self.MODULE) if r.get("status") == "已接收"}
        done = sum(1 for b in batches if self.dedup_batch(b).get("ok"))
        return {"ok": True, "message": f"批量去重完成 {done}/{len(batches)} 个批次"}

    def consume_from_variant_assign(self, exp_code: str, user_id: str) -> dict[str, Any]:
        """读取分组分配器的用户分组，为曝光记录补充分组标签。"""
        matched = [
            r for r in store.rows("variant_assign")
            if r.get("实验编码") == exp_code
            and r.get("用户标识") == user_id
            and r.get("status") != "已剔除"
        ]
        if not matched:
            return {"ok": False, "message": f"用户 {user_id} 在实验 {exp_code} 无分组"}
        return {"ok": True, "分组名称": matched[-1].get("分组名称", ""), "分配批次": matched[-1].get("分配批次", "")}

    def produce_to_metric_compute(self, exp_code: str) -> dict[str, Any]:
        """把去重曝光数推送给指标计算层，登记计算输入摘要。"""
        rows = [
            r for r in store.rows(self.MODULE)
            if r.get("实验编码") == exp_code and r.get("status") == "已去重"
        ]
        if not rows:
            return {"ok": False, "message": f"实验 {exp_code} 无去重曝光"}
        summary = {
            "实验编码": exp_code,
            "去重曝光数": str(len(rows)),
            "最新曝光时间": max(str(r.get("曝光时间", "")) for r in rows),
        }
        return {"ok": True, "message": f"去重曝光 {len(rows)} 条已就绪可供计算", "summary": summary}

    def ingest_batch(self, exp_code: str, user_ids: list[str]) -> dict[str, Any]:
        """接入新曝光批次并为每个用户打上分组标签。"""
        if not user_ids or not exp_code:
            return {"ok": False, "message": "缺少实验编码或用户列表"}
        rows = store.rows(self.MODULE)
        batch_id = f"EX-{exp_code}-{datetime.now(timezone.utc).timestamp():.0f}"
        for uid in user_ids:
            group_info = self.consume_from_variant_assign(exp_code, uid)
            rows.append({
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "日志批次": batch_id,
                "实验编码": exp_code,
                "用户标识": uid,
                "分组名称": group_info.get("分组名称", "未分组") if group_info.get("ok") else "未分组",
                "曝光时间": datetime.now(timezone.utc).isoformat(),
                "status": "已接收",
                "pending": True,
                "abnormal": False,
            })
        return {"ok": True, "message": f"批次 {batch_id} 接入 {len(user_ids)} 条曝光", "batch_id": batch_id}

    def export_dedup_stats(self, exp_code: str) -> dict[str, Any]:
        """导出实验去重统计，供报告生成器写入数据规模说明。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("实验编码") == exp_code]
        return {
            "实验编码": exp_code,
            "总曝光": len(rows),
            "已去重": sum(1 for r in rows if r.get("status") == "已去重"),
            "异常丢弃": sum(1 for r in rows if r.get("status") == "异常丢弃"),
        }
