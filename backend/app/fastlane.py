"""Hybrid routing for outage spikes.

Muse Spark Open (open weights) classifies every report in parallel. Routine
reports go through a deterministic pipeline (locate -> cluster -> dispatch).
Low-confidence or unlocatable reports, and the FIRST report of a new dangerous
incident, are escalated to the full Muse Spark agent. Repeat reports about a
danger the agent has already opened a fault for attach in the fast lane and
still get the safety warning. Cheap and fast for the 90%, careful for the 10%.
"""
import time

from . import agent, open_model, tools
from .store import store

ESCALATE_BELOW = 0.6

# Fast-lane customer replies (the agent writes its own for escalated reports)
REPLY = {
    "en": "Thank you. Your report is logged under {fid} and a technician is on the way.",
    "ha": "Mun gode. An yi rajistar rahotonku a {fid}, ma'aikacinmu yana kan hanya.",
}
SAFETY = {
    "en": " Please stay far away from the equipment and do not touch any wire.",
    "ha": " Don Allah ku nisanta, kada ku taba kowace waya.",
}


def reply(lang: str, fid: str, danger: bool) -> str:
    lang = lang if lang in REPLY else "en"
    return REPLY[lang].format(fid=fid) + (SAFETY[lang] if danger else "")


def route_one(report: dict, cls: dict, agent_fn=None) -> dict:
    rid = report["id"]
    store.reports[rid] = {**report, "status": "processing", "classification": cls}
    store.log(rid, "classify", {"model": "muse-spark-open", **cls})
    loc = tools.locate_feeder(area=cls.get("area") or report.get("area_hint", ""),
                              landmark=cls.get("landmark", "") or report["text"])

    ft = cls["fault_type"]
    danger = bool(cls.get("danger"))
    existing = tools.find_open_fault(loc["feeder_id"], ft) if loc["found"] else {"found": False}

    reason = None
    if cls.get("confidence", 0) < ESCALATE_BELOW:
        reason = f"low confidence {cls.get('confidence', 0):.2f}"
    elif not loc["found"]:
        reason = "could not locate feeder"
    elif danger and not existing["found"]:
        reason = "new dangerous incident (fire / smoke / fallen wire)"
    if reason:
        store.log(rid, "escalate", {"reason": reason})
        res = (agent_fn or agent.triage)(report)
        return {"report_id": rid, "lane": "agent", "escalation_reason": reason, **res}

    if existing["found"]:
        fid = existing["fault"]["id"]
        tools.attach_report_to_fault(rid, fid, loc["area"])
        action = "attached"
    else:
        fid = tools.create_fault(rid, loc["feeder_id"], loc["area"], ft,
                                 cls.get("summary_en") or report["text"][:80])["fault"]["id"]
        action = "created"
    dispatch = tools.assign_technician(fid)
    f = store.faults[fid]
    result = {"fault_id": fid, "fault_type": ft, "severity": f["severity"],
              "language": cls.get("language"), "action": action, "danger": danger,
              "reply": reply(cls.get("language"), fid, danger),
              "reasoning": f"Fast lane: {ft} on {loc['feeder_id']} ({loc['area']}), "
                           f"{action} fault {fid}"}
    store.reports[rid].update(status="triaged", result=result)
    store.log(rid, "tool", {"name": "fast_lane", "args": {"feeder": loc["feeder_id"], "type": ft},
                            "result": {"fault_id": fid, "action": action, "dispatch": dispatch}})
    return {"report_id": rid, "lane": "fast", **result}


def process_bulk(reports: list[dict], classify_fn=None, agent_fn=None) -> dict:
    t0 = time.time()
    classes = (classify_fn or open_model.classify_many)(reports)
    t_cls = time.time() - t0
    # Routing is sequential so clustering sees earlier reports in the same burst
    results = [route_one(r, c, agent_fn) for r, c in zip(reports, classes)]
    lanes = [r["lane"] for r in results]
    return {
        "results": results,
        "stats": {
            "reports": len(reports),
            "fast_lane": lanes.count("fast"),
            "escalated": lanes.count("agent"),
            "classify_seconds": round(t_cls, 2),
            "total_seconds": round(time.time() - t0, 2),
        },
    }
