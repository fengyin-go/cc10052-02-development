"""曝光补传回执与指标窗口修订的扩展 HTTP 接口。

通用 CRUD/导出仍在 resource.py；这里承载 CSV 预览、导入回执、凭证重开和
指标分母重算等带业务语义的路径，并保留若干常见命名别名以方便对接。
"""
from __future__ import annotations

import json
from email.parser import BytesParser
from email.policy import default
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.services.exposure_log import ExposureLogService
from app.services.metric_compute import MetricComputeService

exposure_router = APIRouter(prefix="/api/exposure_log", tags=["曝光补传与回执"])
metric_router = APIRouter(prefix="/api/metric_compute", tags=["指标分母与修订"])
generic_receipt_router = APIRouter(prefix="/api", tags=["导入回执"])

exposure_service = ExposureLogService()
metric_service = MetricComputeService()

# main.py 按模块读取 ``router``；文件末尾会把三组扩展路由合并到这里。
router = APIRouter()


async def read_payload(request: Request) -> dict[str, Any]:
    """兼容 JSON、text/csv、form-data 和原始 CSV body。"""
    content_type = request.headers.get("content-type", "")
    body = await request.body()

    if "application/json" in content_type:
        try:
            data = json.loads(body.decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {"rows": data}
        except json.JSONDecodeError:
            return {"content": body.decode("utf-8-sig", errors="replace")}

    if "multipart/form-data" in content_type:
        header = f"Content-Type: {content_type}\r\n\r\n".encode()
        message = BytesParser(policy=default).parsebytes(header + body)
        result: dict[str, Any] = {}
        for part in message.walk():
            if part.is_multipart():
                continue
            name = part.get_param("name", header="content-disposition") or "file"
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8-sig", errors="replace")
            if filename:
                result["filename"] = filename
                result.setdefault("content", text)
            else:
                result[name] = text
        return result

    if "application/x-www-form-urlencoded" in content_type:
        from urllib.parse import parse_qs
        parsed = parse_qs(body.decode("utf-8", errors="replace"))
        return {k: v[0] for k, v in parsed.items()}

    text = body.decode("utf-8-sig", errors="replace")
    filename = request.query_params.get("filename", "")
    return {"content": text, "filename": filename}


def _csv_response(csv_text: str, import_no: str) -> Response:
    return Response(
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="receipt-{import_no}.csv"'},
    )


# ---------- 曝光 CSV 预览/提交 ----------

@exposure_router.post("/preview")
@exposure_router.post("/csv/preview")
@exposure_router.post("/imports/preview")
@exposure_router.post("/import/preview")
@exposure_router.post("/import-csv/preview")
@exposure_router.post("/corrections/preview")
async def preview_csv(request: Request) -> dict[str, Any]:
    payload = await read_payload(request)
    return exposure_service.preview_import(
        filename=str(payload.get("filename") or request.query_params.get("filename") or ""),
        content=payload.get("content") if isinstance(payload.get("content"), str) else payload.get("csv"),
        rows=payload.get("rows") if isinstance(payload.get("rows"), list) else None,
    )


@exposure_router.post("/imports")
@exposure_router.post("/import")
@exposure_router.post("/import-csv")
@exposure_router.post("/import-correction")
@exposure_router.post("/corrections")
@exposure_router.post("/correction")
@exposure_router.post("/csv/import")
@exposure_router.post("/upload")
@exposure_router.post("/upload-csv")
async def submit_csv(request: Request) -> dict[str, Any]:
    payload = await read_payload(request)
    content = payload.get("content")
    if not isinstance(content, str):
        content = payload.get("csv") if isinstance(payload.get("csv"), str) else payload.get("text")
    return exposure_service.submit_import(
        filename=str(payload.get("filename") or request.query_params.get("filename") or ""),
        content=content if isinstance(content, str) else None,
        rows=payload.get("rows") if isinstance(payload.get("rows"), list) else None,
        import_no=payload.get("import_no") or payload.get("import_id") or payload.get("receipt_no"),
        idempotency_key=payload.get("idempotency_key") or request.headers.get("idempotency-key"),
    )


@exposure_router.get("/imports")
@exposure_router.get("/import")
@exposure_router.get("/receipts")
async def list_receipts() -> dict[str, Any]:
    return exposure_service.list_receipts()


@exposure_router.get("/imports/{import_no}")
@exposure_router.get("/import/{import_no}")
@exposure_router.get("/receipt/{import_no}")
@exposure_router.get("/receipts/{import_no}")
@exposure_router.get("/import-receipts/{import_no}")
async def get_receipt(import_no: str) -> dict[str, Any]:
    receipt = exposure_service.get_receipt(import_no)
    if receipt is None:
        return Response(status_code=404, content=json.dumps({"ok": False, "message": "导入回执不存在"}),
                        media_type="application/json")
    return receipt


@exposure_router.get("/imports/{import_no}/receipt")
@exposure_router.get("/imports/{import_no}/download")
@exposure_router.get("/import/{import_no}/receipt")
@exposure_router.get("/import/{import_no}/download")
@exposure_router.get("/receipt/{import_no}/download")
@exposure_router.get("/receipts/{import_no}/receipt")
@exposure_router.get("/receipts/{import_no}/download")
@exposure_router.get("/import-receipts/{import_no}/download")
async def download_receipt(import_no: str, format: str = "csv"):
    if format.lower() == "json":
        receipt = exposure_service.get_receipt(import_no)
        if receipt is None:
            return Response(status_code=404, content=json.dumps({"ok": False, "message": "导入回执不存在"}),
                            media_type="application/json")
        return receipt
    csv_text = exposure_service.receipt_csv(import_no)
    if csv_text is None:
        return Response(status_code=404, content=json.dumps({"ok": False, "message": "导入回执不存在"}),
                        media_type="application/json")
    return _csv_response(csv_text, import_no)


@exposure_router.post("/imports/{import_no}/recalculate")
@exposure_router.post("/imports/{import_no}/recalc")
@exposure_router.post("/receipts/{import_no}/recalculate")
async def recalc_receipt(import_no: str) -> dict[str, Any]:
    return exposure_service.recalculate_for_receipt(import_no)


@exposure_router.get("/sources/{source_id}/trace")
@exposure_router.get("/source-traces/{source_id}")
async def source_trace(source_id: str, experiment_code: str | None = None) -> dict[str, Any]:
    return exposure_service.source_trace(source_id, experiment_code)


# 不带模块前缀的常见回执路径别名。
@generic_receipt_router.get("/import_receipts")
@generic_receipt_router.get("/import-receipts")
async def generic_list() -> dict[str, Any]:
    return exposure_service.list_receipts()


@generic_receipt_router.get("/import_receipts/{import_no}")
@generic_receipt_router.get("/import-receipts/{import_no}")
@generic_receipt_router.get("/receipts/{import_no}")
async def generic_get(import_no: str) -> dict[str, Any]:
    receipt = exposure_service.get_receipt(import_no)
    if receipt is None:
        return Response(status_code=404, content=json.dumps({"ok": False, "message": "导入回执不存在"}),
                        media_type="application/json")
    return receipt


@generic_receipt_router.get("/import_receipts/{import_no}/download")
@generic_receipt_router.get("/import-receipts/{import_no}/download")
async def generic_download(import_no: str) -> Response:
    csv_text = exposure_service.receipt_csv(import_no)
    if csv_text is None:
        return Response(status_code=404, content=json.dumps({"ok": False, "message": "导入回执不存在"}),
                        media_type="application/json")
    return _csv_response(csv_text, import_no)


# ---------- 指标作业分母/修订 ----------

@metric_router.post("/{job_id}/recalculate")
@metric_router.post("/{job_id}/recalc")
@metric_router.post("/{job_id}/recompute")
@metric_router.post("/{job_id}/recalculate-window")
@metric_router.post("/{job_id}/recompute-window")
async def recalculate_job(job_id: int, request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = metric_service.recalculate_job(
        job_id,
        experiment_code=payload.get("experiment_code") or payload.get("实验编码"),
        trigger={
            "trigger_type": payload.get("trigger_type", "manual_recalculation"),
            "import_no": payload.get("import_no") or payload.get("receipt_no"),
            "receipt_row_id": payload.get("receipt_row_id"),
            "source_id": payload.get("source_id"),
        },
    )
    if not result.get("ok"):
        return Response(status_code=int(result.get("status_code", 400)),
                        content=json.dumps(result, ensure_ascii=False),
                        media_type="application/json")
    return result


@metric_router.get("/{job_id}/revisions")
async def revisions(job_id: int) -> dict[str, Any]:
    return {"items": metric_service.list_revisions(job_id), "total": len(metric_service.list_revisions(job_id))}


@metric_router.get("/{job_id}/denominator")
async def current_denominator(job_id: int, experiment_code: str | None = None) -> dict[str, Any]:
    job = metric_service.get_entry(job_id)
    if job is None:
        return Response(status_code=404, content=json.dumps({"ok": False, "message": "作业不存在"}),
                        media_type="application/json")
    return metric_service.build_denominator(job, experiment_code)


@metric_router.post("/{job_id}/claim")
async def claim(job_id: int, request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = metric_service.claim_denominator(
        job_id,
        experiment_code=payload.get("experiment_code") or payload.get("实验编码"),
        claimed_by=str(payload.get("claimed_by", "metric_compute")),
    )
    if not result.get("ok"):
        return Response(status_code=int(result.get("status_code", 400)),
                        content=json.dumps(result, ensure_ascii=False),
                        media_type="application/json")
    return result


@metric_router.post("/{job_id}/commit")
async def commit(job_id: int, request: Request) -> dict[str, Any]:
    payload = await request.json()
    result = metric_service.commit_claimed_denominator(
        job_id,
        str(payload.get("claim_token", "")),
        trigger=payload.get("trigger"),
    )
    if not result.get("ok"):
        return Response(status_code=int(result.get("status_code", 409 if result.get("conflict") else 400)),
                        content=json.dumps(result, ensure_ascii=False),
                        media_type="application/json")
    return result

# 所有装饰器注册完成后再合并路由。
router.include_router(exposure_router)
router.include_router(metric_router)
router.include_router(generic_receipt_router)
