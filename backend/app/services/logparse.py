"""采集日志解析业务规则：状态流转、字段校验与筛选口径都收在这里。

生命周期固定按 草稿 → 已启用 → 已暂停 → 已关闭 推进，不允许跳步；
启用时读取采集配置模板的引用结果（当前版本号与参数依据），并把该版本
固定为当前解析批次所用版本——之后配置模板再发布或停用，本批次读取的
仍是自己固定下来的版本。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "logparse"
CONF_MODULE = "crawlconf"
REQUIRED_FIELDS = ["模板编号", "日志类型", "解析规则"]
STATUS_ORDER = ["草稿", "已启用", "已暂停", "已关闭"]
TERMINAL_STATUS = STATUS_ORDER[-1]
# 动作 -> (允许执行的当前状态集合, 推进到的目标状态)
ACTION_RULES = {
    "启用模板": (("草稿", "已暂停"), "已启用"),
    "暂停模板": (("已启用",), "已暂停"),
    "关闭模板": (("已暂停",), "已关闭"),
}
NEGATIVE_ACTIONS = []
PENDING_STATUSES = {"草稿", "已启用"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_batch(current: Any) -> str:
    text = str(current or "").strip()
    if text.upper().startswith("BATCH-") and text[6:].isdigit():
        return f"BATCH-{int(text[6:]) + 1:04d}"
    return "BATCH-0001"


class LogparseService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._with_reference(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self._with_reference(entry)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["引用模板编号"] = str(values.get("引用模板编号") or "").strip()
        entry["引用模板版本"] = ""
        entry["参数依据"] = ""
        entry["解析批次"] = ""
        entry["流转记录"] = []
        entry["status"] = STATUS_ORDER[0]
        entry["模板状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"日志模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集日志解析可执行范围"
        sources, target = ACTION_RULES[action]
        current = str(entry.get("status") or "")
        if current == TERMINAL_STATUS:
            return None, f"日志模板已{TERMINAL_STATUS}，归档终态保持，不再执行「{action}」"
        if current not in sources:
            return None, (
                f"日志模板当前状态为「{current}」，不能执行「{action}」，"
                f"生命周期需按{'→'.join(STATUS_ORDER)}推进"
            )
        basis = str(remark or "").strip() or f"{action}：按生命周期{'→'.join(STATUS_ORDER)}推进"
        if action == "启用模板":
            # 读取模板引用结果，把当前版本固定为本解析批次所用版本
            self._pin_reference(entry, values or {})
        entry["status"] = target
        entry["模板状态"] = target
        entry["pending"] = target in PENDING_STATUSES
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        self._record(entry, action, current, target, basis)
        return entry, f"日志模板已{action}"

    def lifecycle_checklist(self) -> list[dict[str, Any]]:
        """修复模板生命周期复核清单：把模板引用结果数据汇总给解析模块与其他页面。"""
        items: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            reference = self._reference_result(row)
            pinned = str(row.get("引用模板版本") or "")
            current_version = reference.get("模板当前版本")
            items.append({
                "id": row.get("id"),
                "模板编号": row.get("模板编号"),
                "解析状态": row.get("status"),
                "解析批次": row.get("解析批次") or "",
                "引用模板编号": row.get("引用模板编号") or "",
                "引用模板版本": pinned,
                "参数依据": row.get("参数依据") or "",
                "模板当前版本": current_version,
                "模板当前状态": reference.get("模板当前状态"),
                "版本一致": bool(pinned) and pinned == current_version,
            })
        return items

    def _pin_reference(self, entry: dict[str, Any], values: dict[str, Any]) -> None:
        ref_code = str(values.get("引用模板编号") or entry.get("引用模板编号") or "").strip()
        if ref_code:
            entry["引用模板编号"] = ref_code
        template = self._find_template(ref_code)
        if template is not None:
            entry["引用模板版本"] = str(template.get("版本号") or "")
            entry["参数依据"] = str(template.get("配置参数") or "")
        entry["解析批次"] = _next_batch(entry.get("解析批次"))

    def _with_reference(self, entry: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(entry)
        enriched["模板引用结果"] = self._reference_result(entry)
        return enriched

    def _reference_result(self, entry: dict[str, Any]) -> dict[str, Any]:
        template = self._find_template(entry.get("引用模板编号"))
        if template is None:
            return {
                "引用模板编号": str(entry.get("引用模板编号") or ""),
                "模板当前版本": None,
                "模板当前状态": None,
                "参数依据": None,
            }
        return {
            "引用模板编号": template.get("模板编号"),
            "模板当前版本": template.get("版本号"),
            "模板当前状态": template.get("status"),
            "参数依据": template.get("配置参数"),
        }

    def _find_template(self, ref_code: Any) -> dict[str, Any] | None:
        code = str(ref_code or "").strip()
        if not code:
            return None
        for row in store.rows(CONF_MODULE):
            if str(row.get("模板编号") or "") == code:
                return row
        return None

    def _record(
        self,
        entry: dict[str, Any],
        action: str,
        source: str,
        target: str,
        basis: str,
    ) -> None:
        entry.setdefault("流转记录", []).append({
            "动作": action,
            "从状态": source,
            "到状态": target,
            "引用模板版本": entry.get("引用模板版本", ""),
            "解析批次": entry.get("解析批次", ""),
            "依据": basis,
            "时间": _now(),
        })
