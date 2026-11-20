"""数据提取接口：维护提取任务，覆盖启动提取、取消任务、归档结果等动作。"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.schemas import ActionPayload, ActionResult, DeliveryActionPayload, EntryPayload, PageResult, SourceUserUpdate
from app.services.data_extract import DataExtractService
from app.services.data_extract_delivery import DataExtractDeliveryService, DeliveryError

router = APIRouter(prefix="/api/data_extract", tags=["数据提取"])

service = DataExtractService()
delivery_service = DataExtractDeliveryService()

LIST_FIELDS = ["任务编号", "来源数据集", "筛选条件", "字段列表", "排序规则", "分页方式", "输出格式", "提取上限"]
STATUSES = ["草稿", "提取中", "已完成", "已取消"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="草稿、提取中、已完成、已取消"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、任务编号关键字与状态过滤数据提取列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="data_extract.csv"'})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条提取任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"提取任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条提取任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="提取任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条提取任务执行启动提取、取消任务、归档结果；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


# ---------------------------------------------------------------------------
# 分段交付：来源成员、交付版本、断点分段、下载与活动报表
# ---------------------------------------------------------------------------

def _error(error: DeliveryError) -> JSONResponse:
    """业务约束不满足时返回可读说明；已存在的交付版本一并带回，便于页面继续操作。"""
    return JSONResponse(
        status_code=400,
        content={"ok": False, "message": error.message, "entry": error.delivery},
    )


@router.get("/{entry_id}/source-users")
def list_source_users(entry_id: int) -> dict[str, object]:
    """查看来源数据集的当前成员，便于核对消费额更正后的分布。"""
    try:
        delivery_service._task(entry_id)
    except DeliveryError as error:
        raise HTTPException(status_code=404, detail=error.message) from None
    rows = delivery_service.source_users()
    return {"items": rows, "total": len(rows), "dataset": "cohort_users"}


@router.patch("/{entry_id}/source-users/{user_id}")
def update_source_user(entry_id: int, user_id: str, payload: SourceUserUpdate) -> ActionResult:
    """更正来源成员字段（如消费额）；已冻结的交付快照不会随之改变。"""
    try:
        delivery_service._task(entry_id)
        updated = delivery_service.update_source_user(
            user_id, payload.model_dump(exclude_none=True))
    except DeliveryError as error:
        return ActionResult(ok=False, message=error.message)
    return ActionResult(ok=True, message=f"来源成员 {user_id} 已更新", entry=updated)


@router.get("/{entry_id}/deliveries", response_model=dict)
def list_deliveries(entry_id: int) -> dict[str, object]:
    """列出该任务下的全部交付版本，含业务截止时间与确认/未送达分段计数。"""
    try:
        items = delivery_service.list_deliveries(entry_id)
    except DeliveryError as error:
        raise HTTPException(status_code=404, detail=error.message) from None
    return {"items": items, "total": len(items)}


@router.post("/{entry_id}/deliveries", response_model=dict)
def start_delivery(entry_id: int, payload: dict[str, object] | None = None) -> dict[str, object]:
    """启动分段交付：冻结来源快照、生成断点分段与计数。"""
    try:
        delivery = delivery_service.start_delivery(entry_id, payload)
    except DeliveryError as error:
        return _error(error)
    return JSONResponse(status_code=201, content={"ok": True, "message": "分段交付已启动", "entry": delivery})


@router.get("/{entry_id}/deliveries/{delivery_id}", response_model=dict)
def get_delivery(entry_id: int, delivery_id: int) -> dict[str, object]:
    """读取单个交付版本的分段状态与操作留痕。"""
    try:
        return delivery_service.delivery_detail(entry_id, delivery_id)
    except DeliveryError as error:
        raise HTTPException(status_code=404, detail=error.message) from None


@router.post("/{entry_id}/deliveries/{delivery_id}/actions", response_model=dict)
def run_delivery_action(entry_id: int, delivery_id: int, payload: DeliveryActionPayload) -> dict[str, object]:
    """确认分段、中断交付或恢复交付；中断只影响未确认分段。"""
    try:
        delivery, message = delivery_service.run_action(
            entry_id, delivery_id, payload.action, payload.segment_seq)
    except DeliveryError as error:
        return _error(error)
    return JSONResponse(content={"ok": True, "message": message, "entry": delivery})


@router.get("/{entry_id}/deliveries/{delivery_id}/download")
def download_delivery(
    entry_id: int,
    delivery_id: int,
    segment: int | None = Query(default=None, description="只下载指定分段；不传则下载全部已确认分段"),
) -> StreamingResponse:
    """下载交付文件：只包含已确认分段，文件内 user_id 唯一。"""
    try:
        content, filename, headers = delivery_service.build_csv(entry_id, delivery_id, segment)
    except DeliveryError as error:
        return _error(error)
    disposition = f'attachment; filename="{filename}"'
    return StreamingResponse(
        iter([content]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disposition, **headers})


@router.get("/{entry_id}/deliveries/{delivery_id}/report", response_model=dict)
def delivery_report(entry_id: int, delivery_id: int) -> dict[str, object]:
    """活动交付报表：按已确认成员汇总人数与 LTV，并给出截止时间后的来源漂移。"""
    try:
        return delivery_service.report(entry_id, delivery_id)
    except DeliveryError as error:
        raise HTTPException(status_code=404, detail=error.message) from None
