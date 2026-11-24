"""AA预检器领域服务：管理实验上线前的AA一致性检验、P值分布评估与重跑流程。

数据链路位置：上线预检层，消费分组分配器的对照分组数据，在实验启动前执行
AA空转检验，把未通过结论写回实验设计层阻断上线。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class AaCheckService(BaseModuleService):
    MODULE = "aa_check"
    ENTITY = "AA检验"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["检验编码", "实验编码", "检验窗口", "检验结论", "P值分布"]
    REQUIRED_FIELDS = ["检验编码", "实验编码", "检验窗口"]
    STATUS_ORDER = ["待检验", "已通过", "未通过"]
    ACTION_RULES = {"执行AA检验": "已通过", "重跑检验": "未通过"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：AA空转检验与上线阻断 ----

    def run_aa_check(self, entry_id: int) -> dict[str, Any]:
        """执行AA检验：评估P值分布均匀性并把结论写回实验设计。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待检验":
            return {"ok": False, "message": "仅待检验记录可执行"}
        exp_code = str(entry.get("实验编码", ""))
        assign_rows = [
            r for r in store.rows("variant_assign")
            if r.get("实验编码") == exp_code and r.get("status") != "已剔除"
        ]
        if len(assign_rows) < 10:
            entry["status"] = "未通过"
            entry["abnormal"] = True
            entry["检验结论"] = "样本量不足"
            return {"ok": False, "message": f"分组样本 {len(assign_rows)} 不足 10，AA检验未通过"}
        p_dist = str(entry.get("P值分布", "0.5"))
        try:
            uniform = abs(float(p_dist) - 0.5) < 0.2
        except ValueError:
            uniform = False
        entry["status"] = "已通过" if uniform else "未通过"
        entry["pending"] = not uniform
        entry["检验结论"] = "P值分布均匀" if uniform else "P值分布偏斜"
        entry["检验时间"] = datetime.now(timezone.utc).isoformat()
        exp = next((r for r in store.rows("exp_design") if r.get("实验编码") == exp_code), None)
        if exp is not None and exp.get("status") == "待审批":
            if uniform:
                exp["AA预检"] = "已通过"
            else:
                exp["AA预检"] = "未通过"
                exp["pending"] = True
        return {"ok": True, "message": f"AA检验{'通过' if uniform else '未通过'}（P分布 {p_dist}）"}

    def rerun_check(self, entry_id: int) -> dict[str, Any]:
        """重跑未通过的AA检验，重新采样检验窗口。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "未通过":
            return {"ok": False, "message": "仅未通过检验可重跑"}
        entry["status"] = "待检验"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["重跑次数"] = str(int(entry.get("重跑次数", "0") or 0) + 1)
        entry["检验窗口"] = f"重跑窗口-{entry['重跑次数']}"
        return {"ok": True, "message": f"AA检验已重置待重跑（第 {entry['重跑次数']} 次）"}

    def batch_run_pending(self) -> dict[str, Any]:
        """批量执行待检验的AA记录。"""
        pending = [r for r in store.rows(self.MODULE) if r.get("status") == "待检验"]
        passed = sum(1 for r in pending if "通过" in str(self.run_aa_check(int(r.get("id", 0))).get("message", "")) and "未通过" not in str(self.run_aa_check(int(r.get("id", 0))).get("message", "")))
        return {"ok": True, "message": f"批量AA检验完成，通过 {passed}/{len(pending)}"}

    def consume_from_variant_assign(self, exp_code: str) -> dict[str, Any]:
        """读取分组分配器的双组规模，评估AA检验可行性。"""
        rows = [r for r in store.rows("variant_assign") if r.get("实验编码") == exp_code and r.get("status") != "已剔除"]
        ctrl = sum(1 for r in rows if r.get("分组名称") == "对照组")
        treat = sum(1 for r in rows if r.get("分组名称") == "实验组")
        return {
            "ok": ctrl > 0 and treat > 0,
            "对照组规模": ctrl,
            "实验组规模": treat,
            "均衡比": round(min(ctrl, treat) / max(max(ctrl, treat), 1), 4),
        }

    def produce_to_sample_ratio(self, exp_code: str) -> dict[str, Any]:
        """AA通过后触发样本量核对，登记待核对批次。"""
        passed = any(
            r.get("实验编码") == exp_code and r.get("status") == "已通过"
            for r in store.rows(self.MODULE)
        )
        if not passed:
            return {"ok": False, "message": "AA检验未通过，禁止触发核对"}
        ratio_rows = store.rows("sample_ratio")
        ratio_rows.append({
            "id": max((int(r.get("id", 0)) for r in ratio_rows), default=0) + 1,
            "核对批次": f"SR-AA-{exp_code}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": exp_code,
            "计划比例": "0.5000",
            "实际比例": "",
            "偏移量": "",
            "status": "待核对",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "AA已通过，样本量核对批次已登记"}

    def list_blocked_experiments(self) -> list[str]:
        """列出被AA预检阻断上线的实验编码。"""
        return [
            str(r.get("实验编码", ""))
            for r in store.rows(self.MODULE) if r.get("status") == "未通过"
        ]

    def export_check_summary(self) -> list[dict[str, Any]]:
        """导出AA检验汇总，供报告生成器写入上线合规章节。"""
        return [
            {
                "检验编码": r.get("检验编码", ""),
                "实验编码": r.get("实验编码", ""),
                "检验结论": r.get("检验结论", ""),
                "P值分布": r.get("P值分布", ""),
            }
            for r in store.rows(self.MODULE)
        ]
