"""引航拖轮业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "pilot"
VOYAGE_MODULE = "voyage"
REQUIRED_FIELDS = ["作业编号", "作业类型", "关联船舶", "拖轮名称", "引航员", "计划时间"]
STATUS_ORDER = ["待指派", "已指派", "作业中", "已完成"]
# 每个动作只允许从指定前置状态发起：已完成的作业不能改回作业中
ACTION_RULES = {"指派作业": ("待指派", "已指派"), "开始作业": ("已指派", "作业中"), "确认完成": ("作业中", "已完成")}
PLAN_TIME_FORMAT = "%Y-%m-%d %H:%M"
PLAN_TIME_FORMATS = [PLAN_TIME_FORMAT, "%Y-%m-%dT%H:%M", "%Y-%m-%d"]


def _parse_plan_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in PLAN_TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _find_voyage_by_ship(ship: str) -> dict[str, Any] | None:
    for row in store.rows(VOYAGE_MODULE):
        if str(row.get("关联船舶") or "").strip() == ship:
            return row
    return None


class PilotService:
    def _enrich(self, row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        """补齐展示口径：关联航次、是否超期，并让作业状态列与内部状态对齐。"""
        now = now or datetime.now()
        ship = str(row.get("关联船舶") or "").strip()
        voyage = _find_voyage_by_ship(ship)
        row["关联航次"] = voyage.get("航次编号") if voyage else None
        plan = _parse_plan_time(row.get("计划时间"))
        overdue = bool(plan and row.get("status") == STATUS_ORDER[0] and plan < now)
        row["overdue"] = overdue
        if overdue:
            row["abnormal"] = True
        row["作业状态"] = str(row.get("status") or STATUS_ORDER[0])
        return row

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        vessel: str | None = None,
        status: str | None = None,
        overdue_only: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        now = datetime.now()
        rows = [self._enrich(row, now=now) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("作业编号", ""))]
        if vessel:
            rows = [row for row in rows if vessel in str(row.get("关联船舶", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if overdue_only:
            rows = [row for row in rows if row.get("overdue")]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._enrich(entry) if entry else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{'、'.join(missing)}"]

        errors: list[str] = []
        ship = str(values.get("关联船舶") or "").strip()
        plan = _parse_plan_time(values.get("计划时间"))
        if plan is None:
            errors.append("计划时间格式不正确，应为 年-月-日 或 年-月-日 时:分")
        if _find_voyage_by_ship(ship) is None:
            errors.append(f"关联船舶「{ship}」在航次管理中没有对应航次，请核对后再提交")
        if plan is not None:
            for row in store.rows(MODULE):
                if str(row.get("关联船舶") or "").strip() != ship:
                    continue
                if row.get("status") == STATUS_ORDER[-1]:
                    continue
                if _parse_plan_time(row.get("计划时间")) == plan:
                    errors.append(
                        f"同一艘船在 {plan.strftime(PLAN_TIME_FORMAT)} 已有引航作业（{row.get('作业编号')}），重复指派只保留一条"
                    )
                    break
        if errors:
            return None, errors

        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS:
            entry[field] = str(values.get(field) or "").strip()
        entry["计划时间"] = plan.strftime(PLAN_TIME_FORMAT) if plan else ""
        entry["实际时间"] = None
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        self._enrich(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"引航作业 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于引航拖轮可执行范围"
        current = str(entry.get("status") or STATUS_ORDER[0])
        if current == STATUS_ORDER[-1]:
            return None, "引航作业已完成，不能改回作业中"
        source, target = ACTION_RULES[action]
        if current != source:
            return None, f"当前状态为「{current}」，不能执行{action}，请先完成前置环节"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        if action == "确认完成" and not str(entry.get("实际时间") or "").strip():
            entry["实际时间"] = datetime.now().strftime(PLAN_TIME_FORMAT)
        self._enrich(entry)
        return entry, f"引航作业已{action}"
