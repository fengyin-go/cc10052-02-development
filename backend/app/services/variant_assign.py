"""分组分配器领域服务：管理实验用户的稳定分组、重新分配、剔除与批次核对。

数据链路位置：分组供给层，消费实验设计层下发的分组策略，为样本量核对与
曝光日志提供用户分组的权威数据，保证同一用户分组稳定。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class VariantAssignService(BaseModuleService):
    MODULE = "variant_assign"
    ENTITY = "分组结果"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["分配批次", "用户标识", "实验编码", "分组名称", "分配时间"]
    REQUIRED_FIELDS = ["分配批次", "用户标识", "实验编码"]
    STATUS_ORDER = ["已分配", "重新分配", "已剔除"]
    ACTION_RULES = {"重新分配": "重新分配", "剔除用户": "已剔除"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：用户分组与批次核对 ----

    def reassign_user(self, entry_id: int, new_group: str) -> dict[str, Any]:
        """重新分配用户分组并在原批次记录变更历史。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or new_group not in ("对照组", "实验组"):
            return {"ok": False, "message": "记录不存在或目标分组非法"}
        old_group = entry.get("分组名称", "")
        entry["分组名称"] = new_group
        entry["status"] = "重新分配"
        entry["pending"] = True
        entry["分配时间"] = datetime.now(timezone.utc).isoformat()
        entry["变更历史"] = f"{old_group}->{new_group}"
        return {"ok": True, "message": f"用户 {entry.get('用户标识')} 由 {old_group} 重分配到 {new_group}"}

    def exclude_user(self, entry_id: int, reason: str) -> dict[str, Any]:
        """剔除用户并同步样本量核对批次更新实际比例。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "记录不存在"}
        if not reason.strip():
            return {"ok": False, "message": "剔除必须填写原因"}
        entry["status"] = "已剔除"
        entry["pending"] = False
        entry["abnormal"] = True
        ratio_rows = store.rows("sample_ratio")
        exp_code = str(entry.get("实验编码", ""))
        matched = [r for r in ratio_rows if r.get("实验编码") == exp_code and r.get("status") == "待核对"]
        for row in matched:
            row["偏移量"] = str(float(row.get("偏移量", "0") or 0) + 0.01)
            row["pending"] = True
        return {"ok": True, "message": f"用户已剔除（{reason}），联动更新 {len(matched)} 条核对批次"}

    def batch_assign(self, exp_code: str, user_ids: list[str], strategy: str) -> dict[str, Any]:
        """按策略批量分配用户到对照组/实验组。"""
        if not user_ids or not exp_code:
            return {"ok": False, "message": "缺少实验编码或用户列表"}
        rows = store.rows(self.MODULE)
        batch_id = f"VA-{exp_code}-{datetime.now(timezone.utc).timestamp():.0f}"
        base_ratio = 0.5
        for idx, uid in enumerate(user_ids):
            group = "实验组" if (idx / max(len(user_ids), 1)) < base_ratio else "对照组"
            if strategy == "轮转分组":
                group = "实验组" if idx % 2 == 0 else "对照组"
            rows.append({
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "分配批次": batch_id,
                "用户标识": uid,
                "实验编码": exp_code,
                "分组名称": group,
                "分配时间": datetime.now(timezone.utc).isoformat(),
                "status": "已分配",
                "pending": True,
                "abnormal": False,
            })
        return {"ok": True, "message": f"批次 {batch_id} 分配完成 {len(user_ids)} 人", "batch_id": batch_id}

    def produce_to_sample_ratio(self, exp_code: str) -> dict[str, Any]:
        """汇总实验实际分组比例并写入样本量核对批次。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("实验编码") == exp_code and r.get("status") != "已剔除"]
        if not rows:
            return {"ok": False, "message": f"实验 {exp_code} 无有效分组数据"}
        exp_rows = sum(1 for r in rows if r.get("分组名称") == "实验组")
        actual_ratio = round(exp_rows / len(rows), 4)
        ratio_rows = store.rows("sample_ratio")
        ratio_rows.append({
            "id": max((int(r.get("id", 0)) for r in ratio_rows), default=0) + 1,
            "核对批次": f"SR-{exp_code}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": exp_code,
            "计划比例": "0.5000",
            "实际比例": str(actual_ratio),
            "偏移量": str(round(abs(actual_ratio - 0.5), 4)),
            "status": "待核对",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"已写入核对批次，实际实验组比例 {actual_ratio}"}

    def consume_from_exp_design(self, exp_code: str) -> dict[str, Any]:
        """读取实验设计层方案，返回分组策略与流量比例。"""
        exp = next(
            (r for r in store.rows("exp_design") if r.get("实验编码") == exp_code),
            None,
        )
        if exp is None:
            return {"ok": False, "message": f"实验方案 {exp_code} 不存在"}
        return {
            "ok": True,
            "实验编码": exp_code,
            "分组策略": exp.get("分组策略", ""),
            "流量比例": exp.get("流量比例", ""),
            "状态": exp.get("status", ""),
        }

    def find_user_group(self, exp_code: str, user_id: str) -> dict[str, Any]:
        """定位用户在指定实验的当前分组，供曝光日志打标使用。"""
        matched = [
            r for r in store.rows(self.MODULE)
            if r.get("实验编码") == exp_code
            and r.get("用户标识") == user_id
            and r.get("status") != "已剔除"
        ]
        if not matched:
            return {"ok": False, "message": f"用户 {user_id} 在实验 {exp_code} 无分组"}
        latest = matched[-1]
        return {"ok": True, "分组名称": latest.get("分组名称", ""), "分配批次": latest.get("分配批次", "")}

    def batch_stats(self, exp_code: str) -> dict[str, Any]:
        """统计实验分组分布，输出给报告生成器引用。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("实验编码") == exp_code]
        return {
            "实验编码": exp_code,
            "总分配数": len(rows),
            "已分配": sum(1 for r in rows if r.get("status") == "已分配"),
            "重新分配": sum(1 for r in rows if r.get("status") == "重新分配"),
            "已剔除": sum(1 for r in rows if r.get("status") == "已剔除"),
        }
