"""曝光日志接入领域服务：CSV 补传/更正、逐行回执、幂等去重与异常丢弃。

数据链路位置：日志接入层，是实验侧原始数据的第一入口，消费分组分配器的
分组标签，把当前有效曝光供给指标计算作为分母。

身份口径：``(来源编号, 实验编码)`` 标识一条源记录；相同来源编号在不同实验下
分别核计。已接受过的原样内容（即使是已被更正的历史版本）再次上传只产生
“重复忽略”回执，不会复活旧时间窗口。
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services.windowing import parse_exposure_time, windows_for_day
from app.store import store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(r.get("id", 0) or 0) for r in rows), default=0) + 1


def _csv_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    # 与通用导出保持一致，避免回执中的来源/原因被 Excel 当成公式执行。
    if text[:1] in {"=", "+", "-", "@"}:
        return "'" + text
    return text


def _norm_header(value: Any) -> str:
    return re.sub(r"[\s_\-/\\.。:：（）()\[\]]", "", str(value or "")).strip().lower()


HEADER_ALIASES: dict[str, set[str]] = {
    "source_id": {
        "来源编号", "源记录编号", "源编号", "来源id", "来源编码", "sourceid",
        "sourcerecordid", "recordid", "recordno", "source", "编号",
    },
    "batch_id": {"日志批次", "批次", "批次号", "batchid", "batchno", "batch"},
    "experiment_code": {
        "实验编码", "实验id", "实验编号", "experimentcode", "expcode",
        "experimentid", "experiment", "exp",
    },
    "user_id": {"用户标识", "用户id", "用户编号", "userid", "uid", "user"},
    "group_name": {"分组名称", "分组", "组别", "groupname", "group", "variant", "variantname"},
    "occurred_at": {
        "曝光时间", "曝光日期", "发生时间", "事件时间", "业务时间", "曝光发生时间", "日期", "时间",
        "occurredat", "eventtime", "eventdate", "exposuretime", "timestamp", "time", "date",
    },
}

CANONICAL_CHINESE = {
    "source_id": "来源编号",
    "batch_id": "日志批次",
    "experiment_code": "实验编码",
    "user_id": "用户标识",
    "group_name": "分组名称",
    "occurred_at": "曝光时间",
}


class ExposureLogService(BaseModuleService):
    MODULE = "exposure_log"
    ENTITY = "曝光记录"
    OPERATION_TERM = "接入"
    LIST_FIELDS = ["来源编号", "日志批次", "实验编码", "用户标识", "分组名称", "曝光时间"]
    REQUIRED_FIELDS = ["日志批次", "实验编码", "用户标识"]
    STATUS_ORDER = ["已接收", "已去重", "已更正", "异常丢弃"]
    ACTION_RULES = {"执行去重": "已去重", "丢弃异常": "异常丢弃"}
    NEGATIVE_ACTIONS = []

    # ---- 列表查询：关键字同时覆盖来源编号和原日志批次，保持旧检索习惯 ----

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(self.MODULE))
        for field, expected in (filters or {}).items():
            if field in self.LIST_FIELDS:
                rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            search_fields = ["来源编号", "source_id", "日志批次", "实验编码", "用户标识"]
            folded = keyword.casefold()
            rows = [
                row for row in rows
                if any(folded in str(row.get(field, "")).casefold() for field in search_fields)
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    # ---- CSV 解析 ----

    def _canonical_key(self, header: Any) -> str | None:
        normalized = _norm_header(header)
        for key, aliases in HEADER_ALIASES.items():
            if normalized in {_norm_header(a) for a in aliases}:
                return key
        return None

    def parse_tabular(
        self,
        content: str | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """解析 CSV 文本或已读入的 dict 行，输出带文件行号的规范行。"""
        records: list[dict[str, Any]] = []
        columns: list[str] = []

        if rows is not None:
            for index, item in enumerate(rows, start=2):
                if not isinstance(item, dict):
                    continue
                file_row = int(item.get("file_row") or item.get("行号") or index)
                canonical = self._canonicalize_dict(item)
                records.append({
                    "file_row": file_row,
                    "raw": {str(k): ("" if v is None else str(v)) for k, v in item.items()},
                    **canonical,
                })
            columns = sorted({k for r in records for k in r["raw"].keys()})
            return {"ok": True, "columns": columns, "records": records, "error": None}

        text = str(content or "")
        if text.startswith("﻿"):
            text = text.lstrip("﻿")
        if not text.strip():
            return {"ok": False, "columns": [], "records": [], "error": "CSV 内容为空"}

        try:
            reader = csv.reader(io.StringIO(text))
            raw_headers = next(reader, [])
            if not raw_headers:
                return {"ok": False, "columns": [], "records": [], "error": "CSV 缺少表头"}
            columns = [str(h).strip() for h in raw_headers]
            index_map = {i: self._canonical_key(header) for i, header in enumerate(raw_headers)}
            for logical_row, raw_values in enumerate(reader, start=2):
                if not any(str(v or "").strip() for v in raw_values):
                    continue
                raw_dict = {
                    columns[i] if i < len(columns) and columns[i] else f"column_{i + 1}":
                    (raw_values[i] if i < len(raw_values) else "")
                    for i in range(max(len(columns), len(raw_values)))
                }
                canonical: dict[str, str] = {key: "" for key in HEADER_ALIASES}
                for i, key in index_map.items():
                    if key and i < len(raw_values):
                        canonical[key] = str(raw_values[i] or "").strip()
                # 兼容调用方直接传中文/英文字典但表头未命中别名的情况。
                canonical.update({k: v for k, v in self._canonicalize_dict(raw_dict).items() if v})
                records.append({
                    "file_row": logical_row,
                    "raw": raw_dict,
                    **canonical,
                })
        except csv.Error as exc:
            return {"ok": False, "columns": columns, "records": records, "error": f"CSV 解析失败：{exc}"}

        return {"ok": True, "columns": columns, "records": records, "error": None}

    def _canonicalize_dict(self, data: dict[str, Any]) -> dict[str, str]:
        result = {key: "" for key in HEADER_ALIASES}
        for key, value in data.items():
            canonical = self._canonical_key(key)
            if canonical:
                result[canonical] = str(value or "").strip()
        # 已经是 canonical key 时直接保留。
        for key in HEADER_ALIASES:
            if data.get(key) is not None and str(data.get(key)).strip():
                result[key] = str(data[key]).strip()
        return result

    def _validate_record(self, record: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not record.get("source_id"):
            errors.append("缺少来源编号")
        if not record.get("experiment_code"):
            errors.append("缺少实验编码")
        _normalized, _day, time_error = parse_exposure_time(record.get("occurred_at"))
        if time_error:
            errors.append(time_error)
        return errors

    def preview_import(
        self,
        filename: str = "",
        content: str | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """只解析和校验，不写曝光数据；供选择文件后的预览和服务端复核使用。"""
        parsed = self.parse_tabular(content, rows)
        if not parsed["ok"]:
            return {"ok": False, "filename": filename, "rows": [], "message": parsed["error"]}

        preview_rows: list[dict[str, Any]] = []
        valid_count = 0
        invalid_count = 0
        for record in parsed["records"]:
            errors = self._validate_record(record)
            normalized, day, _ = parse_exposure_time(record.get("occurred_at"))
            planned = self._plan_record(record, normalized)
            row = {
                "file_row": record["file_row"],
                "line_number": record["file_row"],
                "row_number": record["file_row"],
                "row_no": record["file_row"],
                "rowNo": record["file_row"],
                "lineNo": record["file_row"],
                "csv_row": record["file_row"],
                "line": record["file_row"],
                "文件行号": record["file_row"],
                **{k: record.get(k, "") for k in HEADER_ALIASES},
                **{CANONICAL_CHINESE[k]: record.get(k, "") for k in HEADER_ALIASES},
                "normalized_time": normalized,
                "window_key": day,
                "valid": not errors,
                "errors": errors,
                "reason": "；".join(errors),
                "error": "；".join(errors),
                "reject_reason": "；".join(errors),
                "拒绝原因": "；".join(errors),
                "preview_status": planned,
                "raw": record.get("raw", {}),
            }
            preview_rows.append(row)
            if errors:
                invalid_count += 1
            else:
                valid_count += 1

        return {
            "ok": True,
            "filename": filename,
            "columns": parsed["columns"],
            "rows": preview_rows,
            "total_rows": len(preview_rows),
            "valid_count": valid_count,
            "invalid_count": invalid_count,
            "message": f"预览 {len(preview_rows)} 行：可提交 {valid_count} 行，问题行 {invalid_count} 行",
        }

    def _plan_record(self, record: dict[str, Any], normalized_time: str | None) -> str:
        if self._validate_record(record):
            return "rejected"
        content_hash = self._content_hash(record, normalized_time or "")
        exposure_rows = store.rows(self.MODULE)
        if any(str(r.get("content_hash") or "") == content_hash for r in exposure_rows):
            return "duplicate"
        current = self._find_current(record.get("source_id", ""), record.get("experiment_code", ""))
        return "corrected" if current is not None else "accepted"

    def _content_hash(self, record: dict[str, Any], normalized_time: str) -> str:
        material = "|".join([
            record.get("source_id", ""),
            record.get("experiment_code", ""),
            record.get("user_id", ""),
            record.get("group_name", ""),
            record.get("batch_id", ""),
            normalized_time,
        ])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _find_current(self, source_id: str, experiment_code: str) -> dict[str, Any] | None:
        current = [
            r for r in store.rows(self.MODULE)
            if r.get("valid", True) is not False
            and str(r.get("来源编号") or r.get("source_id") or "") == str(source_id)
            and str(r.get("实验编码") or r.get("experiment_code") or "") == str(experiment_code)
        ]
        current.sort(key=lambda r: int(r.get("id", 0) or 0))
        return current[-1] if current else None

    # ---- 提交导入/更正并生成回执 ----

    def submit_import(
        self,
        filename: str = "",
        content: str | None = None,
        rows: list[dict[str, Any]] | None = None,
        import_no: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        receipt_table = store.rows("exposure_import_receipts")

        if idempotency_key:
            existing = next((r for r in receipt_table if r.get("idempotency_key") == idempotency_key), None)
            if existing:
                return self.get_receipt(str(existing["import_no"]))  # type: ignore[return-value]

        custom_no = str(import_no or "").strip()
        if custom_no:
            existing = next((r for r in receipt_table if str(r.get("import_no")) == custom_no), None)
            if existing:
                return self.get_receipt(custom_no)  # type: ignore[return-value]

        parsed = self.parse_tabular(content, rows)
        receipt_id = _next_id(receipt_table)
        business_no = custom_no or f"IMP-{datetime.now():%Y%m%d}-{receipt_id:04d}"
        if not parsed["ok"]:
            receipt = {
                "id": receipt_id,
                "import_no": business_no,
                "import_id": business_no,
                "receipt_no": business_no,
                "导入编号": business_no,
                "filename": filename,
                "idempotency_key": idempotency_key,
                "created_at": _now(),
                "processed": False,
                "status": "failed",
                "status_label": "解析失败",
                "total_rows": 0,
                "accepted_count": 0,
                "corrected_count": 0,
                "duplicate_count": 0,
                "rejected_count": 0,
                "rows": [],
                "message": parsed["error"],
            }
            receipt_table.append(receipt)
            store.rows("import_receipts").append(receipt)
            return self._response_receipt(receipt, ok=False, message=str(parsed["error"]))

        receipt_rows: list[dict[str, Any]] = []
        exposure_rows = store.rows(self.MODULE)
        counts = {"accepted": 0, "corrected": 0, "duplicate": 0, "rejected": 0}

        for record in parsed["records"]:
            file_row = int(record["file_row"])
            row_id = f"RR-{receipt_id}-{file_row}"
            base_row: dict[str, Any] = {
                "row_id": row_id,
                "file_row": file_row,
                "line_number": file_row,
                **{key: record.get(key, "") for key in HEADER_ALIASES},
                **{CANONICAL_CHINESE[key]: record.get(key, "") for key in HEADER_ALIASES},
                "filename": filename,
                "import_no": business_no,
                "receipt_no": business_no,
                "raw": record.get("raw", {}),
            }
            errors = self._validate_record(record)
            normalized_time, day_key, time_error = parse_exposure_time(record.get("occurred_at"))
            base_row["normalized_time"] = normalized_time
            base_row["window_key"] = day_key

            if errors:
                reason = "；".join(errors)
                base_row.update({
                    "status": "rejected",
                    "status_code": "rejected",
                    "status_label": "已拒收",
                    "状态": "已拒收",
                    "ok": False,
                    "accepted": False,
                    "rejected": True,
                    "duplicate": False,
                    "corrected": False,
                    "counted": False,
                    "reason": reason,
                    "reject_reason": reason,
                    "拒绝原因": reason,
                })
                counts["rejected"] += 1
                receipt_rows.append(base_row)
                continue

            content_hash = self._content_hash(record, normalized_time or "")
            historical = next(
                (r for r in exposure_rows if str(r.get("content_hash") or "") == content_hash),
                None,
            )
            if historical is not None:
                reason = "与已接受源记录内容完全一致，幂等忽略，不重复计入曝光"
                if historical.get("valid", True) is False:
                    reason = "该原样版本已处理且被后续更正取代；重传只核对行号，不回滚当前有效时间"
                base_row.update({
                    "status": "duplicate",
                    "status_code": "duplicate",
                    "status_label": "重复未计数",
                    "状态": "重复未计数",
                    "ok": True,
                    "accepted": False,
                    "rejected": False,
                    "duplicate": True,
                    "corrected": False,
                    "counted": False,
                    "reason": reason,
                    "reject_reason": "",
                    "拒绝原因": "",
                    "content_hash": content_hash,
                    "exposure_id": historical.get("id"),
                    "original_import_no": historical.get("import_no"),
                    "original_file_row": historical.get("file_row"),
                    "original_receipt_row_id": historical.get("receipt_row_id"),
                    "original_window_key": historical.get("effective_date"),
                })
                counts["duplicate"] += 1
                receipt_rows.append(base_row)
                continue

            current = self._find_current(record["source_id"], record["experiment_code"])
            exposure_id = _next_id(exposure_rows)
            is_correction = current is not None
            old_day = self._exposure_day(current) if current else None
            old_exposure_id = current.get("id") if current else None
            version = int(current.get("version", 1) or 1) + 1 if current else 1

            exposure = {
                "id": exposure_id,
                "来源编号": record["source_id"],
                "source_id": record["source_id"],
                "日志批次": record["batch_id"] or f"CSV-{business_no}",
                "实验编码": record["experiment_code"],
                "experiment_code": record["experiment_code"],
                "用户标识": record["user_id"],
                "分组名称": record["group_name"] or self._lookup_group(record["experiment_code"], record["user_id"]),
                "曝光时间": normalized_time,
                "原始曝光时间": record.get("occurred_at", ""),
                "effective_date": day_key,
                "window_key": day_key,
                "content_hash": content_hash,
                "version": version,
                "valid": True,
                "current": True,
                "source_type": "csv",
                "import_no": business_no,
                "receipt_no": business_no,
                "receipt_row_id": row_id,
                "file_row": file_row,
                "status": "已去重",
                "pending": False,
                "abnormal": False,
                "created_at": _now(),
            }
            exposure_rows.append(exposure)

            changed_fields: list[str] = []
            if current is not None:
                field_pairs = [
                    ("用户标识", "user_id"), ("分组名称", "group_name"),
                    ("日志批次", "batch_id"), ("曝光时间", "occurred_at"),
                ]
                for chinese, canonical in field_pairs:
                    old_value = str(current.get(chinese, "") or "")
                    new_value = str(normalized_time if canonical == "occurred_at" else record.get(canonical, ""))
                    if old_value != new_value and new_value:
                        changed_fields.append(chinese)
                current.update({
                    "valid": False,
                    "current": False,
                    "status": "已更正",
                    "pending": False,
                    "superseded_by": exposure_id,
                    "superseded_at": _now(),
                })

            impact_refs = self._register_metric_change({
                "source_id": record["source_id"],
                "experiment_code": record["experiment_code"],
                "old_window_key": old_day,
                "window_key": day_key,
                "import_no": business_no,
                "receipt_row_id": row_id,
                "old_exposure_id": old_exposure_id,
                "new_exposure_id": exposure_id,
                "original_import_no": current.get("import_no") if current else None,
            }) if is_correction or old_day != day_key else []

            if is_correction:
                base_row.update({
                    "status": "corrected",
                    "status_code": "corrected",
                    "status_label": "已更正",
                    "状态": "已更正",
                    "accepted": True,
                    "corrected": True,
                    "duplicate": False,
                    "rejected": False,
                    "counted": True,
                    "reason": "源记录更正已生效为当前有效版本；旧版本不再计入分母",
                    "changed_fields": changed_fields,
                    "变更字段": changed_fields,
                    "old_exposure_id": old_exposure_id,
                    "new_exposure_id": exposure_id,
                    "old_window_key": old_day,
                    "window_key": day_key,
                    "affected_windows": sorted({str(i.get("window_day")) for i in impact_refs if i.get("window_day")}),
                    "impact_refs": impact_refs,
                    "affected_jobs": impact_refs,
                })
                counts["corrected"] += 1
            else:
                base_row.update({
                    "status": "accepted",
                    "status_code": "accepted",
                    "status_label": "已接受",
                    "状态": "已接受",
                    "accepted": True,
                    "corrected": False,
                    "duplicate": False,
                    "rejected": False,
                    "counted": True,
                    "reason": "",
                    "exposure_id": exposure_id,
                    "window_key": day_key,
                    "affected_windows": sorted({str(i.get("window_day")) for i in impact_refs if i.get("window_day")}),
                    "impact_refs": impact_refs,
                    "affected_jobs": impact_refs,
                })
                counts["accepted"] += 1
            base_row["ok"] = True
            receipt_rows.append(base_row)

        total = len(receipt_rows)
        if counts["rejected"] and (counts["accepted"] or counts["corrected"] or counts["duplicate"]):
            receipt_status, receipt_label = "partial_rejected", "部分拒收"
        elif counts["rejected"] == total and total:
            receipt_status, receipt_label = "all_rejected", "全部拒收"
        else:
            receipt_status, receipt_label = "processed", "已办结"

        receipt = {
            "id": receipt_id,
            "import_no": business_no,
            "import_id": business_no,
            "receipt_no": business_no,
            "导入编号": business_no,
            "filename": filename or "exposure-correction.csv",
            "idempotency_key": idempotency_key,
            "created_at": _now(),
            "processed": True,
            "status": receipt_status,
            "status_label": receipt_label,
            "total_rows": total,
            "total": total,
            "accepted_count": counts["accepted"],
            "corrected_count": counts["corrected"],
            "duplicate_count": counts["duplicate"],
            "rejected_count": counts["rejected"],
            "valid_count": counts["accepted"] + counts["corrected"],
            "counted_count": counts["accepted"] + counts["corrected"],
            "rows": receipt_rows,
            "download_url": f"/api/exposure_log/imports/{business_no}/download",
            "receipt_url": f"/api/exposure_log/imports/{business_no}",
            "message": (
                f"导入编号 {business_no}：新增 {counts['accepted']}，更正 {counts['corrected']}，"
                f"重复忽略 {counts['duplicate']}，拒收 {counts['rejected']}"
            ),
        }
        receipt_table.append(receipt)
        # 兼容更贴近业务命名的内部表；两个键保存同一个回执对象，不产生两份状态。
        store.rows("import_receipts").append(receipt)
        return self._response_receipt(receipt, ok=True, message=receipt["message"])

    def _lookup_group(self, exp_code: str, user_id: str) -> str:
        info = self.consume_from_variant_assign(exp_code, user_id)
        return info.get("分组名称", "未分组") if info.get("ok") else "未分组"

    def _exposure_day(self, row: dict[str, Any] | None) -> str | None:
        if not row:
            return None
        return row.get("effective_date") or parse_exposure_time(row.get("曝光时间"))[1]

    def _register_metric_change(self, change: dict[str, Any]) -> list[dict[str, Any]]:
        from app.services.metric_compute import MetricComputeService
        return MetricComputeService().register_exposure_change(change)

    def _attach_follow_up_corrections(self, receipt: dict[str, Any]) -> None:
        """把原始/重传回执行关联到其后到达的更正，保证重开旧回执也能看到重算轨迹。"""
        correction_rows: list[tuple[str, dict[str, Any]]] = []
        for other in store.rows("exposure_import_receipts"):
            for candidate in other.get("rows", []):
                if candidate.get("status") == "corrected":
                    correction_rows.append((str(other.get("import_no")), candidate))

        for row in receipt.get("rows", []):
            source_id = row.get("source_id")
            exp_code = row.get("experiment_code")
            if not source_id or not exp_code:
                continue
            follow_ups: list[dict[str, Any]] = []
            extra_refs: list[dict[str, Any]] = []
            for correction_no, correction in correction_rows:
                if correction.get("row_id") == row.get("row_id"):
                    continue
                if correction.get("source_id") != source_id:
                    continue
                if correction.get("experiment_code") != exp_code:
                    continue
                summary = {
                    "import_no": correction_no,
                    "receipt_no": correction_no,
                    "file_row": correction.get("file_row"),
                    "receipt_row_id": correction.get("row_id"),
                    "old_window": correction.get("old_window_key"),
                    "new_window": correction.get("window_key"),
                    "changed_fields": correction.get("changed_fields") or [],
                }
                if summary not in follow_ups:
                    follow_ups.append(summary)
                for ref in correction.get("impact_refs") or []:
                    if ref not in extra_refs:
                        extra_refs.append(ref)
            if follow_ups:
                row["follow_up_corrections"] = follow_ups
                row["后续更正"] = "；".join(
                    f"{item['import_no']}({item.get('old_window')}→{item.get('new_window')})"
                    for item in follow_ups
                )
                extra_job_ids = {ref.get("job_id") for ref in extra_refs}
                # 同一作业若已有后续更正影响，旧的“新增入账”提示已被 remove/update 轨迹取代，
                # 不再向值班员展示一个互相矛盾的待重算标记。
                own_refs = [
                    ref for ref in (row.get("impact_refs") or [])
                    if ref.get("job_id") not in extra_job_ids
                ]
                row["_all_impact_refs"] = extra_refs + own_refs

    def _decorate_impacts(self, receipt: dict[str, Any]) -> None:
        from app.services.metric_compute import MetricComputeService
        metric_service = MetricComputeService()
        flattened: list[dict[str, Any]] = []
        for row in receipt.get("rows", []):
            refs = row.get("_all_impact_refs") or row.get("impact_refs") or []
            unique_refs: list[dict[str, Any]] = []
            for ref in refs:
                key = (ref.get("job_id"), ref.get("effect"), ref.get("receipt_row_id"))
                if not any((r.get("job_id"), r.get("effect"), r.get("receipt_row_id")) == key for r in unique_refs):
                    unique_refs.append(ref)
            described = [metric_service.describe_impact(ref) for ref in unique_refs]
            row["affected_jobs"] = described
            for item in described:
                if not any(x.get("job_id") == item.get("job_id") and x.get("effect") == item.get("effect") for x in flattened):
                    flattened.append(item)
        receipt["affected_jobs"] = flattened

    def _response_receipt(self, receipt: dict[str, Any], ok: bool, message: str) -> dict[str, Any]:
        serialized = self.serialize_receipt(receipt)
        return {
            "ok": ok,
            "message": message,
            **serialized,
            "receipt": serialized,
            "entry": serialized,
        }

    def serialize_receipt(self, receipt: dict[str, Any]) -> dict[str, Any]:
        self._attach_follow_up_corrections(receipt)
        self._decorate_impacts(receipt)
        receipt["import_id"] = receipt.get("import_no")
        receipt["receipt_no"] = receipt.get("import_no")
        receipt["receipt_id"] = receipt.get("import_no")
        receipt["import_number"] = receipt.get("import_no")
        receipt["导入编号"] = receipt.get("import_no")
        receipt["download_url"] = f"/api/exposure_log/imports/{receipt.get('import_no')}/download"
        receipt["receipt_url"] = f"/api/exposure_log/imports/{receipt.get('import_no')}"
        job_ids: list[int] = []
        for row in receipt.get("rows", []):
            file_row = row.get("file_row")
            row.setdefault("line_number", file_row)
            row["row_number"] = file_row
            row["row_no"] = file_row
            row["rowNo"] = file_row
            row["lineNo"] = file_row
            row["csv_row"] = file_row
            row["line"] = file_row
            row["文件行号"] = file_row
            row["处理状态"] = row.get("status_label") or row.get("状态") or row.get("status")
            reason = row.get("reason") or row.get("reject_reason") or row.get("拒绝原因") or ""
            if reason:
                row["error"] = reason
                row["rejection_reason"] = reason
                row["message"] = reason
            impacts = row.get("affected_jobs") or []
            row["job_ids"] = [int(j.get("job_id")) for j in impacts if j.get("job_id") is not None]
            for job_id in row["job_ids"]:
                if job_id not in job_ids:
                    job_ids.append(job_id)
        receipt["affected_job_ids"] = job_ids
        receipt["job_ids"] = job_ids
        return receipt

    def preview_csv(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """常见命名别名：兼容 preview_csv(csv_text) / preview_csv(filename, text)。"""
        for arg in args:
            if not isinstance(arg, str):
                continue
            if "\n" in arg or ("," in arg and not arg.lower().endswith(".csv")):
                kwargs.setdefault("content", arg)
            elif arg.lower().endswith(".csv"):
                kwargs.setdefault("filename", arg)
            else:
                kwargs.setdefault("content", arg)
        return self.preview_import(**kwargs)

    def import_csv(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """常见命名别名：兼容 import_csv(csv_text, filename) 等调用。"""
        positional = list(args)
        if positional and "content" not in kwargs and isinstance(positional[0], str) and (
            "\n" in positional[0] or "," in positional[0] or len(positional) == 1
        ):
            kwargs["content"] = positional.pop(0)
        if positional and "filename" not in kwargs and isinstance(positional[0], str):
            kwargs["filename"] = positional.pop(0)
        return self.submit_import(**kwargs)

    def submit_correction(self, **kwargs: Any) -> dict[str, Any]:
        return self.submit_import(**kwargs)

    # ---- 回执查询/下载/重算 ----

    def list_receipts(self) -> dict[str, Any]:
        rows = list(store.rows("exposure_import_receipts"))
        rows.sort(key=lambda r: int(r.get("id", 0) or 0), reverse=True)
        items = []
        for receipt in rows:
            summary = {k: v for k, v in receipt.items() if k != "rows"}
            summary["row_count"] = len(receipt.get("rows", []))
            items.append(summary)
        return {"items": items, "total": len(items), "page": 1, "size": max(len(items), 1)}

    def get_receipt(self, identifier: str) -> dict[str, Any] | None:
        text = str(identifier or "").strip()
        table = store.rows("exposure_import_receipts")
        receipt = next((
            r for r in table
            if text in {str(r.get("import_no", "")), str(r.get("import_id", "")),
                        str(r.get("receipt_no", "")), str(r.get("导入编号", "")), str(r.get("id", ""))}
        ), None)
        if receipt is None:
            # 容错：如果调用方把来源编号（如 X7）当作凭证号，返回包含它的最新回执。
            candidate = None
            for item in reversed(table):
                if any(str(row.get("source_id") or "") == text for row in item.get("rows", [])):
                    candidate = item
                    break
            receipt = candidate
        return self.serialize_receipt(receipt) if receipt else None

    def receipt_csv(self, identifier: str) -> str | None:
        receipt = self.get_receipt(identifier)
        if receipt is None:
            return None
        output = io.StringIO()
        output.write("﻿")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow([
            "导入编号", "文件名", "文件行号", "来源编号", "实验编码", "用户标识", "分组名称",
            "曝光时间", "处理状态", "是否计数", "拒绝原因", "原导入编号", "原文件行号",
            "变更字段", "影响窗口", "受影响作业", "重算状态", "后续更正回执",
        ])
        import_no = receipt.get("import_no", "")
        filename = receipt.get("filename", "")
        for row in receipt.get("rows", []):
            jobs = row.get("affected_jobs") or []
            values = [
                import_no,
                filename,
                row.get("file_row", ""),
                row.get("source_id", ""),
                row.get("experiment_code", ""),
                row.get("user_id", ""),
                row.get("group_name", ""),
                row.get("normalized_time") or row.get("occurred_at", ""),
                row.get("status_label") or row.get("status", ""),
                "是" if row.get("counted") else "否",
                row.get("reason") or row.get("reject_reason") or "",
                row.get("original_import_no") or "",
                row.get("original_file_row") or "",
                "、".join(row.get("changed_fields") or []),
                "、".join(row.get("affected_windows") or []),
                "；".join(f"{j.get('job_id')}:{j.get('window_day') or j.get('window')}" for j in jobs),
                "；".join(f"{j.get('job_id')}={j.get('state_label')}" for j in jobs),
                row.get("后续更正") or "",
            ]
            writer.writerow([_csv_safe(value) for value in values])
        return output.getvalue()

    def recalculate_for_receipt(self, identifier: str) -> dict[str, Any]:
        receipt = self.get_receipt(identifier)
        if receipt is None:
            return {"ok": False, "status_code": 404, "message": f"导入回执 {identifier} 不存在"}
        from app.services.metric_compute import MetricComputeService
        metric_service = MetricComputeService()
        job_ids: list[int] = []
        for row in receipt.get("rows", []):
            for impact in row.get("affected_jobs") or row.get("impact_refs") or []:
                job_id = int(impact.get("job_id", 0) or 0)
                if job_id and job_id not in job_ids:
                    job_ids.append(job_id)
        results = []
        for job_id in job_ids:
            results.append(metric_service.recalculate_job(job_id, trigger={
                "trigger_type": "correction_receipt_recalculation",
                "import_no": receipt.get("import_no"),
            }))
        fresh = self.get_receipt(str(receipt.get("import_no")))
        ok = all(bool(r.get("ok")) for r in results)
        return {
            "ok": ok,
            "message": f"已重算回执 {identifier} 影响的 {len(job_ids)} 个作业窗口",
            "job_ids": job_ids,
            "results": results,
            "receipt": fresh,
        }

    def source_trace(self, source_id: str, experiment_code: str | None = None) -> dict[str, Any]:
        rows = [
            r for r in store.rows(self.MODULE)
            if (str(r.get("来源编号") or r.get("source_id") or "") == source_id)
            and (not experiment_code or str(r.get("实验编码") or "") == experiment_code)
        ]
        rows.sort(key=lambda r: int(r.get("id", 0) or 0))
        from app.services.metric_compute import MetricComputeService
        metric_service = MetricComputeService()
        windows = []
        for row in rows:
            day = self._exposure_day(row)
            jobs = []
            if row.get("valid", True) is not False and day:
                for job in windows_for_day(day, store.rows("metric_compute")):
                    job_exp = str(job.get("实验编码") or "")
                    if experiment_code and job_exp and job_exp != experiment_code:
                        continue
                    jobs.append({
                        "job_id": job.get("id"),
                        "job_name": job.get("作业名称"),
                        "window": job.get("计算窗口"),
                        "current_revision_no": job.get("current_revision_no"),
                        "recalc_status": job.get("recalc_status"),
                    })
            windows.append({
                "exposure_id": row.get("id"),
                "version": row.get("version", 1),
                "valid": row.get("valid", True),
                "status": row.get("status"),
                "experiment_code": row.get("实验编码"),
                "day": day,
                "import_no": row.get("import_no"),
                "file_row": row.get("file_row"),
                "jobs": jobs,
            })
        return {
            "ok": True,
            "source_id": source_id,
            "experiment_code": experiment_code,
            "versions": windows,
            "current": next((w for w in reversed(windows) if w["valid"]), None),
        }

    # ---- 既有批次/清洗能力 ----

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "执行去重":
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            result = self.dedup_batch(str(entry.get("日志批次", "")))
            return store.find(self.MODULE, entry_id), str(result.get("message"))
        return super().run_action(entry_id, action)

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
                row["valid"] = False
                row["丢弃原因"] = "批次内重复曝光"
                dropped += 1
            else:
                seen_users.add(uid)
                row["status"] = "已去重"
                row["pending"] = False
                row["valid"] = True
                if not row.get("来源编号"):
                    row["来源编号"] = f"ONLINE-{row.get('id')}"
                    row["source_id"] = row["来源编号"]
                row["source_type"] = "online"
                row["effective_date"] = parse_exposure_time(row.get("曝光时间"))[1]
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
        entry["valid"] = False
        entry["丢弃原因"] = reason
        freshness_rows = store.rows("data_freshness")
        freshness_rows.append({
            "id": _next_id(freshness_rows),
            "监控对象": f"exposure_log:{entry_id}",
            "数据域": "日志接入",
            "最新水位": _now(),
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
            and r.get("valid", True) is not False
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
            row_id = _next_id(rows)
            source_id = f"ONLINE-{row_id}"
            now = _now()
            rows.append({
                "id": row_id,
                "来源编号": source_id,
                "source_id": source_id,
                "日志批次": batch_id,
                "实验编码": exp_code,
                "用户标识": uid,
                "分组名称": group_info.get("分组名称", "未分组") if group_info.get("ok") else "未分组",
                "曝光时间": now,
                "effective_date": parse_exposure_time(now)[1],
                "source_type": "online",
                "valid": True,
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
            "当前有效": sum(1 for r in rows if r.get("valid", True) is not False and r.get("status") == "已去重"),
        }
