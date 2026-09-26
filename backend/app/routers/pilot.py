"""引航拖轮接口：维护引航作业，覆盖指派作业、开始作业、确认完成等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.pilot import PilotService

router = APIRouter(prefix="/api/pilot", tags=["引航拖轮"])

service = PilotService()

LIST_FIELDS = ["作业编号", "作业类型", "关联船舶", "关联航次", "拖轮名称", "引航员", "计划时间", "实际时间", "作业状态"]
STATUSES = ["待指派", "已指派", "作业中", "已完成"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按作业编号检索"),
    vessel: str | None = Query(default=None, description="按关联船舶检索"),
    status: str | None = Query(default=None, description="待指派、已指派、作业中、已完成"),
    overdue: bool = Query(default=False, description="只看超过计划时间仍待指派的作业"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按作业编号、关联船舶与状态过滤引航拖轮列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, vessel=vessel, status=status, overdue_only=overdue, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/overdue", response_model=PageResult[dict])
def list_overdue(page: int = 1, size: int = 200) -> PageResult[dict]:
    """把超过计划时间还在待指派的引航作业全部挑出来，超期单子不能没了踪影。"""
    items, total = service.list_entries(overdue_only=True, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出引航拖轮清单：返回当前过滤条件下的全量数据。

    该路由必须声明在 /{entry_id} 之前，否则 export 会被当成 entry_id，返回 422。
    """
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "pilot", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条引航作业明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"引航作业 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条引航作业，缺字段或违反排班规则时说明原因而不是静默丢弃。"""
    entry, errors = service.create_entry(payload.values)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="引航作业已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条引航作业执行指派作业、开始作业、确认完成；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
