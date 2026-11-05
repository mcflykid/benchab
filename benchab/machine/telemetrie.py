"""Projections locales de relevés explicitement fournis, sans aucun transport réseau.

Adapté de RUVYKO integrations/anycubic-telemetry/telemetry.py (30/10/2026).
Provenance, limites de schéma et utilisation : TELEMETRIE.md.

Sources: anycubic-cloud-api 0.4.36 data_models/{printer,project,printer_properties}.py.
Unknown fields never become zero, a physical assumption, or a copied raw payload.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

MAX_HISTORY = 20
SOURCE = "anycubic-cloud-api0.4.36"


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 160) -> str | None:
    return (
        value
        if isinstance(value, str)
        and 0 < len(value) <= limit
        and (not any((ord(c) < 32 or ord(c) == 127 for c in value)))
        else None
    )


def _number(value: Any, minimum: float = 0, maximum: float = 1000000000000.0) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        if len(value) > 32 or not re.fullmatch("[+]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)", value):
            return None
        value = float(value)
    if not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) and minimum <= value <= maximum else None
    except (OverflowError, ValueError):
        return None


def _integer(value: Any, maximum: int = 2**31 - 1) -> int | None:
    number = _number(value, maximum=maximum)
    return int(number) if number is not None and number == int(number) else None


def _positive_id(value: Any) -> int | None:
    value = _integer(value)
    return value if value is not None and value > 0 else None


def _json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and len(value) <= 65536:

        def pairs(items):
            result = {}
            for key, item in items:
                if key in result:
                    raise ValueError("duplicate")
                result[key] = item
            return result

        try:
            return _dict(
                json.loads(
                    value,
                    object_pairs_hook=pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
                )
            )
        except (ValueError, RecursionError):
            pass
    return {}


def _duration_minutes(value: Any) -> float | int | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    match = re.fullmatch("(\\d{1,8})hour(\\d{1,2})min", value)
    if match:
        hours, minutes = map(int, match.groups())
        return hours * 60 + minutes if minutes < 60 else None
    return _number(value)


def _color(value: Any) -> str | None:
    if (
        not isinstance(value, list)
        or len(value) not in (3, 4)
        or any((type(v) is not int or not 0 <= v <= 255 for v in value))
    ):
        return None
    return "#" + "".join((f"{v:02X}" for v in value[:3]))


def project_statistics(detail: Any) -> dict:
    base = _dict(_dict(detail).get("base"))
    minutes = _duration_minutes(base.get("print_totaltime"))
    count = _integer(base.get("print_count"))
    material = base.get("material_used")
    match = (
        re.fullmatch("\\s*([0-9]+(?:\\.[0-9]+)?)\\s*kg\\s*", material, re.IGNORECASE)
        if isinstance(material, str) and len(material) <= 64
        else None
    )
    kg = _number(match.group(1)) if match else None
    return {
        "quality": "reported" if any((v is not None for v in (minutes, count, kg))) else "not_reported",
        "total_duration_seconds": minutes * 60 if minutes is not None else None,
        "print_count": count,
        "total_filament_kg": kg,
        "source": SOURCE + ":printer.py/base",
    }


def project_job(raw: Any, *, source: str = "cloud_project") -> dict:
    record = _dict(raw)
    message = _json_dict(record.get("device_message"))
    settings = _json_dict(record.get("settings"))
    known = source == "cloud_project"

    def pick(key):
        return record[key] if key in record and record[key] is not None else message.get(key)

    elapsed = _number(pick("print_time")) if known else None
    remaining = _number(pick("remain_time")) if known else None
    status = _integer(record.get("print_status")) if known else None
    pause = _integer(record.get("pause")) if known else None
    state = {
        1: "printing",
        2: "complete",
        3: "cancelled",
        4: "downloading",
        5: "checking",
        6: "preheating",
        7: "slicing",
        9: "levelling",
    }.get(status, "unknown")
    if status in (1, 4, 5, 6) and pause is not None and (pause > 0):
        state = "paused"
    name = next(
        (s for key in ("gcode_name", "filename", "name") if (s := _text(record.get(key))) is not None),
        _text(message.get("filename")),
    )
    return {
        "quality": "reported" if record and known else "not_reported",
        "name": name if known else None,
        "progress_percent": _number(pick("progress"), maximum=100) if known else None,
        "elapsed_seconds": elapsed * 60 if elapsed is not None else None,
        "remaining_seconds": remaining * 60 if remaining is not None else None,
        "print_status_code": _integer(record.get("print_status")) if known else None,
        "state": state,
        "current_layer": _integer(settings.get("curr_layer")) if known else None,
        "total_layers": _integer(settings.get("total_layers")) if known else None,
        "source": SOURCE + ":project.py" if known else "schema_not_qualified",
    }


def project_ace(raw_boxes: Any) -> list[dict]:
    boxes = raw_boxes if isinstance(raw_boxes, list) else [raw_boxes] if isinstance(raw_boxes, dict) else []
    counts = {}
    for box in boxes[:16]:
        if isinstance(box, dict) and type(box.get("id")) is int:
            counts[box["id"]] = counts.get(box["id"], 0) + 1
    result = []
    emitted = set()
    for box in boxes[:2]:
        if not isinstance(box, dict) or type(box.get("id")) is not int or (not 0 <= box["id"] <= 2**31 - 1):
            continue
        if box["id"] in emitted:
            continue
        emitted.add(box["id"])
        conflicting = counts[box["id"]] != 1 or len(boxes) > 16
        if conflicting:
            box = {"id": box["id"]}
        slots = [
            {
                "slot": i + 1,
                "presence": "unknown",
                "information_source": "unknown",
                "sku": None,
                "material": None,
                "color": None,
                "colors": [],
                "remaining_percent": None,
                "loaded": None,
                "rfid_uid": None,
            }
            for i in range(4)
        ]
        seen = set()
        reports = box.get("slots")
        slots_truncated = isinstance(reports, list) and len(reports) > 16
        for report in reports if isinstance(reports, list) and (not slots_truncated) else []:
            if not isinstance(report, dict) or type(report.get("index")) is not int or (not 0 <= report["index"] < 4):
                continue
            index = report["index"]
            if index in seen:
                slots[index] = {
                    "slot": index + 1,
                    "presence": "unknown",
                    "information_source": "conflicting_reports",
                    "sku": None,
                    "material": None,
                    "color": None,
                    "colors": [],
                    "remaining_percent": None,
                    "loaded": None,
                    "rfid_uid": None,
                }
                continue
            seen.add(index)
            edit = report.get("edit_status")
            edit = edit if type(edit) is int else None
            presence = {0: "present", 1: "present", 2: "absent"}.get(edit, "unknown")
            origin = {0: "tag_read_reported", 1: "manual_entry_reported", 2: "empty_reported"}.get(edit, "unknown")
            present = presence == "present"
            colors = report.get("color_group")
            status = report.get("status")
            slots[index] = {
                "slot": index + 1,
                "presence": presence,
                "information_source": origin,
                "sku": _text(report.get("sku"), 100) if present else None,
                "material": _text(report.get("type"), 80) if present else None,
                "color": _color(report.get("color")) if present else None,
                "colors": [c for raw in colors[:16] if (c := _color(raw)) is not None]
                if present and isinstance(colors, list)
                else [],
                "remaining_percent": _number(report.get("consumables_percent"), maximum=100) if present else None,
                "loaded": status == 5 if present and type(status) is int and (status in (4, 5)) else None,
                "rfid_uid": None,
            }
        drying = _dict(box.get("drying_status"))
        result.append(
            {
                "box_index": box["id"],
                "model_id": _integer(box.get("model_id")),
                "temperature_c": _number(box.get("temp"), minimum=-20, maximum=120),
                "humidity_percent": _number(box.get("humidity"), maximum=100),
                "drying_status_code": _integer(drying.get("status")),
                "slots": slots,
                "slot_reports_truncated": slots_truncated,
                "quality": "conflicting_reports"
                if conflicting
                else "truncated_reports"
                if slots_truncated
                else "cloud_report",
                "physical_validation": False,
            }
        )
    return result


def project_records(response: Any, printer_id: Any) -> dict:
    wanted = _positive_id(printer_id)
    data = _dict(response).get("data")
    if wanted is None:
        return {"quality": "printer_scope_required", "items": [], "truncated": False}
    if not isinstance(data, list):
        return {"quality": "not_reported", "items": [], "truncated": False}
    items = []
    truncated = len(data) > 100
    for raw in data[:100]:
        if not isinstance(raw, dict) or _integer(raw.get("printer_id")) != wanted:
            continue
        if len(items) >= MAX_HISTORY:
            truncated = True
            break
        items.append(project_job(raw))
    return {
        "quality": "project_records",
        "items": items,
        "truncated": truncated,
        "semantic_scope": "project_records_not_verified_completed_print_history",
        "scope": "selected_printer",
        "source": SOURCE + ":functions.py/list_all_projects",
    }


def project_history(response: Any, printer_id: Any) -> dict:
    """Exact SDK /v2/project/printHistory schema, not getProjects records."""
    wanted = _positive_id(printer_id)
    data = _dict(response).get("data")
    if wanted is None:
        return {"quality": "printer_scope_required", "items": [], "truncated": False}
    if not isinstance(data, list):
        return {"quality": "not_reported", "items": [], "truncated": False}
    items = []
    truncated = len(data) > 100
    for row in data[:100]:
        row = _dict(row)
        if _integer(row.get("printerID")) != wanted:
            continue
        if len(items) >= MAX_HISTORY:
            truncated = True
            break
        code = _integer(row.get("print_status"))
        items.append(
            {
                "name": _text(row.get("gcode_name")),
                "print_status_code": code,
                "state": {
                    1: "printing",
                    2: "complete",
                    3: "cancelled",
                    4: "downloading",
                    5: "checking",
                    6: "preheating",
                    7: "slicing",
                    9: "levelling",
                }.get(code, "unknown"),
            }
        )
    return {
        "quality": "reported",
        "scope": "selected_printer",
        "items": items,
        "truncated": truncated,
        "source": "sdk2.0.0.3:/v2/project/printHistory",
        "timestamp_units": "not_qualified",
    }


def project_notifications(response: Any, printer_id: Any) -> dict:
    """Native MessageAggregateInfo with aggregate_mode=0, update_status=0.

    No message is marked read. A missing printer association stays explicit.
    """
    wanted = _positive_id(printer_id)
    data = _dict(response).get("data")
    if wanted is None:
        return {"quality": "printer_scope_required", "items": [], "truncated": False}
    if not isinstance(data, list):
        return {"quality": "not_reported", "items": [], "truncated": False}
    items = []
    truncated = len(data) > 100
    for row in data[:100]:
        row = _dict(row)
        if _integer(row.get("aggregate_mode")) != 0:
            continue
        printers = row.get("printer_list")
        if printers is not None and (not isinstance(printers, list)):
            continue
        if isinstance(printers, list) and len(printers) > 100:
            continue
        ids = [_integer(_dict(p).get("printer_id")) for p in printers or []]
        if wanted in ids:
            scope = "selected_printer"
        elif not ids:
            scope = "account_unattributed"
        else:
            continue
        if len(items) >= MAX_HISTORY:
            truncated = True
            break
        status = _integer(row.get("status"))
        items.append(
            {
                "scope": scope,
                "name": _text(row.get("task_name")),
                "description": _text(row.get("reason_desc")),
                "alarm_code": _integer(row.get("alarm_code")),
                "read": bool(status) if status in (0, 1) else None,
            }
        )
    return {
        "quality": "reported",
        "scope": "per_item_explicit",
        "items": items,
        "truncated": truncated,
        "source": "sdk2.0.0.3:/farm/message/list;aggregate_mode=0",
        "marks_read": False,
        "timestamp_units": "not_qualified",
    }


def select_active_project(response: Any, printer_id: Any) -> dict | None:
    """Internal-only record selected from the real getProjects response.

    Never treat a printer record as a job. Refuse ambiguous multiple active jobs.
    The returned original record must remain internal to the bridge.
    """
    wanted = _positive_id(printer_id)
    rows = _dict(response).get("data")
    if wanted is None or not isinstance(rows, list) or len(rows) > 100:
        return None
    matches = [
        row
        for row in rows[:100]
        if isinstance(row, dict)
        and _integer(row.get("printer_id")) == wanted
        and (_positive_id(row.get("id")) is not None)
        and (_integer(row.get("print_status")) in (1, 4, 5, 6, 7, 9))
    ]
    return matches[0] if len(matches) == 1 else None


def capabilities_model(machine_type: Any, ace_model_id: Any = None) -> dict:
    """Documented model capabilities, distinct from live state/permissions.

    Use only model IDs actually reported by the selected device. Limits are
    constructor-software constraints, not a physical verification of equipment.
    A current reported fan limit must take precedence over this model default.
    """
    s1 = _integer(machine_type) == 20025
    ace = _integer(ace_model_id)
    return {
        "model_qualified": s1,
        "light_type": 2 if s1 else None,
        "box_fan_scale_default": "percent" if s1 else None,
        "box_fan_range_default": [0, 100] if s1 else None,
        "ace_model": {40001: "ACE PRO", 40002: "ACE PRO 2"}.get(ace),
        "ace_max_drying_temperature_c": {40001: 55, 40002: 65}.get(ace),
        "source": "shipped-SPA2.0.0.3:model20025;ACE40001/40002;ControlModuleV3;client0.4.36:light_type",
        "physical_validation": False,
        "commands_enabled": False,
        "live_state_required": ["light_status", "homed", "position_xyz", "job_state", "ace_presence"],
        "live_fan_limit_overrides_model_default": True,
    }


def capabilities_for_model(model: Any, detail: Any) -> dict:
    """Static S1 capability fallback, with explicit received limits first.

    Does not report homing, current lamp state, ACE presence or hardware safety.
    ``model`` accepts only the exact known numeric ID or exact constructor name.
    A contradictory ID in detail prevents the name-based fallback.
    """
    detail = _dict(detail)
    machine_type = detail.get("machine_type")
    if machine_type is not None:
        s1 = _integer(machine_type) == 20025
    else:
        s1 = _integer(model) == 20025 or model == "Anycubic Kobra S1"
    fan_range = [0, 100] if s1 else None
    fan_source = "shipped-SPA2.0.0.3:app.js:5993-6008:machine_type20025" if s1 else "model_not_qualified"
    received = detail.get("box_fan_level_limit")
    if s1 and isinstance(received, list) and (len(received) == 2):
        lower, upper = (_integer(v, maximum=100) for v in received)
        if lower is not None and upper is not None and (0 <= lower < upper):
            fan_range = [lower, upper]
            fan_source = "received:detail.box_fan_level_limit"
    if s1 and received is not None and (fan_source != "received:detail.box_fan_level_limit"):
        fan_range = None
        fan_source = "received_range_not_qualified"
    by_box = {}
    seen = set()
    boxes = detail.get("multi_color_box")
    if isinstance(boxes, dict):
        boxes = [boxes]
    boxes_truncated = isinstance(boxes, list) and len(boxes) > 16
    for box in boxes if s1 and isinstance(boxes, list) and (not boxes_truncated) else []:
        box = _dict(box)
        box_id = _integer(box.get("id"))
        if box_id is None:
            continue
        key = str(box_id)
        if box_id in seen:
            by_box.pop(key, None)
            continue
        seen.add(box_id)
        ace = _integer(box.get("model_id"))
        if ace not in (40001, 40002):
            continue
        by_box[key] = {
            "model_id": ace,
            "name": "ACE 2 Pro" if ace == 40002 else "ACE Pro",
            "max_drying_temperature_c": 65 if ace == 40002 else 55,
            "source": "received:model_id;shipped-SPA2.0.0.3:WorkbenchV3:35595-35602;23794-23795",
        }
    return {
        "model_qualified": s1,
        "light_type": 2 if s1 else None,
        "light_type_source": "client0.4.36:printer.py:2297;SPA:WorkbenchV3:35208" if s1 else "model_not_qualified",
        "box_fan_level_limit": fan_range,
        "box_fan_limit_source": fan_source,
        "box_fan_scale": ("percent" if fan_range[1] > 3 else "discrete") if fan_range else None,
        "ace_models_by_box": by_box,
        "ace_inventory_truncated": boxes_truncated,
        "homed": None,
        "position_xyz": None,
        "physical_validation": False,
        "commands_enabled": False,
    }


def control_context(detail: Any, record: Any = None) -> dict:
    """Internal routing context, never a permission to operate hardware.

    REST details do not qualify a light type, homing or XYZ. The vendor obtains
    light/coordinates from separate MQTT replies, not machine_data dimensions.
    IDs must stay internal to the bridge and must not be copied into UI output.
    """
    detail = _dict(detail)
    record = _dict(record)
    printer_id = _positive_id(detail.get("id"))
    scoped = printer_id is not None and _integer(record.get("printer_id")) == printer_id
    status = _integer(record.get("print_status")) if scoped else None
    pause = _integer(record.get("pause")) if scoped else None
    state = {
        1: "printing",
        2: "complete",
        3: "cancelled",
        4: "downloading",
        5: "checking",
        6: "preheating",
        7: "slicing",
        9: "levelling",
    }.get(status, "unknown")
    if status in (1, 4, 5, 6) and pause is not None and (pause > 0):
        state = "paused"
    available = _integer(detail.get("is_printing"))
    if not record and available == 1:
        state = "idle"
    project_id = _positive_id(record.get("id")) if scoped and status in (1, 4, 5, 6, 7, 9) else None
    return {
        "print_state": state,
        "project_id": project_id,
        "light_type": None,
        "homed": None,
        "position_xyz": None,
        "source": SOURCE + ":printer.py/is_available;project.py/print_status;const/enums.py",
        "unqualified": ["light_type", "homed", "position_xyz"],
    }


def project_telemetry(
    detail: Any,
    *,
    job: Any = None,
    history: Any = None,
    notifications: Any = None,
    printer_id: Any = None,
    observed_at: Any = None,
    now: datetime | None = None,
) -> dict:
    record = _dict(detail)
    scoped_job = _dict(job)
    wanted = _positive_id(printer_id)
    detail_id = _integer(record.get("id"))
    if wanted is None or wanted <= 0:
        record = {}
        detail_scope = "printer_scope_required"
    elif "id" in record and detail_id != wanted:
        record = {}
        detail_scope = "conflicting_printer"
    else:
        detail_scope = "reported_printer" if detail_id == wanted else "caller_scope_unverified"
    if wanted is None or _integer(scoped_job.get("printer_id")) != wanted:
        scoped_job = {}
    boxes = record.get("multi_color_box")
    boxes_list = boxes if isinstance(boxes, list) else [boxes] if isinstance(boxes, dict) else []
    return {
        "schema": "atelier.telemetrie.v1",
        "statistics": project_statistics(record),
        "job": project_job(scoped_job),
        "ace_boxes": project_ace(record.get("multi_color_box")),
        "history": project_history(history, printer_id),
        "notifications": project_notifications(notifications, printer_id),
        "detail_scope": detail_scope,
        "freshness": freshness(observed_at, now=now),
        "ace_inventory_truncated": len(boxes_list) > 2,
        "commands_enabled": False,
        "physical_validation": False,
    }


def freshness(observed_at: Any, *, now: datetime | None = None) -> dict:
    """Timestamp supplied with the file, not verified reception from a printer.

    The 30-second limit is a display policy, never an authorization to act.
    Missing, naive, future or invalid timestamps remain explicitly unknown.
    """
    result = {
        "quality": "not_reported",
        "age_seconds": None,
        "fresh": None,
        "maximum_age_seconds": 30,
        "source": "provided_timestamp_not_authenticated",
    }
    if observed_at is None:
        return result
    try:
        if type(observed_at) is not str or len(observed_at) > 64:
            raise ValueError("timestamp")
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        current = datetime.now(timezone.utc) if now is None else now
        if not isinstance(current, datetime) or observed.tzinfo is None or current.tzinfo is None:
            raise ValueError("timezone")
        age = (current - observed).total_seconds()
        if not math.isfinite(age) or age < 0:
            raise ValueError("future")
    except (ValueError, TypeError, OverflowError):
        result["quality"] = "invalid_timestamp"
        return result
    return {**result, "quality": "fresh" if age <= 30 else "stale", "age_seconds": age, "fresh": age <= 30}


ROUTE_CATALOG = (
    {
        "method": "GET",
        "path": "/v2/printer/info",
        "purpose": "statistics_and_ace",
        "query": ("id",),
        "source": "api_endpoints.py/printer_info; functions.py/printer_info_for_id",
    },
    {
        "method": "GET",
        "path": "/work/project/getProjects",
        "purpose": "project_records_not_verified_print_history",
        "query": ("page", "limit", "print_status"),
        "source": "api_endpoints.py/project_get_projects; functions.py/list_all_projects",
    },
    {
        "method": "GET",
        "path": "/v2/project/info",
        "purpose": "job_details",
        "query": ("id",),
        "source": "api_endpoints.py/project_info; functions.py/project_info_for_id",
    },
    {
        "method": "GET",
        "path": "/v2/project/printHistory",
        "purpose": "printing_history",
        "query": ("printer_id", "page", "limit"),
        "source": "SDK2.0.0.3 CloudClient::printHistory 0x9d760",
    },
    {
        "method": "POST",
        "path": "/farm/message/list",
        "purpose": "personal_printing_notifications",
        "body": {"page": 1, "limit": 20, "status": -1, "aggregate_mode": 0, "update_status": 0, "keyword": ""},
        "source": "SDK2.0.0.3 0x773bc/0x79740; ACMessageCenterPopup 0x101b01770",
        "eligible_for_personal_bridge": True,
    },
)


def read_snapshot(path):
    """Read one explicit local JSON file, bounded and without symbolic links."""
    import os
    import stat
    from pathlib import Path

    path = Path(path).absolute()
    if any((p.is_symlink() for p in (path, *path.parents))):
        raise ValueError("symbolic_path")
    limit = 1024 * 1024
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("input_budget")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())

    def identity(s):
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

    if len(raw) > limit or identity(before) != identity(after) or identity(before) != identity(path.lstat()):
        raise ValueError("unstable_input")

    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result

    def finite(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("nonfinite")
        return value

    def reject_constant(_):
        raise ValueError("nonfinite")

    data = json.loads(raw, object_pairs_hook=unique, parse_float=finite, parse_constant=reject_constant)
    allowed = {"detail", "printer_id", "model", "job", "history", "notifications", "observed_at"}
    if type(data) is not dict or set(data) - allowed or type(data.get("detail")) is not dict:
        raise ValueError("snapshot_shape")
    printer = _integer(data.get("printer_id"))
    if printer is None or printer <= 0:
        raise ValueError("printer_scope_required")
    return data


def main(argv=None):
    """CLI locale : un relevé fourni explicitement, rapport JSON sur stdout."""
    import argparse

    parser = argparse.ArgumentParser(description="Analyser un relevé JSON fourni, sans réseau ni commande machine.")
    parser.add_argument("fichier", help="Fichier JSON local, 1 Mio maximum ; TELEMETRIE.md décrit son schéma.")
    args = parser.parse_args(argv)
    try:
        data = read_snapshot(args.fichier)
        result = project_telemetry(
            data["detail"],
            job=data.get("job"),
            history=data.get("history"),
            notifications=data.get("notifications"),
            printer_id=data["printer_id"],
            observed_at=data.get("observed_at"),
        )
        detail = {} if result["detail_scope"] == "conflicting_printer" else data["detail"]
        model = None if result["detail_scope"] == "conflicting_printer" else data.get("model")
        result["declared_capabilities"] = capabilities_for_model(model, detail)
        output = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(output.encode()) > 32768:
            raise ValueError("output_budget")
        print(output)
        return 0
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        print(
            json.dumps(
                {
                    "error": "Relevé refusé : format, taille, identité ou chemin non conforme.",
                    "commands_enabled": False,
                    "physical_validation": False,
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
