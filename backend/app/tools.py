"""Tools the GridSense agent can call. Each returns a JSON-serialisable dict."""
from datetime import datetime, timedelta, timezone

from .config import CLUSTER_WINDOW_MIN
from .store import store

FAULT_TYPES = ["no_supply", "transformer", "line_down", "low_voltage", "meter", "other"]
SEVERITY = {"line_down": "critical", "transformer": "high", "no_supply": "high",
            "low_voltage": "medium", "meter": "low", "other": "medium"}
SKILL_FOR = {"line_down": "lines", "transformer": "transformer", "no_supply": "protection",
             "low_voltage": "transformer", "meter": "meter", "other": "lines"}


def _now():
    return datetime.now(timezone.utc)


def locate_feeder(area: str = "", landmark: str = ""):
    """Map an area name or landmark mentioned by the customer to a feeder."""
    q = (area or "").strip().lower()
    lm = (landmark or "").strip().lower()
    hit = store.areas.get(q)
    if not hit:
        for a in store.areas.values():
            names = [a["name"].lower()] + [x.lower() for x in a["landmarks"]]
            if any(n in q or n in lm or (q and q in n) or (lm and lm in n) for n in names):
                hit = a
                break
    if not hit:
        return {"found": False, "known_areas": sorted(a["name"] for a in store.areas.values())}
    f = store.feeders[hit["feeder_id"]]
    return {"found": True, "area": hit["name"], "feeder_id": f["id"], "feeder_name": f["name"],
            "lat": hit["lat"], "lon": hit["lon"]}


def find_open_fault(feeder_id: str, fault_type: str):
    """Find an open fault on the same feeder, same type, within the cluster window."""
    cutoff = _now() - timedelta(minutes=CLUSTER_WINDOW_MIN)
    for f in store.faults.values():
        if (f["feeder_id"] == feeder_id and f["status"] != "closed"
                and f["fault_type"] == fault_type
                and datetime.fromisoformat(f["updated_at"]) >= cutoff):
            return {"found": True, "fault": f}
    return {"found": False}


def create_fault(report_id: str, feeder_id: str, area: str, fault_type: str, summary: str):
    if fault_type not in FAULT_TYPES:
        fault_type = "other"
    if feeder_id not in store.feeders:
        return {"error": f"unknown feeder {feeder_id}"}
    fid = store.next_fault_id()
    ts = _now().isoformat()
    store.faults[fid] = {
        "id": fid, "feeder_id": feeder_id, "areas": [area], "fault_type": fault_type,
        "severity": SEVERITY[fault_type], "summary": summary, "report_ids": [report_id],
        "status": "open", "technician_id": None, "created_at": ts, "updated_at": ts,
    }
    return {"created": True, "fault": store.faults[fid]}


def attach_report_to_fault(report_id: str, fault_id: str, area: str = ""):
    f = store.faults.get(fault_id)
    if not f:
        return {"error": f"unknown fault {fault_id}"}
    if report_id not in f["report_ids"]:
        f["report_ids"].append(report_id)
    if area and area not in f["areas"]:
        f["areas"].append(area)
    f["updated_at"] = _now().isoformat()
    return {"attached": True, "fault_id": fault_id, "report_count": len(f["report_ids"])}


def assign_technician(fault_id: str):
    """Pick an available technician with the right skill (prefers same feeder area)."""
    f = store.faults.get(fault_id)
    if not f:
        return {"error": f"unknown fault {fault_id}"}
    if f["technician_id"]:
        return {"already_assigned": f["technician_id"]}
    skill = SKILL_FOR[f["fault_type"]]
    feeder_areas = {a["name"] for a in store.areas.values() if a["feeder_id"] == f["feeder_id"]}
    pool = [t for t in store.technicians.values() if t["available"] and skill in t["skills"]]
    pool.sort(key=lambda t: t["base_area"] not in feeder_areas)
    if not pool:
        return {"assigned": False, "reason": f"no available technician with skill '{skill}'"}
    t = pool[0]
    t["available"] = False
    f["technician_id"] = t["id"]
    f["status"] = "dispatched"
    f["updated_at"] = _now().isoformat()
    return {"assigned": True, "technician": {"id": t["id"], "name": t["name"], "skill": skill}}


REGISTRY = {
    "locate_feeder": locate_feeder,
    "find_open_fault": find_open_fault,
    "create_fault": create_fault,
    "attach_report_to_fault": attach_report_to_fault,
    "assign_technician": assign_technician,
}


def _fn(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


S = {"type": "string"}
TOOL_SCHEMAS = [
    _fn("locate_feeder", "Map the customer's area or landmark to a feeder.",
        {"area": S, "landmark": S}, []),
    _fn("find_open_fault", "Check for an existing open fault on this feeder of this type.",
        {"feeder_id": S, "fault_type": {"type": "string", "enum": FAULT_TYPES}},
        ["feeder_id", "fault_type"]),
    _fn("create_fault", "Open a new fault ticket when no matching open fault exists.",
        {"report_id": S, "feeder_id": S, "area": S,
         "fault_type": {"type": "string", "enum": FAULT_TYPES}, "summary": S},
        ["report_id", "feeder_id", "area", "fault_type", "summary"]),
    _fn("attach_report_to_fault", "Attach this report to an existing open fault (duplicate report).",
        {"report_id": S, "fault_id": S, "area": S}, ["report_id", "fault_id"]),
    _fn("assign_technician", "Dispatch a suitable technician to a fault that has none.",
        {"fault_id": S}, ["fault_id"]),
]
