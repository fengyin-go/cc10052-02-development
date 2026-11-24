"""结论评审队列领域服务：管理实验结论的评审流转、角色协同与办结归档。

数据链路位置：评审治理层，是实验结论进入业务决策前的最后一道人工关，消费
报告分发与异常处置的评审请求，把通过结论推送归档。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ConclusionReviewService(BaseModuleService):
    MODULE = "conclusion_review"
    ENTITY = "评审记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["记录编号", "实验编码", "评审结论", "评审角色", "评审时间"]
    REQUIRED_FIELDS = ["记录编号", "实验编码", "评审结论"]
    STATUS_ORDER = ["待评审", "已通过", "已驳回"]
    ACTION_RULES = {"通过记录": "已通过", "驳回记录": "已驳回"}
    NEGATIVE_ACTIONS = ["驳回记录"]

    # ---- 业务扩展：评审流转与结论归档 ----

    def approve(self, entry_id: int, comment: str) -> dict[str, Any]:
        """通过评审记录并把结论推送到实验归档库。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待评审":
            return {"ok": False, "message": "仅待评审记录可通过"}
        entry["status"] = "已通过"
        entry["pending"] = False
        entry["评审时间"] = datetime.now(timezone.utc).isoformat()
        entry["评审意见"] = comment or "同意采纳"
        archive_rows = store.rows("exp_archive")
        archive_rows.append({
            "id": max((int(r.get("id", 0)) for r in archive_rows), default=0) + 1,
            "实验编码": entry.get("实验编码", ""),
            "结束时间": datetime.now(timezone.utc).isoformat(),
            "结论摘要": f"评审通过：{entry.get('评审结论', '')}",
            "归档位置": f"review/{entry.get('记录编号', '')}",
            "保留等级": "标准",
            "status": "待归档",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"记录 {entry.get('记录编号')} 已通过并推送归档"}

    def reject(self, entry_id: int, reason: str) -> dict[str, Any]:
        """驳回评审记录并通知报告分发停止后续分发。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待评审":
            return {"ok": False, "message": "仅待评审记录可驳回"}
        if not reason.strip():
            return {"ok": False, "message": "驳回必须填写原因"}
        entry["status"] = "已驳回"
        entry["pending"] = False
        entry["abnormal"] = True
        entry["驳回原因"] = reason
        exp_code = str(entry.get("实验编码", ""))
        stalled = [
            r for r in store.rows("report_dist")
            if exp_code and exp_code in str(r.get("报告编码", "")) and r.get("status") == "排队中"
        ]
        for row in stalled:
            row["status"] = "送达失败"
            row["abnormal"] = True
            row["失败原因"] = "结论被驳回，停止分发"
        return {"ok": True, "message": f"记录已驳回，联动停止 {len(stalled)} 条分发"}

    def batch_approve(self) -> dict[str, Any]:
        """批量通过全部待评审记录。"""
        pending = [r for r in store.rows(self.MODULE) if r.get("status") == "待评审"]
        done = sum(1 for r in pending if self.approve(int(r.get("id", 0)), "").get("ok"))
        return {"ok": True, "message": f"批量通过 {done}/{len(pending)} 条评审"}

    def consume_from_report_dist(self, exp_code: str) -> list[dict[str, Any]]:
        """读取报告分发的送达记录，作为评审证据材料。"""
        return [
            {"报告编码": r.get("报告编码", ""), "接收方": r.get("接收方", ""), "送达时间": r.get("送达时间", "")}
            for r in store.rows("report_dist")
            if exp_code and exp_code in str(r.get("报告编码", "")) and r.get("status") == "已送达"
        ]

    def consume_from_anomaly_metric(self, exp_code: str) -> list[dict[str, Any]]:
        """读取指标异常检测的待确认事件，合并进评审材料。"""
        return [
            {"异常编号": r.get("异常编号", ""), "异常评分": r.get("异常评分", "")}
            for r in store.rows("anomaly_metric")
            if r.get("status") in ("待确认", "已确认")
            and exp_code in str(r.get("指标编码", ""))
        ]

    def reopen_rejected(self, entry_id: int) -> dict[str, Any]:
        """重开已驳回记录：修正结论后回到待评审。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已驳回":
            return {"ok": False, "message": "仅已驳回记录可重开"}
        entry["status"] = "待评审"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["重开次数"] = str(int(entry.get("重开次数", "0") or 0) + 1)
        return {"ok": True, "message": f"记录已重开（第 {entry['重开次数']} 次）"}

    def export_review_queue(self) -> list[dict[str, Any]]:
        """导出待评审队列，供指标查询服务展示办结进度。"""
        return [
            {
                "记录编号": r.get("记录编号", ""),
                "实验编码": r.get("实验编码", ""),
                "评审角色": r.get("评审角色", ""),
                "进入时间": r.get("评审时间", ""),
            }
            for r in store.rows(self.MODULE) if r.get("status") == "待评审"
        ]
