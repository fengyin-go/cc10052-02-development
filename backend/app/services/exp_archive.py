"""实验归档库领域服务：管理已结束实验的结论归档、保留等级与恢复流转。

数据链路位置：归档存储层，是实验评估数据沉淀的终点，消费实验设计与报告生成
的结论数据，按保留等级归档，支持恢复供切片分析复用历史明细。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ExpArchiveService(BaseModuleService):
    MODULE = "exp_archive"
    ENTITY = "归档实验"
    OPERATION_TERM = "归档"
    LIST_FIELDS = ["实验编码", "结束时间", "结论摘要", "归档位置", "保留等级"]
    REQUIRED_FIELDS = ["实验编码", "结束时间", "结论摘要"]
    STATUS_ORDER = ["待归档", "已归档", "恢复中"]
    ACTION_RULES = {"执行归档": "已归档", "恢复实验": "恢复中"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：结论归档与恢复复用 ----

    def archive(self, entry_id: int) -> dict[str, Any]:
        """执行归档：锁定结论摘要并标注归档位置与保留等级。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待归档":
            return {"ok": False, "message": "仅待归档实验可执行归档"}
        exp_code = str(entry.get("实验编码", ""))
        ended = any(
            r.get("实验编码") == exp_code and r.get("status") == "已结束"
            for r in store.rows("exp_design")
        )
        if not ended:
            return {"ok": False, "message": "实验尚未结束，禁止归档"}
        entry["status"] = "已归档"
        entry["pending"] = False
        entry["归档位置"] = f"archive/{exp_code}/{datetime.now(timezone.utc).strftime('%Y%m')}"
        entry["归档时间"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": f"实验 {exp_code} 已归档到 {entry['归档位置']}"}

    def restore(self, entry_id: int) -> dict[str, Any]:
        """恢复已归档实验，重建切片分析批次复用历史明细。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已归档":
            return {"ok": False, "message": "仅已归档实验可恢复"}
        entry["status"] = "恢复中"
        entry["pending"] = True
        entry["恢复时间"] = datetime.now(timezone.utc).isoformat()
        slice_rows = store.rows("segment_slice")
        slice_rows.append({
            "id": max((int(r.get("id", 0)) for r in slice_rows), default=0) + 1,
            "切片批次": f"SG-AR-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": entry.get("实验编码", ""),
            "切片维度": "归档恢复",
            "切片值": entry.get("结论摘要", "")[:20],
            "分组差异": "",
            "status": "待计算",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "实验恢复中，历史切片批次已重建"}

    def batch_archive_pending(self) -> dict[str, Any]:
        """批量归档全部待归档实验。"""
        pending = [r for r in store.rows(self.MODULE) if r.get("status") == "待归档"]
        done, skipped = 0, 0
        for row in pending:
            if self.archive(int(row.get("id", 0))).get("ok"):
                done += 1
            else:
                skipped += 1
        return {"ok": True, "message": f"批量归档完成 {done} 条，跳过 {skipped} 条"}

    def consume_from_report_gen(self, exp_code: str) -> list[dict[str, Any]]:
        """读取报告生成器中该实验的完成报告，作为归档结论来源。"""
        return [
            {"报告编码": r.get("报告编码", ""), "结论条数": r.get("结论条数", "0"), "数据窗口": r.get("数据窗口", "")}
            for r in store.rows("report_gen")
            if r.get("实验编码") == exp_code and r.get("status") == "已完成"
        ]

    def set_retention_level(self, entry_id: int, level: str) -> dict[str, Any]:
        """设置归档保留等级并联动清理策略。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or level not in ("永久", "标准", "临时"):
            return {"ok": False, "message": "归档记录不存在或保留等级非法"}
        entry["保留等级"] = level
        entry["pending"] = level != "标准"
        entry["保留更新时间"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": f"保留等级已设置为 {level}"}

    def purge_expired_temp(self) -> dict[str, Any]:
        """清理超过保留期的临时等级归档记录。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("保留等级") == "临时" and r.get("status") == "已归档"]
        purged = 0
        for row in rows:
            kept = str(row.get("归档时间", ""))
            if kept and kept < datetime.now(timezone.utc).isoformat()[:10]:
                row["status"] = "已清理"
                row["pending"] = False
                row["abnormal"] = True
                purged += 1
        return {"ok": True, "message": f"清理临时归档 {purged} 条"}

    def export_archive_index(self) -> list[dict[str, Any]]:
        """导出归档索引，供指标查询服务提供历史数据说明。"""
        return [
            {
                "实验编码": r.get("实验编码", ""),
                "结束时间": r.get("结束时间", ""),
                "归档位置": r.get("归档位置", ""),
                "保留等级": r.get("保留等级", ""),
            }
            for r in store.rows(self.MODULE) if r.get("status") in ("已归档", "恢复中")
        ]
