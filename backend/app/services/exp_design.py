"""实验设计领域服务：管理AB实验方案的分组策略、审批流转与假设指标绑定。

数据链路位置：实验规划层，承接事件接入层的已验证事件，向下驱动分组分配器
生成用户分组，并把假设指标写入指标口径中心参与计算。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ExpDesignService(BaseModuleService):
    MODULE = "exp_design"
    ENTITY = "实验方案"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["实验编码", "实验名称", "分组策略", "流量比例", "假设指标"]
    REQUIRED_FIELDS = ["实验编码", "实验名称", "分组策略"]
    STATUS_ORDER = ["设计中", "待审批", "运行中", "已结束"]
    ACTION_RULES = {"提交审批": "待审批", "启动实验": "运行中", "结束实验": "已结束"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：实验规划与分组驱动 ----

    def submit_for_review(self, entry_id: int) -> dict[str, Any]:
        """提交实验方案审批，并在结论评审队列创建对应评审记录。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "设计中":
            return {"ok": False, "message": "仅设计中方案可提交审批"}
        entry["status"] = "待审批"
        entry["pending"] = True
        review_rows = store.rows("conclusion_review")
        review_rows.append({
            "id": max((int(r.get("id", 0)) for r in review_rows), default=0) + 1,
            "记录编号": f"RV-{entry.get('实验编码', entry_id)}",
            "实验编码": entry.get("实验编码", ""),
            "评审结论": "方案待评审",
            "评审角色": "实验规划",
            "评审时间": datetime.now(timezone.utc).isoformat(),
            "status": "待评审",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"实验 {entry_id} 已提交审批并登记评审记录"}

    def launch_experiment(self, entry_id: int, user_ids: list[str]) -> dict[str, Any]:
        """启动实验并向分组分配器批量写入用户分组结果。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待审批":
            return {"ok": False, "message": "仅待审批方案可启动"}
        if not user_ids:
            return {"ok": False, "message": "缺少待分配用户列表"}
        entry["status"] = "运行中"
        entry["pending"] = False
        assign_rows = store.rows("variant_assign")
        batch_id = f"VA-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}"
        for idx, uid in enumerate(user_ids):
            assign_rows.append({
                "id": max((int(r.get("id", 0)) for r in assign_rows), default=0) + 1,
                "分配批次": batch_id,
                "用户标识": uid,
                "实验编码": entry.get("实验编码", ""),
                "分组名称": "对照组" if idx % 2 == 0 else "实验组",
                "分配时间": datetime.now(timezone.utc).isoformat(),
                "status": "已分配",
                "pending": True,
                "abnormal": False,
            })
        return {"ok": True, "message": f"实验已启动，分配批次 {batch_id} 共 {len(user_ids)} 人"}

    def finish_experiment(self, entry_id: int) -> dict[str, Any]:
        """结束实验并把结论写入实验归档库待归档。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "运行中":
            return {"ok": False, "message": "仅运行中实验可结束"}
        entry["status"] = "已结束"
        entry["pending"] = False
        archive_rows = store.rows("exp_archive")
        archive_rows.append({
            "id": max((int(r.get("id", 0)) for r in archive_rows), default=0) + 1,
            "实验编码": entry.get("实验编码", ""),
            "结束时间": datetime.now(timezone.utc).isoformat(),
            "结论摘要": f"{entry.get('实验名称', '')}已结束待归档",
            "归档位置": "default",
            "保留等级": "标准",
            "status": "待归档",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"实验 {entry_id} 已结束并登记归档"}

    def bind_metric(self, entry_id: int, metric_code: str) -> dict[str, Any]:
        """把指标口径中心的生效指标绑定为实验假设指标。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or not metric_code:
            return {"ok": False, "message": "实验不存在或指标编码为空"}
        metric = next(
            (m for m in store.rows("metric_catalog")
             if m.get("指标编码") == metric_code and m.get("status") == "已生效"),
            None,
        )
        if metric is None:
            return {"ok": False, "message": f"指标口径 {metric_code} 不存在或未生效"}
        entry["假设指标"] = metric_code
        entry["pending"] = True
        return {"ok": True, "message": f"假设指标已绑定 {metric_code}（口径版本 {metric.get('口径版本', 'v1')}）"}

    def consume_from_event_stream(self, entry_id: int) -> list[dict[str, Any]]:
        """读取事件接入层全部已验证事件，作为假设指标候选来源。"""
        if store.find(self.MODULE, entry_id) is None:
            return []
        return [
            {"事件编码": r.get("事件编码", ""), "事件名称": r.get("事件名称", "")}
            for r in store.rows("event_stream") if r.get("status") == "已验证"
        ]

    def check_running_metrics(self) -> list[dict[str, Any]]:
        """汇总运行中实验的假设指标，供指标计算作业批量建作业。"""
        running = [r for r in store.rows(self.MODULE) if r.get("status") == "运行中"]
        summary = []
        for exp in running:
            metric_code = str(exp.get("假设指标", ""))
            if metric_code:
                summary.append({
                    "实验编码": exp.get("实验编码", ""),
                    "假设指标": metric_code,
                    "流量比例": exp.get("流量比例", ""),
                })
        return summary

    def archive_snapshot(self) -> dict[str, Any]:
        """输出实验设计层数据快照供归档库与报告生成器消费。"""
        rows = store.rows(self.MODULE)
        return {
            "module": self.MODULE,
            "artifact": f"{self.ENTITY}数据集",
            "running": sum(1 for r in rows if r.get("status") == "运行中"),
            "ended": sum(1 for r in rows if r.get("status") == "已结束"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
