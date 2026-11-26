"""指标计算作业领域服务：管理实验指标的计算窗口、窗口水位、分母回执引用与修订。

数据链路位置：指标计算层，消费指标口径中心的生效口径与曝光日志的有效曝光，
产出指标结果写入显著性检验，是实验评估的核心计算环节。
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services.windowing import day_in_window, parse_window_days, windows_for_day
from app.store import store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(r.get("id", 0) or 0) for r in rows), default=0) + 1


class MetricComputeService(BaseModuleService):
    MODULE = "metric_compute"
    ENTITY = "计算作业"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["作业名称", "指标编码", "实验编码", "计算窗口", "窗口水位", "作业状态"]
    REQUIRED_FIELDS = ["作业名称", "指标编码", "计算窗口"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "待重算", "计算失败"]
    ACTION_RULES = {"启动作业": "执行中", "重算窗口": "已完成", "重试计算": "计算失败"}
    NEGATIVE_ACTIONS = []

    # ---- 登记时保留窗口/实验等扩展字段 ----

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry, missing = super().create_entry(values)
        if entry is not None:
            for field in (
                "实验编码",
                "experiment_code",
                "窗口开始",
                "窗口结束",
                "window_start",
                "window_end",
                "指标版本",
                "分母口径",
            ):
                if values.get(field) not in (None, ""):
                    entry[field] = values[field]
        return entry, missing

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action in {"重算窗口", "重算", "重新计算", "recalculate", "recalc"}:
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            result = self.recalculate_job(entry_id, trigger={"trigger_type": "row_action_recalculation"})
            return store.find(self.MODULE, entry_id), str(result.get("message"))
        return super().run_action(entry_id, action)

    # ---- 基础作业动作（保留脚手架原有行为） ----

    def start_job(self, entry_id: int) -> dict[str, Any]:
        """启动计算作业，推进窗口水位并在完成后写入显著性检验批次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待执行":
            return {"ok": False, "message": "仅待执行作业可启动"}
        metric = next(
            (m for m in store.rows("metric_catalog") if m.get("指标编码") == entry.get("指标编码")),
            None,
        )
        if metric is None or metric.get("status") != "已生效":
            return {"ok": False, "message": f"口径 {entry.get('指标编码')} 未生效，禁止计算"}
        entry["status"] = "已完成"
        entry["pending"] = False
        entry["作业状态"] = "已完成"
        entry["窗口水位"] = _now()
        test_rows = store.rows("significance_test")
        test_rows.append({
            "id": _next_id(test_rows),
            "检验批次": f"ST-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": entry.get("实验编码") or f"EXP_-000{entry_id}",
            "指标编码": entry.get("指标编码", ""),
            "P值": "0.031",
            "置信区间": "[-0.021, 0.045]",
            "status": "待检验",
            "pending": True,
            "abnormal": False,
        })
        # 新链路同时落一份可核对的分母快照。
        self.recalculate_job(entry_id, trigger={"trigger_type": "start_job"})
        return {"ok": True, "message": f"作业 {entry_id} 计算完成并登记检验批次"}

    def retry_failed_job(self, entry_id: int) -> dict[str, Any]:
        """重试计算失败作业，重算窗口并登记异常事件。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "计算失败":
            return {"ok": False, "message": "仅计算失败作业可重试"}
        entry["status"] = "执行中"
        entry["pending"] = True
        entry["作业状态"] = "执行中"
        entry["重试次数"] = str(int(entry.get("重试次数", "0") or 0) + 1)
        anomaly_rows = store.rows("anomaly_metric")
        anomaly_rows.append({
            "id": _next_id(anomaly_rows),
            "异常编号": f"AN-{entry_id}-retry",
            "指标编码": entry.get("指标编码", ""),
            "基线区间": entry.get("计算窗口", ""),
            "观测值": "作业重试",
            "异常评分": "0.5",
            "status": "待确认",
            "pending": True,
            "abnormal": False,
        })
        result = self.recalculate_job(entry_id, trigger={"trigger_type": "retry"})
        if result.get("ok"):
            entry["status"] = "已完成"
            entry["pending"] = False
            entry["作业状态"] = "已完成"
        return {"ok": True, "message": f"作业 {entry_id} 已进入重试（第 {entry['重试次数']} 次）"}

    def advance_watermark(self, entry_id: int, new_watermark: str) -> dict[str, Any]:
        """推进窗口水位并同步数据时效监控的最新水位。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or not new_watermark.strip():
            return {"ok": False, "message": "作业不存在或水位为空"}
        old = str(entry.get("窗口水位", ""))
        if new_watermark <= old:
            return {"ok": False, "message": "新水位必须晚于当前水位"}
        entry["窗口水位"] = new_watermark
        freshness_rows = store.rows("data_freshness")
        matched = [f for f in freshness_rows if f.get("监控对象") == f"metric_compute:{entry_id}"]
        if matched:
            matched[-1]["最新水位"] = new_watermark
            matched[-1]["status"] = "新鲜"
            matched[-1]["时效等级"] = "新鲜"
        else:
            freshness_rows.append({
                "id": _next_id(freshness_rows),
                "监控对象": f"metric_compute:{entry_id}",
                "数据域": "指标计算",
                "最新水位": new_watermark,
                "延迟分钟": "0",
                "时效等级": "新鲜",
                "status": "新鲜",
                "pending": True,
                "abnormal": False,
            })
        return {"ok": True, "message": f"水位已推进到 {new_watermark}"}

    def batch_execute_pending(self) -> dict[str, Any]:
        """批量执行全部待执行作业并汇总结果。"""
        pending_rows = [r for r in store.rows(self.MODULE) if r.get("status") == "待执行"]
        done, failed = 0, 0
        for row in pending_rows:
            result = self.start_job(int(row.get("id", 0)))
            if result.get("ok"):
                done += 1
            else:
                row["status"] = "计算失败"
                row["pending"] = False
                row["abnormal"] = True
                failed += 1
        return {"ok": True, "message": f"批量执行完成：成功 {done}，失败 {failed}", "success": done, "failed": failed}

    def consume_from_window_scheduler(self, metric_code: str) -> list[dict[str, Any]]:
        """读取窗口调度器中该指标的活跃计划，返回调度约束。"""
        return [
            {
                "计划名称": s.get("计划名称", ""),
                "窗口粒度": s.get("窗口粒度", ""),
                "下次窗口": s.get("下次窗口", ""),
            }
            for s in store.rows("window_scheduler")
            if s.get("指标编码") == metric_code and s.get("status") in ("待执行", "执行中")
        ]

    # ---- 有效曝光分母与导入回执引用 ----

    def _exposure_day(self, row: dict[str, Any]) -> str | None:
        day = row.get("effective_date") or row.get("window_day")
        if day:
            return str(day)
        from app.services.windowing import parse_exposure_time
        return parse_exposure_time(row.get("曝光时间"))[1]

    def _is_countable_exposure(self, row: dict[str, Any], window: Any) -> bool:
        if row.get("valid", True) is False:
            return False
        status = str(row.get("status", ""))
        if status in {"异常丢弃", "已更正", "已失效", "rejected", "superseded"}:
            return False
        # 在线批次经“执行去重”后进入分母；CSV 有效行接入时直接置为已去重。
        if status != "已去重":
            return False
        day = self._exposure_day(row)
        return day_in_window(day, window)

    def _row_ref(self, row: dict[str, Any]) -> dict[str, Any]:
        source_id = row.get("来源编号") or row.get("source_id") or f"ONLINE-{row.get('id')}"
        exp = row.get("实验编码") or row.get("experiment_code") or ""
        day = self._exposure_day(row)
        ref = {
            "exposure_id": row.get("id"),
            "id": row.get("id"),
            "source_id": source_id,
            "来源编号": source_id,
            "experiment_code": exp,
            "实验编码": exp,
            "user_id": row.get("用户标识") or row.get("user_id") or "",
            "用户标识": row.get("用户标识") or "",
            "group_name": row.get("分组名称") or row.get("group_name") or "",
            "分组名称": row.get("分组名称") or "",
            "effective_at": row.get("曝光时间") or row.get("occurred_at") or "",
            "曝光时间": row.get("曝光时间") or "",
            "window_day": day,
            "effective_date": day,
            "import_no": row.get("import_no") or row.get("source_import_no"),
            "receipt_no": row.get("receipt_no") or row.get("import_no"),
            "receipt_row_id": row.get("receipt_row_id"),
            "file_row": row.get("file_row"),
            "original_file_row": row.get("original_file_row") or row.get("file_row"),
            "content_hash": row.get("content_hash"),
            "source_type": row.get("source_type") or ("import" if row.get("import_no") else "online"),
        }
        return ref

    def _receipt_refs(self, row_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for ref in row_refs:
            import_no = ref.get("import_no")
            if not import_no:
                continue
            item = grouped.setdefault(import_no, {
                "import_no": import_no,
                "receipt_no": import_no,
                "count": 0,
                "valid_row_ids": [],
                "valid_file_rows": [],
                "valid_sources": [],
                "valid_rows": [],
            })
            item["count"] += 1
            if ref.get("receipt_row_id") is not None:
                item["valid_row_ids"].append(ref["receipt_row_id"])
            if ref.get("file_row") is not None:
                item["valid_file_rows"].append(ref["file_row"])
            if ref.get("source_id") is not None:
                item["valid_sources"].append(ref["source_id"])
            item["valid_rows"].append({
                "receipt_row_id": ref.get("receipt_row_id"),
                "file_row": ref.get("file_row"),
                "source_id": ref.get("source_id"),
                "experiment_code": ref.get("experiment_code"),
                "exposure_id": ref.get("exposure_id"),
                "content_hash": ref.get("content_hash"),
            })

        receipts = store.rows("exposure_import_receipts")
        for item in grouped.values():
            receipt = next((r for r in receipts if str(r.get("import_no")) == str(item["import_no"])), None)
            if receipt:
                item["filename"] = receipt.get("filename", "")
                item["import_id"] = receipt.get("id")
            item["valid_row_count"] = item["count"]
        return sorted(grouped.values(), key=lambda x: str(x["import_no"]))

    def _correction_refs_for_window(
        self,
        exp_code: str,
        start: str | None,
        end: str | None,
    ) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for receipt in store.rows("exposure_import_receipts"):
            for row in receipt.get("rows", []):
                if row.get("status") != "corrected":
                    continue
                if exp_code and str(row.get("experiment_code") or row.get("实验编码") or "") != exp_code:
                    continue
                old_day = row.get("old_window_key")
                new_day = row.get("window_key")
                in_old = bool(old_day and start and end and start <= old_day <= end)
                in_new = bool(new_day and start and end and start <= new_day <= end)
                if not (in_old or in_new):
                    continue
                effect = "updated"
                if in_old and not in_new:
                    effect = "moved_out"
                elif in_new and not in_old:
                    effect = "moved_in"
                refs.append({
                    "import_no": receipt.get("import_no"),
                    "receipt_no": receipt.get("import_no"),
                    "receipt_row_id": row.get("row_id"),
                    "file_row": row.get("file_row"),
                    "source_id": row.get("source_id"),
                    "experiment_code": row.get("experiment_code"),
                    "effect": effect,
                    "old_window": old_day,
                    "new_window": new_day,
                    "old_exposure_id": row.get("old_exposure_id"),
                    "new_exposure_id": row.get("new_exposure_id"),
                })
        return refs

    def build_denominator(
        self,
        job: dict[str, Any],
        experiment_code: str | None = None,
    ) -> dict[str, Any]:
        """读取作业窗口内当前有效的曝光，并冻结回执/有效行引用。"""
        exp_code = str(experiment_code or job.get("实验编码") or job.get("experiment_code") or "")
        window = job.get("计算窗口", "")
        start, end = parse_window_days(window)

        rows = [
            r for r in store.rows("exposure_log")
            if self._is_countable_exposure(r, window)
            and (not exp_code or str(r.get("实验编码") or r.get("experiment_code") or "") == exp_code)
        ]
        rows.sort(key=lambda r: int(r.get("id", 0) or 0))
        row_refs = [self._row_ref(r) for r in rows]
        group_distribution: dict[str, int] = {}
        for ref in row_refs:
            group = str(ref.get("group_name") or "未分组")
            group_distribution[group] = group_distribution.get(group, 0) + 1
        # 保持历史返回中的两个固定分组键。
        group_distribution.setdefault("对照组", 0)
        group_distribution.setdefault("实验组", 0)

        checksum = hashlib.sha256(
            json.dumps(
                [[r.get("exposure_id"), r.get("content_hash"), r.get("window_day")] for r in row_refs],
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        receipt_refs = self._receipt_refs(row_refs)
        correction_refs = self._correction_refs_for_window(exp_code, start, end)

        return {
            "job_id": job.get("id"),
            "experiment_code": exp_code or "*",
            "实验编码": exp_code or "*",
            "window": window,
            "计算窗口": window,
            "window_start": start,
            "window_end": end,
            "valid_exposure_count": len(row_refs),
            "有效曝光数": len(row_refs),
            "去重曝光数": len(row_refs),
            "denominator": len(row_refs),
            "分组分布": group_distribution,
            "group_distribution": group_distribution,
            "row_refs": row_refs,
            "valid_rows": row_refs,
            "有效曝光行": row_refs,
            "receipt_refs": receipt_refs,
            "import_receipts": receipt_refs,
            "receipts": receipt_refs,
            "导入回执": receipt_refs,
            "导入回执引用": receipt_refs,
            "correction_refs": correction_refs,
            "更正引用": correction_refs,
            "checksum": checksum,
            "source_type_counts": {
                "online": sum(1 for r in row_refs if r.get("source_type") == "online"),
                "csv": sum(1 for r in row_refs if r.get("source_type") != "online"),
            },
        }

    def list_valid_exposures(
        self,
        experiment_code: str | None = None,
        window: str | dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """列出当前有效曝光；供指标计算或核对脚本直接调用。"""
        pseudo_job = {"实验编码": experiment_code or "", "计算窗口": window or ""}
        return self.build_denominator(pseudo_job, experiment_code).get("row_refs", [])

    def calculate_window(self, entry_id: int, **kwargs: Any) -> dict[str, Any]:
        return self.recalculate_job(entry_id, **kwargs)

    def recalculate_window(self, entry_id: int, **kwargs: Any) -> dict[str, Any]:
        return self.recalculate_job(entry_id, **kwargs)

    def consume_from_exposure_log(
        self,
        exp_code: str,
        window: str | None = None,
    ) -> dict[str, Any]:
        """统计有效曝光数，作为计算作业的分母数据；同时给出来源回执引用。"""
        pseudo_job = {"id": None, "实验编码": exp_code, "计算窗口": window or ""}
        denom = self.build_denominator(pseudo_job, exp_code)
        legacy_group_distribution = {
            "对照组": sum(1 for r in denom["row_refs"] if r.get("group_name") == "对照组"),
            "实验组": sum(1 for r in denom["row_refs"] if r.get("group_name") == "实验组"),
        }
        return {
            "实验编码": exp_code,
            "去重曝光数": denom["valid_exposure_count"],
            "分组分布": legacy_group_distribution,
            "全部分组分布": denom["分组分布"],
            "receipt_refs": denom["receipt_refs"],
            "row_refs": denom["row_refs"],
        }

    # ---- 分母领取/提交冲突控制 ----

    def claim_denominator(
        self,
        job_id: int,
        experiment_code: str | None = None,
        claimed_by: str = "metric_compute",
    ) -> dict[str, Any]:
        job = store.find(self.MODULE, job_id)
        if job is None:
            return {"ok": False, "status_code": 404, "message": f"计算作业 {job_id} 不存在"}
        exp_code = experiment_code or job.get("实验编码") or "*"
        denom = self.build_denominator(job, experiment_code or job.get("实验编码"))
        rows = store.rows("metric_denominators")
        token = f"DCT-{secrets.token_hex(8)}"
        record = {
            "id": _next_id(rows),
            "denominator_no": f"DEN-{job_id}-{_next_id(rows):04d}",
            "claim_token": token,
            "job_id": job_id,
            "experiment_code": exp_code,
            "window": denom["window"],
            "window_start": denom["window_start"],
            "window_end": denom["window_end"],
            "checksum": denom["checksum"],
            "valid_exposure_count": denom["valid_exposure_count"],
            "receipt_refs": denom["receipt_refs"],
            "row_refs": denom["row_refs"],
            "status": "claimed",
            "claimed_by": claimed_by,
            "claimed_at": _now(),
            "committed_at": None,
            "conflicted": False,
            "stale": False,
        }
        rows.append(record)
        job["denominator_claim_token"] = token
        job["denominator_status"] = "已领取"
        return {"ok": True, "claim_token": token, "denominator": record, "message": "分母版本已领取"}

    def commit_claimed_denominator(
        self,
        job_id: int,
        claim_token: str,
        trigger: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        claimed = next(
            (d for d in store.rows("metric_denominators") if d.get("claim_token") == claim_token),
            None,
        )
        if claimed is None:
            return {"ok": False, "status_code": 404, "message": "分母领取凭证不存在"}
        if claimed.get("conflicted"):
            return {"ok": False, "status_code": 409, "conflict": True,
                    "message": "领取后的分母已被曝光更正修改，旧出账已拒绝，请重算窗口"}
        job = store.find(self.MODULE, job_id)
        if job is None:
            return {"ok": False, "status_code": 404, "message": f"计算作业 {job_id} 不存在"}
        current = self.build_denominator(job, claimed.get("experiment_code") if claimed.get("experiment_code") != "*" else None)
        if current["checksum"] != claimed.get("checksum"):
            claimed["conflicted"] = True
            claimed["status"] = "conflicted"
            return {"ok": False, "status_code": 409, "conflict": True,
                    "message": "分母在领取后发生变化，提交与旧出账冲突，请按最新回执重算"}
        claimed["status"] = "committed"
        claimed["committed_at"] = _now()
        return self.recalculate_job(
            job_id,
            experiment_code=claimed.get("experiment_code") if claimed.get("experiment_code") != "*" else None,
            trigger=trigger or {"trigger_type": "commit_claim", "claim_token": claim_token},
        )

    # ---- 更正到达：标记受影响窗口和已生成结果 ----

    def _matching_jobs(self, experiment_code: str, days: set[str]) -> list[dict[str, Any]]:
        jobs = store.rows(self.MODULE)
        day_jobs: list[dict[str, Any]] = []
        for day in days:
            day_jobs.extend(windows_for_day(day, jobs))
        unique = {int(j["id"]): j for j in day_jobs if j.get("id") is not None}
        result = []
        for job in unique.values():
            job_exp = str(job.get("实验编码") or job.get("experiment_code") or "")
            if job_exp and experiment_code and job_exp != experiment_code:
                continue
            result.append(job)
        return sorted(result, key=lambda j: int(j["id"]))

    def register_exposure_change(self, change: dict[str, Any]) -> list[dict[str, Any]]:
        """登记一次新增/更正对计算窗口的影响，并把已有结果标记为待重算。"""
        exp_code = str(change.get("experiment_code") or change.get("实验编码") or "")
        old_day = change.get("old_window_key")
        new_day = change.get("window_key") or change.get("new_window_key")
        days = {d for d in (old_day, new_day) if d}
        impacts: list[dict[str, Any]] = []
        receipt_no = change.get("import_no") or change.get("receipt_no")
        receipt_row_id = change.get("receipt_row_id")

        for job in self._matching_jobs(exp_code, days):
            job_id = int(job["id"])
            affected_days = [d for d in (old_day, new_day) if d and parse_window_days(job.get("计算窗口"))[0]
                             and parse_window_days(job.get("计算窗口"))[0] <= d <= parse_window_days(job.get("计算窗口"))[1]]
            for day in dict.fromkeys(affected_days):
                if old_day == new_day:
                    effect = "update"
                elif day == old_day:
                    effect = "remove"
                else:
                    effect = "add"
                impacts.append({
                    "job_id": job_id,
                    "job_name": job.get("作业名称", ""),
                    "window": job.get("计算窗口", ""),
                    "window_day": day,
                    "effect": effect,
                    "receipt_no": receipt_no,
                    "receipt_row_id": receipt_row_id,
                    "source_id": change.get("source_id"),
                    "state": "pending_recalculation",
                    "state_label": "待重算",
                    "href": f"/metric_compute?jobs={job_id}&receipt={receipt_no or ''}&source={change.get('source_id') or ''}",
                })

            has_result = bool(job.get("current_revision_no")) or job.get("status") == "已完成"
            if has_result:
                job["needs_recalculation"] = True
                job["recalc_status"] = "待重算"
                job["结果修订状态"] = "旧结果可追踪，等待重算"
                pending = job.setdefault("pending_corrections", [])
                token_ref = f"{receipt_no}:{receipt_row_id}"
                if token_ref not in pending:
                    pending.append(token_ref)
                for denom_no in job.get("denominator_nos", []) or []:
                    denom = next((d for d in store.rows("metric_denominators") if d.get("denominator_no") == denom_no), None)
                    if denom and denom.get("status") == "committed":
                        denom["stale"] = True
                conflict_rows = store.rows("metric_conflicts")
                conflict_key = f"{job_id}:{token_ref}"
                if not any(c.get("conflict_key") == conflict_key and c.get("status") != "resolved" for c in conflict_rows):
                    conflict_rows.append({
                        "id": _next_id(conflict_rows),
                        "conflict_key": conflict_key,
                        "conflict_no": f"MC-{job_id}-{_next_id(conflict_rows):04d}",
                        "job_id": job_id,
                        "experiment_code": exp_code,
                        "window": job.get("计算窗口", ""),
                        "source_id": change.get("source_id"),
                        "original_import_no": change.get("original_import_no"),
                        "correction_import_no": receipt_no,
                        "correction_receipt_row_id": receipt_row_id,
                        "old_window": old_day,
                        "new_window": new_day,
                        "status": "待重算",
                        "created_at": _now(),
                    })
            else:
                job["denominator_status"] = "更正将在首次计算时生效"

            # 若旧分母正被领取，显式标记领取版本冲突，阻止旧快照出账。
            for denom in store.rows("metric_denominators"):
                if denom.get("job_id") != job_id or denom.get("status") != "claimed":
                    continue
                if exp_code and denom.get("experiment_code") not in (exp_code, "*"):
                    continue
                denom["conflicted"] = True
                denom["status"] = "conflicted"

        return impacts

    def describe_impact(self, impact: dict[str, Any]) -> dict[str, Any]:
        """根据作业当前修订/分母状态，告诉回执这一行是否已经生效。"""
        result = dict(impact)
        job = store.find(self.MODULE, int(impact.get("job_id", 0) or 0))
        if job is None:
            result.update({"state": "job_missing", "state_label": "作业不存在", "effective": False})
            return result

        result.update({
            "job_name": job.get("作业名称", result.get("job_name", "")),
            "window": job.get("计算窗口", result.get("window", "")),
            "job_status": job.get("status", ""),
            "recalc_status": job.get("recalc_status", ""),
        })
        receipt_row_id = impact.get("receipt_row_id")
        current_rev_no = job.get("current_revision_no")
        revision = next(
            (r for r in store.rows("metric_result_revisions") if r.get("revision_no") == current_rev_no),
            None,
        )
        denoms: list[dict[str, Any]] = []
        if revision:
            for denom_no in revision.get("denominator_nos", []):
                denom = next((d for d in store.rows("metric_denominators") if d.get("denominator_no") == denom_no), None)
                if denom:
                    denoms.append(denom)

        effect = impact.get("effect")
        effective = False
        for denom in denoms:
            row_ids = {ref.get("receipt_row_id") for ref in denom.get("row_refs", [])}
            correction_ids = {ref.get("receipt_row_id") for ref in denom.get("correction_refs", [])}
            if effect in {"add", "update"} and receipt_row_id in row_ids:
                effective = True
            if effect == "remove" and receipt_row_id in correction_ids:
                effective = True

        if effective and not job.get("needs_recalculation"):
            state, label = "effective", "已生效"
        elif job.get("status") in {"待执行", "执行中"} and not current_rev_no:
            state, label = "pending_execution", "待执行（计算时引用最新有效行）"
        else:
            state, label = "pending_recalculation", "仍待重算"
        result.update({"state": state, "state_label": label, "effective": state == "effective"})
        result["href"] = (
            f"/metric_compute?jobs={job.get('id')}"
            f"&receipt={impact.get('receipt_no') or ''}"
            f"&source={impact.get('source_id') or ''}"
        )
        if revision:
            result["current_revision_no"] = revision.get("revision_no")
            result["denominator_nos"] = revision.get("denominator_nos", [])
        return result

    # ---- 重算并生成可追踪修订 ----

    def recalculate_job(
        self,
        entry_id: int,
        experiment_code: str | None = None,
        trigger: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = store.find(self.MODULE, entry_id)
        if job is None:
            return {"ok": False, "status_code": 404, "message": f"计算作业 {entry_id} 不存在"}

        trigger = dict(trigger or {"trigger_type": "manual_recalculation"})
        fixed_exp = experiment_code or job.get("实验编码") or job.get("experiment_code") or ""
        if fixed_exp:
            exp_codes = [str(fixed_exp)]
        else:
            window = job.get("计算窗口", "")
            start, end = parse_window_days(window)
            exp_codes = sorted({
                str(r.get("实验编码") or r.get("experiment_code") or "")
                for r in store.rows("exposure_log")
                if self._is_countable_exposure(r, window)
            })
            if not exp_codes:
                exp_codes = ["*"]

        denominator_rows = store.rows("metric_denominators")
        denominators: list[dict[str, Any]] = []
        for exp in exp_codes:
            scope_exp = None if exp == "*" else exp
            built = self.build_denominator(job, scope_exp)
            denom = {
                "id": _next_id(denominator_rows),
                "denominator_no": f"DEN-{entry_id}-{_next_id(denominator_rows):04d}",
                "claim_token": None,
                "job_id": entry_id,
                "experiment_code": exp,
                "window": built["window"],
                "window_start": built["window_start"],
                "window_end": built["window_end"],
                "checksum": built["checksum"],
                "valid_exposure_count": built["valid_exposure_count"],
                "去重曝光数": built["valid_exposure_count"],
                "分组分布": built["分组分布"],
                "row_refs": built["row_refs"],
                "valid_rows": built["row_refs"],
                "receipt_refs": built["receipt_refs"],
                "import_receipts": built["receipt_refs"],
                "receipts": built["receipt_refs"],
                "correction_refs": built["correction_refs"],
                "source_type_counts": built["source_type_counts"],
                "status": "committed",
                "claimed_at": _now(),
                "committed_at": _now(),
                "trigger": trigger,
                "conflicted": False,
                "stale": False,
            }
            denominator_rows.append(denom)
            denominators.append(denom)

        total_count = sum(int(d["valid_exposure_count"]) for d in denominators)
        revision_rows = store.rows("metric_result_revisions")
        old_revision_no = job.get("current_revision_no")
        revision_no = f"MR-{entry_id}-{_next_id(revision_rows):04d}"
        revision = {
            "id": _next_id(revision_rows),
            "revision_no": revision_no,
            "job_id": entry_id,
            "作业名称": job.get("作业名称", ""),
            "指标编码": job.get("指标编码", ""),
            "experiment_code": fixed_exp or "*",
            "实验编码": fixed_exp or "*",
            "计算窗口": job.get("计算窗口", ""),
            "window_start": denominators[0].get("window_start") if denominators else None,
            "window_end": denominators[0].get("window_end") if denominators else None,
            "denominator_nos": [d["denominator_no"] for d in denominators],
            "denominators": [
                {k: d.get(k) for k in (
                    "denominator_no", "experiment_code", "valid_exposure_count", "去重曝光数",
                    "分组分布", "receipt_refs", "correction_refs", "checksum", "source_type_counts"
                )}
                for d in denominators
            ],
            "experiment_denominators": {
                str(d["experiment_code"]): {
                    "denominator_no": d["denominator_no"],
                    "valid_exposure_count": d["valid_exposure_count"],
                    "去重曝光数": d["valid_exposure_count"],
                    "分组分布": d["分组分布"],
                    "receipt_refs": d["receipt_refs"],
                    "correction_refs": d["correction_refs"],
                    "checksum": d["checksum"],
                }
                for d in denominators
            },
            "denominator_refs": [
                {
                    "denominator_no": d["denominator_no"],
                    "experiment_code": d["experiment_code"],
                    "count": d["valid_exposure_count"],
                    "receipt_refs": d["receipt_refs"],
                }
                for d in denominators
            ],
            "valid_exposure_count": total_count,
            "去重曝光数": total_count,
            "result": {
                "曝光分母": total_count,
                "denominator": total_count,
                "有效曝光数": total_count,
                "experiment_denominators": {
                    str(d["experiment_code"]): d["valid_exposure_count"] for d in denominators
                },
            },
            "trigger_type": trigger.get("trigger_type", "recalculation"),
            "trigger_import_no": trigger.get("import_no") or trigger.get("trigger_import_no"),
            "trigger_receipt_row_id": trigger.get("receipt_row_id"),
            "trigger_source_id": trigger.get("source_id"),
            "claim_token": trigger.get("claim_token"),
            "revises": old_revision_no,
            "status": "current",
            "created_at": _now(),
        }
        revision_rows.append(revision)
        if old_revision_no:
            for old in revision_rows:
                if old.get("revision_no") == old_revision_no:
                    old["status"] = "superseded"
                    old["superseded_by"] = revision_no

        job["status"] = "已完成"
        job["pending"] = False
        job["abnormal"] = False
        job["作业状态"] = "已完成"
        job["窗口水位"] = _now()
        job["last_calculated_at"] = _now()
        job["current_revision_no"] = revision_no
        job["denominator_nos"] = revision["denominator_nos"]
        job["current_denominator_no"] = revision["denominator_nos"][0] if revision["denominator_nos"] else None
        job["denominator_status"] = "已按最新有效行出账"
        job["needs_recalculation"] = False
        job["recalc_status"] = "已生效"
        job["结果修订状态"] = f"当前修订 {revision_no}" + (f"，修订自 {old_revision_no}" if old_revision_no else "")
        job["pending_corrections"] = []
        job["denominator_summary"] = revision["experiment_denominators"]
        job["valid_exposure_count"] = total_count
        job["有效曝光数"] = total_count
        job["exposure_count"] = total_count
        job["denominator_count"] = total_count
        job["current_denominator_refs"] = revision["denominator_refs"]

        # 关闭本次触发相关或该作业的未决冲突。
        for conflict in store.rows("metric_conflicts"):
            if conflict.get("job_id") != entry_id or conflict.get("status") == "resolved":
                continue
            if trigger.get("import_no") and conflict.get("correction_import_no") != trigger.get("import_no"):
                continue
            conflict["status"] = "resolved"
            conflict["resolved_at"] = _now()
            conflict["resolved_revision_no"] = revision_no

        return {
            "ok": True,
            "message": f"作业 {entry_id} 已完成窗口核对，生成修订 {revision_no}",
            "job": job,
            "revision": revision,
            "denominators": denominators,
        }

    def list_revisions(self, entry_id: int) -> list[dict[str, Any]]:
        return [
            r for r in store.rows("metric_result_revisions")
            if int(r.get("job_id", 0) or 0) == entry_id
        ]

    def export_job_results(self, metric_code: str) -> list[dict[str, Any]]:
        """导出指定指标的完成作业清单，供报告生成器编排数据窗口。"""
        return [
            {
                "作业名称": r.get("作业名称", ""),
                "实验编码": r.get("实验编码", ""),
                "计算窗口": r.get("计算窗口", ""),
                "窗口水位": r.get("窗口水位", ""),
                "current_revision_no": r.get("current_revision_no", ""),
                "valid_exposure_count": r.get("denominator_summary", ""),
            }
            for r in store.rows(self.MODULE)
            if r.get("指标编码") == metric_code and r.get("status") == "已完成"
        ]
