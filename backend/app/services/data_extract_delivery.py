"""分群用户分段交付：来源快照、断点分段、确认/中断/恢复与交付报表。

核心约束：
- 每次交付在启动时冻结来源快照并记录业务截止时间，后续分段、恢复、重新下载都只读这份快照，
  不会把「截止前确认的分段」和「截止后重新查询的分段」拼到同一份结果里。
- 分段只有「已确认」才进入下载文件与报表；「未送达」分段单独标记，不参与统计。
- 下载文件按 user_id 去重，报表人数与文件行数、实际采用的门槛可以互相核对。
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.store import store

DELIVERY_TABLE = "_cohort_deliveries"
SOURCE_DATASET = "cohort_users"
SOURCE_FIELDS = ["user_id", "region", "channel", "ltv", "tag", "last_order_date"]
EDITABLE_FIELDS = ["region", "channel", "ltv", "tag", "last_order_date"]
SEGMENT_STATUSES = ["待确认", "已确认", "未送达"]
DELIVERY_STATUSES = ["交付中", "已中断", "已完成"]
DEFAULT_SEGMENT_SIZE = 2
MAX_SEGMENT_SIZE = 1000

FILTER_FIELD_ALIASES = {
    "user_id": "user_id",
    "用户": "user_id",
    "用户编号": "user_id",
    "region": "region",
    "区域": "region",
    "channel": "channel",
    "渠道": "channel",
    "ltv": "ltv",
    "消费额": "ltv",
    "tag": "tag",
    "标签": "tag",
    "last_order_date": "last_order_date",
    "最近下单": "last_order_date",
}

_CONDITION = re.compile(
    r"^\s*(?P<field>[A-Za-z_一-龥][\w一-龥]*)\s*"
    r"(?P<op>>=|<=|!=|==|=|>|<|\bin\b|\bnot\s+in\b)\s*"
    r"(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_BARE_FIELD = re.compile(r"^\s*[A-Za-z_一-龥][\w一-龥]*\s*(>=|<=|!=|==|=|>|<|\bin\b|\bnot\s+in\b)\s*$", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalise_field(name: str) -> str:
    key = name.strip()
    return FILTER_FIELD_ALIASES.get(key, FILTER_FIELD_ALIASES.get(key.casefold(), key))


def _parse_scalar(text: str) -> Any:
    value = text.strip().strip("'\"")
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def parse_filter(text: str | None) -> list[dict[str, Any]]:
    """把「ltv>=1000 and region=华东」解析成条件列表；无法识别时给出可读错误。"""
    if not text or not text.strip():
        return []
    parts = re.split(r"\s+and\s+", text.strip(), flags=re.IGNORECASE)
    conditions: list[dict[str, Any]] = []
    for part in parts:
        if not part.strip():
            continue
        match = _CONDITION.match(part)
        if match is None:
            if _BARE_FIELD.match(part):
                raise ValueError(f"筛选条件「{part.strip()}」缺少比较值")
            raise ValueError(f"筛选条件「{part.strip()}」无法解析，请使用 字段 运算符 值 的写法")
        if not match.group("value").strip() or re.fullmatch(r"[<>=!]+", match.group("value").strip()):
            raise ValueError(f"筛选条件「{part.strip()}」缺少比较值")
        field = _normalise_field(match.group("field"))
        op = re.sub(r"\s+", " ", match.group("op").strip().lower())
        raw = match.group("value").strip()
        if op in ("in", "not in"):
            if raw.startswith("(") and raw.endswith(")"):
                raw = raw[1:-1]
            values = [_parse_scalar(item) for item in raw.split(",") if item.strip()]
        else:
            values = [_parse_scalar(raw)]
        conditions.append({"field": field, "op": op, "value": values[0] if len(values) == 1 else values})
    return conditions


def filter_text(conditions: list[dict[str, Any]]) -> str:
    """把条件还原成实际采用的口径文本，报表里直接展示这一份。"""
    pieces = []
    for cond in conditions:
        value = cond["value"]
        if isinstance(value, list):
            text = "(" + ",".join(str(item) for item in value) + ")"
        else:
            text = str(value)
        if cond["op"] in ("=", "=="):
            pieces.append(f"{cond['field']}={text}")
        elif cond["op"] == "!=":
            pieces.append(f"{cond['field']}!={text}")
        elif cond["op"] in ("in", "not in"):
            pieces.append(f"{cond['field']} {cond['op']} {text}")
        else:
            pieces.append(f"{cond['field']}{cond['op']}{text}")
    return " and ".join(pieces)


def _match(row: dict[str, Any], conditions: list[dict[str, Any]]) -> bool:
    for cond in conditions:
        field = cond["field"]
        actual = row.get(field)
        op = cond["op"]
        expected = cond["value"]
        if op in ("in", "not in"):
            wanted = expected if isinstance(expected, list) else [expected]
            hit = any(str(actual).casefold() == str(item).casefold() for item in wanted)
            if hit == (op == "not in"):
                return False
            continue
        if field == "ltv" or isinstance(expected, (int, float)):
            left = _as_float(actual)
            right = _as_float(expected)
        else:
            left = str(actual if actual is not None else "").casefold()
            right = str(expected).casefold()
        if op in (">=",):
            ok = left >= right
        elif op in ("<=",):
            ok = left <= right
        elif op == ">":
            ok = left > right
        elif op == "<":
            ok = left < right
        elif op == "!=":
            ok = left != right
        else:
            ok = left == right
        if not ok:
            return False
    return True


def parse_sort(text: str | None) -> list[tuple[str, bool]]:
    """解析「ltv desc」这类排序；返回 [(字段, 是否倒序)]。"""
    if not text or not text.strip():
        return [("user_id", False)]
    orders: list[tuple[str, bool]] = []
    for piece in text.split(","):
        tokens = piece.strip().split()
        if not tokens:
            continue
        field = _normalise_field(tokens[0])
        direction = tokens[1].casefold() if len(tokens) > 1 else "asc"
        orders.append((field, direction in ("desc", "descending")))
    return orders or [("user_id", False)]


def _sort_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, (int, float)):
        return (0, float(value))
    text = str(value if value is not None else "")
    return (1, text.casefold())


def _sort_rows(rows: list[dict[str, Any]], orders: list[tuple[str, bool]]) -> list[dict[str, Any]]:
    sorted_rows = list(rows)
    for field, descending in reversed(orders):
        sorted_rows.sort(key=lambda row, f=field: _sort_key(row.get(f)), reverse=descending)
    return sorted_rows


def _checksum(members: list[dict[str, Any]]) -> str:
    payload = "|".join(f"{member.get('user_id')}:{member.get('ltv')}" for member in members)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _member_view(row: dict[str, Any]) -> dict[str, Any]:
    """快照成员只保留来源字段，避免把可变引用带进交付结果。"""
    return {field: row.get(field) for field in SOURCE_FIELDS if field in row}


def _task_fields(task: dict[str, Any]) -> list[str]:
    raw = str(task.get("字段列表") or "")
    fields = [item.strip() for item in raw.split(",") if item.strip()]
    known = [field for field in fields if field in SOURCE_FIELDS]
    return known or list(SOURCE_FIELDS)


class DeliveryError(Exception):
    """业务规则不允许的动作；路由层把 message 原样返回给调用方。"""

    def __init__(self, message: str, delivery: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.delivery = delivery


class DataExtractDeliveryService:
    # ------------------------------------------------------------------
    # 来源数据
    # ------------------------------------------------------------------
    def source_users(self) -> list[dict[str, Any]]:
        return store.rows(SOURCE_DATASET)

    def update_source_user(self, user_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """运营更正来源成员的消费额；只改当前来源，不动已冻结的交付快照。"""
        if not values:
            raise DeliveryError("没有提交需要更新的字段")
        unknown = [key for key in values if key not in EDITABLE_FIELDS]
        if unknown:
            raise DeliveryError(f"字段{'、'.join(unknown)}不允许在来源数据上修改")
        target = next((row for row in self.source_users() if str(row.get("user_id")) == str(user_id)), None)
        if target is None:
            raise DeliveryError(f"来源成员 {user_id} 不存在")
        updates: dict[str, Any] = {}
        for key, value in values.items():
            if key == "ltv":
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    raise DeliveryError("消费额必须填写数字") from None
                if number < 0:
                    raise DeliveryError("消费额不能为负数")
                updates[key] = round(number, 2)
            else:
                updates[key] = "" if value is None else str(value).strip()
        history = store.rows("_cohort_user_changes")
        history.append({
            "user_id": str(user_id),
            "before": {key: target.get(key) for key in updates},
            "after": dict(updates),
            "changed_at": _now().isoformat(sep=" "),
        })
        target.update(updates)
        return target

    # ------------------------------------------------------------------
    # 交付任务
    # ------------------------------------------------------------------
    def _task(self, entry_id: int) -> dict[str, Any]:
        task = store.find("data_extract", entry_id)
        if task is None:
            raise DeliveryError(f"提取任务 {entry_id} 不存在或已归档")
        return task

    def _deliveries(self) -> list[dict[str, Any]]:
        return store.rows(DELIVERY_TABLE)

    def _find_delivery(self, entry_id: int, delivery_id: int) -> dict[str, Any]:
        for delivery in self._deliveries():
            if int(delivery.get("id", 0)) == delivery_id and int(delivery.get("task_id", 0)) == entry_id:
                return delivery
        raise DeliveryError(f"提取任务 {entry_id} 下不存在交付版本 {delivery_id}")

    def _snapshot(self, conditions: list[dict[str, Any]], orders: list[tuple[str, bool]]) -> tuple[list[dict[str, Any]], int]:
        """按来源当前数据取快照；同一 user_id 只保留排序后的第一条。"""
        rows = [row for row in self.source_users() if _match(row, conditions)]
        rows = _sort_rows(rows, orders)
        seen: set[str] = set()
        snapshot: list[dict[str, Any]] = []
        duplicates = 0
        for row in rows:
            user_id = str(row.get("user_id") or "").strip()
            if not user_id:
                continue
            if user_id in seen:
                duplicates += 1
                continue
            seen.add(user_id)
            snapshot.append(_member_view(row))
        return snapshot, duplicates

    def start_delivery(self, entry_id: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """启动一次交付：冻结来源快照，按分段大小切出可下载的断点分段。"""
        payload = payload or {}
        task = self._task(entry_id)
        dataset = str(task.get("来源数据集") or "").strip()
        if dataset and dataset != SOURCE_DATASET:
            raise DeliveryError(
                f"提取任务 {task.get('任务编号')} 的来源数据集是「{dataset}」，"
                f"分段交付当前只支持分群用户数据集「{SOURCE_DATASET}」")
        filter_value = str(payload.get("筛选条件") or payload.get("filter") or task.get("筛选条件") or "").strip()
        sort_value = str(payload.get("排序规则") or payload.get("sort") or task.get("排序规则") or "").strip()
        try:
            conditions = parse_filter(filter_value)
            orders = parse_sort(sort_value)
        except ValueError as error:
            raise DeliveryError(str(error)) from None

        try:
            segment_size = int(payload.get("分段大小") or payload.get("segment_size") or DEFAULT_SEGMENT_SIZE)
        except (TypeError, ValueError):
            raise DeliveryError("分段大小必须是整数") from None
        if segment_size < 1 or segment_size > MAX_SEGMENT_SIZE:
            raise DeliveryError(f"分段大小必须在 1 到 {MAX_SEGMENT_SIZE} 之间")

        snapshot, duplicates = self._snapshot(conditions, orders)
        if not snapshot:
            raise DeliveryError("当前来源数据没有匹配成员，无法启动分段交付")

        delivery_id = max((int(row.get("id", 0)) for row in self._deliveries()), default=0) + 1
        version_number = sum(1 for row in self._deliveries() if int(row.get("task_id", 0)) == entry_id) + 1
        cutoff = _now()
        segments: list[dict[str, Any]] = []
        for index in range(0, len(snapshot), segment_size):
            chunk = snapshot[index:index + segment_size]
            seq = len(segments) + 1
            segments.append({
                "seq": seq,
                "status": SEGMENT_STATUSES[0],
                "start_offset": index + 1,
                "end_offset": index + len(chunk),
                "breakpoint": f"offset {index + 1}-{index + len(chunk)}",
                "member_count": len(chunk),
                "ltv_total": round(sum(_as_float(member.get("ltv")) for member in chunk), 2),
                "checksum": _checksum(chunk),
                "members": chunk,
                "confirmed_at": None,
                "delivered_at": None,
            })

        delivery = {
            "id": delivery_id,
            "task_id": entry_id,
            "task_code": str(task.get("任务编号") or ""),
            "dataset": str(task.get("来源数据集") or SOURCE_DATASET),
            "version": f"v{version_number}",
            "version_number": version_number,
            "status": DELIVERY_STATUSES[0],
            "business_cutoff_at": cutoff.isoformat(sep=" "),
            "created_at": cutoff.isoformat(sep=" "),
            "updated_at": cutoff.isoformat(sep=" "),
            "filter": filter_text(conditions),
            "requested_filter": filter_value,
            "conditions": conditions,
            "sort": sort_value or "user_id asc",
            "sort_orders": [{"field": field, "descending": descending} for field, descending in orders],
            "segment_size": segment_size,
            "total_members": len(snapshot),
            "segments": segments,
            "snapshot_checksum": _checksum(snapshot),
            "duplicates_removed": duplicates,
            "history": [{
                "at": cutoff.isoformat(sep=" "),
                "action": "启动交付",
                "note": f"冻结快照 {len(snapshot)} 人，切分为 {len(segments)} 段",
            }],
        }
        self._deliveries().append(delivery)
        self._sync_task_status(delivery)
        return self.delivery_detail(entry_id, delivery_id)

    def _sync_task_status(self, delivery: dict[str, Any]) -> None:
        """把交付进度回写到提取任务，列表页能直接看出任务是否已交付完成。"""
        task = store.find("data_extract", delivery["task_id"])
        if task is None:
            return
        task["交付版本"] = delivery["version"]
        task["最近交付状态"] = delivery["status"]
        task["最近交付时间"] = delivery["updated_at"]
        if delivery["status"] == "已完成":
            task["status"] = "已完成"
            task["pending"] = False
        elif delivery["status"] == "已中断":
            task["status"] = "提取中"
            task["pending"] = True

    def list_deliveries(self, entry_id: int) -> list[dict[str, Any]]:
        self._task(entry_id)
        deliveries = [row for row in self._deliveries() if int(row.get("task_id", 0)) == entry_id]
        return [self.delivery_summary(delivery) for delivery in deliveries]

    def delivery_summary(self, delivery: dict[str, Any]) -> dict[str, Any]:
        confirmed = [segment for segment in delivery["segments"] if segment["status"] == "已确认"]
        undelivered = [segment for segment in delivery["segments"] if segment["status"] == "未送达"]
        return {
            "id": delivery["id"],
            "task_id": delivery["task_id"],
            "task_code": delivery["task_code"],
            "version": delivery["version"],
            "version_number": delivery["version_number"],
            "dataset": delivery["dataset"],
            "status": delivery["status"],
            "business_cutoff_at": delivery["business_cutoff_at"],
            "created_at": delivery["created_at"],
            "filter": delivery["filter"],
            "segment_size": delivery["segment_size"],
            "total_members": delivery["total_members"],
            "segment_count": len(delivery["segments"]),
            "confirmed_segment_count": len(confirmed),
            "undelivered_segment_count": len(undelivered),
            "confirmed_member_count": sum(int(segment["member_count"]) for segment in confirmed),
            "undelivered_member_count": sum(int(segment["member_count"]) for segment in undelivered),
            "confirmed_ltv_total": round(sum(_as_float(segment["ltv_total"]) for segment in confirmed), 2),
            "duplicates_removed": delivery["duplicates_removed"],
            "snapshot_checksum": delivery["snapshot_checksum"],
            "updated_at": delivery["updated_at"],
        }

    def delivery_detail(self, entry_id: int, delivery_id: int) -> dict[str, Any]:
        delivery = self._find_delivery(entry_id, delivery_id)
        detail = self.delivery_summary(delivery)
        detail["segments"] = [
            {
                "seq": segment["seq"],
                "status": segment["status"],
                "start_offset": segment["start_offset"],
                "end_offset": segment["end_offset"],
                "breakpoint": segment["breakpoint"],
                "member_count": segment["member_count"],
                "ltv_total": segment["ltv_total"],
                "checksum": segment["checksum"],
                "confirmed_at": segment["confirmed_at"],
                "delivered_at": segment["delivered_at"],
            }
            for segment in delivery["segments"]
        ]
        detail["history"] = list(delivery["history"])
        detail["downloads"] = list(delivery.get("downloads", []))
        detail["complete"] = delivery["status"] == "已完成"
        detail["download_ready"] = any(segment["status"] == "已确认" for segment in delivery["segments"])
        return detail

    # ------------------------------------------------------------------
    # 分段动作
    # ------------------------------------------------------------------
    def run_action(self, entry_id: int, delivery_id: int, action: str, segment_seq: int | None = None) -> tuple[dict[str, Any], str]:
        delivery = self._find_delivery(entry_id, delivery_id)
        action = (action or "").strip()
        aliases = {
            "确认": "确认分段",
            "确认分段": "确认分段",
            "中断": "中断交付",
            "中断交付": "中断交付",
            "暂停交付": "中断交付",
            "暂停": "中断交付",
            "恢复": "恢复交付",
            "恢复交付": "恢复交付",
            "续传": "恢复交付",
        }
        canonical = aliases.get(action)
        if canonical is None:
            raise DeliveryError(f"动作「{action}」不属于分段交付可执行范围", delivery)
        if canonical == "确认分段":
            message = self._confirm_segment(delivery, segment_seq)
        elif canonical == "中断交付":
            message = self._interrupt_delivery(delivery, segment_seq)
        else:
            message = self._resume_delivery(delivery)
        delivery["updated_at"] = _now().isoformat(sep=" ")
        self._sync_task_status(delivery)
        return self.delivery_detail(entry_id, delivery_id), message

    def _segment(self, delivery: dict[str, Any], segment_seq: int | None) -> dict[str, Any]:
        if segment_seq is None:
            raise DeliveryError("请指定要操作的分段序号")
        for segment in delivery["segments"]:
            if int(segment["seq"]) == int(segment_seq):
                return segment
        raise DeliveryError(f"交付版本 {delivery['version']} 不存在第 {segment_seq} 段")

    def _confirm_segment(self, delivery: dict[str, Any], segment_seq: int | None) -> str:
        segment = self._segment(delivery, segment_seq)
        if delivery["status"] == "已中断":
            raise DeliveryError("交付已中断，请先恢复交付再确认未送达分段")
        if segment["status"] == "未送达":
            raise DeliveryError(f"第 {segment['seq']} 段未送达，不能计入已确认范围")
        if segment["status"] == "已确认":
            return f"第 {segment['seq']} 段已确认，无需重复确认"
        now = _now().isoformat(sep=" ")
        segment["status"] = "已确认"
        segment["confirmed_at"] = now
        segment["delivered_at"] = now
        delivery["history"].append({
            "at": now,
            "action": "确认分段",
            "note": f"第 {segment['seq']} 段（{segment['breakpoint']}）确认 {segment['member_count']} 人",
        })
        if all(item["status"] == "已确认" for item in delivery["segments"]):
            delivery["status"] = "已完成"
        elif delivery["status"] != "已中断":
            delivery["status"] = "交付中"
        return f"第 {segment['seq']} 段已确认，交付版本 {delivery['version']} 当前覆盖 {sum(int(s['member_count']) for s in delivery['segments'] if s['status'] == '已确认')} 人"

    def _interrupt_delivery(self, delivery: dict[str, Any], segment_seq: int | None) -> str:
        if delivery["status"] == "已完成":
            raise DeliveryError("交付已完成，不能再中断")
        pending = [segment for segment in delivery["segments"] if segment["status"] == "待确认"]
        targets = [segment for segment in pending if segment_seq is None or int(segment["seq"]) == int(segment_seq)]
        if segment_seq is not None and not targets:
            segment = self._segment(delivery, segment_seq)
            if segment["status"] == "已确认":
                raise DeliveryError(f"第 {segment['seq']} 段已确认送达，不能撤回或中断")
            if segment["status"] == "未送达":
                delivery["status"] = "已中断"
                return f"第 {segment['seq']} 段已经是未送达状态"
        if not targets:
            raise DeliveryError("没有可中断的待确认分段")
        now = _now().isoformat(sep=" ")
        seqs = []
        for segment in targets:
            segment["status"] = "未送达"
            seqs.append(str(segment["seq"]))
        delivery["status"] = "已中断"
        delivery["history"].append({
            "at": now,
            "action": "中断交付",
            "note": f"第 {'、'.join(seqs)} 段标记未送达，已确认分段保持独立口径",
        })
        confirmed_count = sum(int(s["member_count"]) for s in delivery["segments"] if s["status"] == "已确认")
        return f"第 {'、'.join(seqs)} 段已中断，未送达；当前已确认 {confirmed_count} 人"

    def _resume_delivery(self, delivery: dict[str, Any]) -> str:
        if delivery["status"] != "已中断":
            raise DeliveryError("只有已中断的交付才需要恢复")
        now = _now().isoformat(sep=" ")
        resumed = []
        for segment in delivery["segments"]:
            if segment["status"] == "未送达":
                segment["status"] = "待确认"
                resumed.append(str(segment["seq"]))
        if not resumed:
            delivery["status"] = "交付中"
            return "没有未送达分段，交付已恢复"
        delivery["status"] = "交付中"
        delivery["history"].append({
            "at": now,
            "action": "恢复交付",
            "note": f"第 {'、'.join(resumed)} 段按原快照继续，业务截止时间仍为 {delivery['business_cutoff_at']}",
        })
        return f"第 {'、'.join(resumed)} 段已恢复，继续使用截止 {delivery['business_cutoff_at']} 的原快照"

    # ------------------------------------------------------------------
    # 下载与报表
    # ------------------------------------------------------------------
    def _confirmed_members(self, delivery: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        """只取已确认分段，并按 user_id 去重；返回 (成员, 去重条数)。"""
        members: list[dict[str, Any]] = []
        seen: set[str] = set()
        duplicates = 0
        for segment in delivery["segments"]:
            if segment["status"] != "已确认":
                continue
            for member in segment["members"]:
                user_id = str(member.get("user_id") or "").strip()
                if not user_id:
                    continue
                if user_id in seen:
                    duplicates += 1
                    continue
                seen.add(user_id)
                members.append(member)
        return members, duplicates

    def build_csv(self, entry_id: int, delivery_id: int, segment_seq: int | None = None) -> tuple[str, str, dict[str, str]]:
        delivery = self._find_delivery(entry_id, delivery_id)
        task = self._task(entry_id)
        fields = _task_fields(task)
        if segment_seq is not None:
            segment = self._segment(delivery, segment_seq)
            if segment["status"] != "已确认":
                raise DeliveryError(f"第 {segment['seq']} 段状态为「{segment['status']}」，未送达分段不能下载", delivery)
            rows = segment["members"]
            deduped: list[dict[str, Any]] = []
            seen: set[str] = set()
            for member in rows:
                user_id = str(member.get("user_id") or "")
                if user_id and user_id not in seen:
                    seen.add(user_id)
                    deduped.append(member)
            filename = f"{delivery['task_code']}-{delivery['version']}-part{segment['seq']}.csv"
            headers = self._csv_headers(delivery, len(deduped), segment=segment)
        else:
            rows, _ = self._confirmed_members(delivery)
            if not rows:
                raise DeliveryError("当前交付没有已确认分段，暂无可下载的交付文件", delivery)
            deduped = rows
            suffix = "confirmed" if delivery["status"] == "已完成" else "partial"
            filename = f"{delivery['task_code']}-{delivery['version']}-{suffix}.csv"
            headers = self._csv_headers(delivery, len(deduped))
        output = io.StringIO()
        output.write("﻿")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(fields)
        for member in deduped:
            row = []
            for field in fields:
                value = member.get(field)
                text = "" if value is None else str(value)
                row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
            writer.writerow(row)
        deduped_ids = {str(member.get("user_id")) for member in deduped}
        delivery.setdefault("downloads", []).append({
            "at": _now().isoformat(sep=" "),
            "scope": "part" if segment_seq is not None else "confirmed",
            "segment_seq": segment_seq,
            "version": delivery["version"],
            "business_cutoff_at": delivery["business_cutoff_at"],
            "row_count": len(deduped),
            "unique_user_count": len(deduped_ids),
            "checksum": _checksum(deduped),
        })
        return output.getvalue(), filename, headers

    def _csv_headers(self, delivery: dict[str, Any], row_count: int, segment: dict[str, Any] | None = None) -> dict[str, str]:
        status_code = {"交付中": "in_progress", "已中断": "interrupted", "已完成": "completed"}[delivery["status"]]
        # HTTP 头只能是 latin-1，口径里的中文用百分号编码，前端解码后展示。
        headers = {
            "X-Delivery-Version": delivery["version"],
            "X-Delivery-Status": status_code,
            "X-Business-Cutoff": delivery["business_cutoff_at"],
            "X-Member-Count": str(row_count),
            "X-Filter": quote(delivery["filter"], safe=""),
        }
        if segment is not None:
            headers["X-Segment-Seq"] = str(segment["seq"])
            headers["X-Segment-Breakpoint"] = segment["breakpoint"]
        return headers

    def report(self, entry_id: int, delivery_id: int) -> dict[str, Any]:
        """活动交付报表：只按已完成下载所用的已确认成员范围汇总人数与 LTV。"""
        delivery = self._find_delivery(entry_id, delivery_id)
        members, duplicates = self._confirmed_members(delivery)
        confirmed_segments = [segment for segment in delivery["segments"] if segment["status"] == "已确认"]
        undelivered_segments = [segment for segment in delivery["segments"] if segment["status"] == "未送达"]
        total_ltv = round(sum(_as_float(member.get("ltv")) for member in members), 2)
        member_count = len(members)

        # 当前来源只作为对照，绝不并入交付口径。
        live_members, _ = self._snapshot(delivery["conditions"], parse_sort(delivery["sort"]))
        live_by_id = {str(member.get("user_id")): member for member in live_members}
        delivered_by_id = {str(member.get("user_id")): member for member in members}
        added = sorted(set(live_by_id) - set(delivered_by_id))
        removed = sorted(set(delivered_by_id) - set(live_by_id))
        changed = sorted(
            user_id for user_id in set(live_by_id) & set(delivered_by_id)
            if _as_float(live_by_id[user_id].get("ltv")) != _as_float(delivered_by_id[user_id].get("ltv"))
        )
        live_ltv = round(sum(_as_float(member.get("ltv")) for member in live_members), 2)

        return {
            "report_type": "活动交付报表",
            "task_id": delivery["task_id"],
            "task_code": delivery["task_code"],
            "delivery_id": delivery["id"],
            "version": delivery["version"],
            "version_number": delivery["version_number"],
            "delivery_status": delivery["status"],
            "business_cutoff_at": delivery["business_cutoff_at"],
            "dataset": delivery["dataset"],
            "filter": delivery["filter"],
            "threshold": {
                "field": "ltv" if any(cond["field"] == "ltv" for cond in delivery["conditions"]) else None,
                "text": delivery["filter"],
                "conditions": delivery["conditions"],
            },
            "segment_size": delivery["segment_size"],
            "segment_count": len(delivery["segments"]),
            "confirmed_segments": [segment["seq"] for segment in confirmed_segments],
            "undelivered_segments": [segment["seq"] for segment in undelivered_segments],
            "scope": "已确认分段",
            "includes_undelivered": False,
            "complete": delivery["status"] == "已完成",
            "download_ready": bool(confirmed_segments),
            "member_count": member_count,
            "unique_user_count": member_count,
            "duplicate_count": duplicates,
            "total_ltv": total_ltv,
            "avg_ltv": round(total_ltv / member_count, 2) if member_count else 0.0,
            "file_check": {
                "last_confirmed_download": next(
                    (item for item in reversed(delivery.get("downloads", []))
                     if item["scope"] == "confirmed"), None),
                "expected_row_count": member_count,
                "expected_checksum": _checksum(members),
            },
            "segment_breakdown": [
                {
                    "seq": segment["seq"],
                    "status": segment["status"],
                    "breakpoint": segment["breakpoint"],
                    "member_count": segment["member_count"],
                    "ltv_total": segment["ltv_total"],
                    "included_in_report": segment["status"] == "已确认",
                }
                for segment in delivery["segments"]
            ],
            "live_reference": {
                "generated_at": _now().isoformat(sep=" "),
                "matched_count": len(live_members),
                "total_ltv": live_ltv,
                "note": "当前来源仅作对照，不并入本次交付人数与金额",
            },
            "drift": {
                "added_user_ids": added,
                "removed_user_ids": removed,
                "changed_ltv_user_ids": changed,
                "note": "业务截止时间之后发生的来源变化不会改写本次交付结果；需要纳入请启动新交付版本",
            },
        }
