"""引航拖轮业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "pilot"
VOYAGE_MODULE = "voyage"
# 派工单登记时必须落实到计划时间、拖轮与引航员，缺一项都不予受理。
REQUIRED_FIELDS = ["作业编号", "作业类型", "关联船舶", "关联航次", "计划时间", "拖轮名称", "引航员"]
# 指派时还会补录/核对的派工要素。
ASSIGN_FIELDS = ["拖轮名称", "引航员", "计划时间"]
STATUS_ORDER = ["待指派", "已指派", "作业中", "已完成"]
ACTION_RULES = {"指派作业": "已指派", "开始作业": "作业中", "确认完成": "已完成"}
# 同一艘船同一时段只允许存在一条未完成的引航作业；已完成的历史单不互相冲突。
DEDUP_FIELDS = ["关联船舶", "计划时间"]
# 计划时间可解析的时间格式，按从精确到粗略的顺序尝试。
_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
NEGATIVE_ACTIONS = []


def _clean(values: dict[str, Any], field: str) -> str:
    return str(values.get(field) or "").strip()


def _parse_time(value: Any) -> datetime | None:
    """把计划时间解析成可比较的时刻；解析不了就返回 None，由调用方决定是否放行。"""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


class PilotService:
    # ---- 查询口径 -------------------------------------------------------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        overdue: bool | None = None,
        now: datetime | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, int]:
        """返回当前页记录、过滤后总数与超期待指派总数。

        超期与作业状态都是按当前时刻实时算出来的，不依赖写入时的标记，
        避免计划时间过去了单子却“消失”。
        """
        moment = now or datetime.now()
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("作业编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if overdue:
            rows = [row for row in rows if self._is_overdue(row, moment)]
        overdue_total = sum(
            1 for row in store.rows(MODULE) if self._is_overdue(row, moment)
        )
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [self._with_flags(row, moment) for row in rows[start:start + size]]
        return page_rows, total, overdue_total

    def stats(self, now: datetime | None = None) -> dict[str, int]:
        """看板统计：待指派、超期待指派、作业中、已完成。"""
        moment = now or datetime.now()
        rows = store.rows(MODULE)
        return {
            "待指派": sum(1 for row in rows if row.get("status") == "待指派"),
            "超期待指派": sum(1 for row in rows if self._is_overdue(row, moment)),
            "作业中": sum(1 for row in rows if row.get("status") == "作业中"),
            "已完成": sum(1 for row in rows if row.get("status") == "已完成"),
        }

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return None if entry is None else self._with_flags(entry)

    # ---- 登记与流转 -----------------------------------------------------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _clean(values, field)]
        if missing:
            return None, missing
        if _parse_time(values.get("计划时间")) is None:
            return None, ["计划时间（需为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM 格式）"]
        error = self._validate_references(values)
        if error:
            return None, [error]
        error = self._find_duplicate(values)
        if error:
            return None, [error]
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS:
            entry[field] = _clean(values, field)
        entry.setdefault("实际时间", "")
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._with_flags(entry), []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"引航作业 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于引航拖轮可执行范围"
        target = ACTION_RULES[action]
        current_index = STATUS_ORDER.index(entry["status"]) if entry["status"] in STATUS_ORDER else -1
        target_index = STATUS_ORDER.index(target)

        # 状态只能顺着 待指派→已指派→作业中→已完成 往前走：
        # 已完成不允许改回作业中，也不允许跨级跳状态。
        if current_index < 0:
            return None, f"当前状态「{entry.get('status')}」不在允许的状态序列里"
        if target_index <= current_index:
            if entry["status"] == STATUS_ORDER[-1]:
                return None, "引航作业已完成，不能改回作业中"
            return None, f"引航作业当前为「{entry['status']}」，动作「{action}」不能把状态改回「{target}」"
        if target_index != current_index + 1:
            return None, f"引航作业需先「{STATUS_ORDER[current_index + 1]}」，不能直接{action}"

        updates = values or {}

        # 指派作业是派工单补录拖轮/引航员/计划时间的环节，同样要过必填、对账、查重。
        if action == "指派作业":
            merged = {**entry, **updates}
            missing = [field for field in ASSIGN_FIELDS if not _clean(merged, field)]
            if missing:
                return None, f"指派被拒：缺少必填字段：{'、'.join(missing)}"
            if _parse_time(merged.get("计划时间")) is None:
                return None, "指派被拒：计划时间需为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM 格式"
            reference_error = self._validate_references(merged)
            if reference_error:
                return None, f"指派被拒：{reference_error}"
            duplicate_error = self._find_duplicate(merged, exclude_id=entry_id)
            if duplicate_error:
                return None, f"指派被拒：{duplicate_error}"
            for field in ASSIGN_FIELDS:
                entry[field] = _clean(merged, field)

        if action == "确认完成":
            entry["实际时间"] = _clean(updates, "实际时间") or datetime.now().strftime("%Y-%m-%d %H:%M")

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self._with_flags(entry), f"引航作业已{action}"

    # ---- 规则口径 -------------------------------------------------------

    def _is_overdue(self, row: dict[str, Any], moment: datetime) -> bool:
        """计划时间已过却仍停在待指派，才算超期待催办。"""
        if row.get("status") != "待指派":
            return False
        planned = _parse_time(row.get("计划时间"))
        return planned is not None and planned < moment

    def _with_flags(self, row: dict[str, Any], moment: datetime | None = None) -> dict[str, Any]:
        """列表/明细统一补上实时的超期标记，不改库里的原始记录。"""
        view = dict(row)
        overdue = self._is_overdue(row, moment or datetime.now())
        view["overdue"] = overdue
        view["超期"] = "是" if overdue else ""
        return view

    def _validate_references(self, values: dict[str, Any]) -> str | None:
        """作业单必须挂在一条真实航次上，且航次关联船舶与作业单一致，刷新后才能对得上账。"""
        voyage_no = _clean(values, "关联航次")
        if not voyage_no:
            return None
        voyage = next(
            (row for row in store.rows(VOYAGE_MODULE) if str(row.get("航次编号", "")).strip() == voyage_no),
            None,
        )
        if voyage is None:
            return f"关联航次「{voyage_no}」不存在，请核对航次编号"
        vessel = _clean(values, "关联船舶")
        voyage_vessel = str(voyage.get("关联船舶", "")).strip()
        if vessel and voyage_vessel and vessel != voyage_vessel:
            return f"关联船舶「{vessel}」与航次「{voyage_no}」的船舶「{voyage_vessel}」对不上"
        return None

    def _find_duplicate(self, values: dict[str, Any], exclude_id: int | None = None) -> str | None:
        vessel = _clean(values, "关联船舶")
        planned = _clean(values, "计划时间")
        if not vessel or not planned:
            return None
        for row in store.rows(MODULE):
            if exclude_id is not None and int(row.get("id", 0)) == exclude_id:
                continue
            if row.get("status") == STATUS_ORDER[-1]:
                continue
            if str(row.get("关联船舶", "")).strip() == vessel and str(row.get("计划时间", "")).strip() == planned:
                return f"船舶「{vessel}」在 {planned} 已有未完成的引航作业（作业编号 {row.get('作业编号')}），同一时段只允许一条"
        return None
