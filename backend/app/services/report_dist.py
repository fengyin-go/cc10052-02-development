"""报告分发领域服务：管理分析报告的分发批次、多渠道送达与失败重投。

数据链路位置：报告分发层，是实验评估数据流出平台的最后一环，消费报告生成器
的成品报告，把送达回执写回报告生成器形成闭环。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ReportDistService(BaseModuleService):
    MODULE = "report_dist"
    ENTITY = "分发记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["分发批次", "报告编码", "接收方", "分发渠道", "送达状态"]
    REQUIRED_FIELDS = ["分发批次", "报告编码", "接收方"]
    STATUS_ORDER = ["排队中", "已送达", "送达失败"]
    ACTION_RULES = {"开始分发": "已送达", "重试分发": "送达失败"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：多渠道送达与回执闭环 ----

    def dispatch(self, entry_id: int) -> dict[str, Any]:
        """执行分发送达并把回执写回报告生成器。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "排队中":
            return {"ok": False, "message": "仅排队中批次可分发"}
        report_code = str(entry.get("报告编码", ""))
        report = next((r for r in store.rows("report_gen") if r.get("报告编码") == report_code), None)
        if report is None or report.get("status") != "已完成":
            entry["status"] = "送达失败"
            entry["abnormal"] = True
            entry["失败原因"] = "报告未完成或不存在"
            return {"ok": False, "message": f"报告 {report_code} 未完成，分发失败"}
        entry["status"] = "已送达"
        entry["送达状态"] = "已送达"
        entry["pending"] = False
        entry["送达时间"] = datetime.now(timezone.utc).isoformat()
        report["分发回执"] = f"已送达:{entry.get('分发渠道', '')}"
        return {"ok": True, "message": f"报告已通过 {entry.get('分发渠道', '站内信')} 送达 {entry.get('接收方', '')}"}

    def retry_failed(self, entry_id: int) -> dict[str, Any]:
        """重投送达失败批次，支持切换分发渠道。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "送达失败":
            return {"ok": False, "message": "仅送达失败批次可重试"}
        channel = str(entry.get("分发渠道", "站内信"))
        fallback = {"站内信": "邮件", "邮件": "webhook", "webhook": "站内信"}
        entry["分发渠道"] = fallback.get(channel, "邮件")
        entry["status"] = "排队中"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["重试次数"] = str(int(entry.get("重试次数", "0") or 0) + 1)
        return {"ok": True, "message": f"渠道切换为 {entry['分发渠道']} 并重新排队"}

    def batch_dispatch(self) -> dict[str, Any]:
        """批量分发全部排队中批次。"""
        queued = [r for r in store.rows(self.MODULE) if r.get("status") == "排队中"]
        done, failed = 0, 0
        for row in queued:
            if self.dispatch(int(row.get("id", 0))).get("ok"):
                done += 1
            else:
                failed += 1
        return {"ok": True, "message": f"批量分发完成：送达 {done}，失败 {failed}"}

    def consume_from_report_gen(self, report_code: str) -> dict[str, Any]:
        """读取报告生成器的成品报告信息，校验可分发性。"""
        report = next((r for r in store.rows("report_gen") if r.get("报告编码") == report_code), None)
        if report is None:
            return {"ok": False, "message": f"报告 {report_code} 不存在"}
        return {
            "ok": report.get("status") == "已完成",
            "生成状态": report.get("status", ""),
            "数据窗口": report.get("数据窗口", ""),
            "结论条数": report.get("结论条数", "0"),
        }

    def broadcast_to_reviewers(self, report_code: str, receivers: list[str]) -> dict[str, Any]:
        """把报告广播给评审队列多个接收方，逐条登记分发记录。"""
        if not receivers or not report_code:
            return {"ok": False, "message": "缺少报告编码或接收方列表"}
        rows = store.rows(self.MODULE)
        batch_id = f"RD-BC-{datetime.now(timezone.utc).timestamp():.0f}"
        for receiver in receivers:
            rows.append({
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "分发批次": batch_id,
                "报告编码": report_code,
                "接收方": receiver,
                "分发渠道": "邮件",
                "送达状态": "排队中",
                "status": "排队中",
                "pending": True,
                "abnormal": False,
            })
        return {"ok": True, "message": f"广播批次 {batch_id} 已登记 {len(receivers)} 个接收方"}

    def list_delivered(self, report_code: str) -> list[dict[str, Any]]:
        """列出报告的全部送达记录，供结论评审队列引用。"""
        return [
            {
                "分发批次": r.get("分发批次", ""),
                "接收方": r.get("接收方", ""),
                "送达时间": r.get("送达时间", ""),
            }
            for r in store.rows(self.MODULE)
            if r.get("报告编码") == report_code and r.get("status") == "已送达"
        ]

    def sync_receipt_to_archive(self, report_code: str) -> dict[str, Any]:
        """把送达回执同步到实验归档库的结论摘要。"""
        delivered = [r for r in store.rows(self.MODULE) if r.get("报告编码") == report_code and r.get("status") == "已送达"]
        if not delivered:
            return {"ok": False, "message": "报告无送达记录"}
        archive_rows = store.rows("exp_archive")
        for row in archive_rows:
            if report_code in str(row.get("归档位置", "")):
                row["结论摘要"] = f"{row.get('结论摘要', '')}；已送达 {len(delivered)} 个接收方"
                row["pending"] = True
        return {"ok": True, "message": f"归档摘要已更新送达数 {len(delivered)}"}
