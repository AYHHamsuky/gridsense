"""GridSense API. Run: uvicorn backend.app.main:app --reload"""
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import agent, fastlane, meta_client, open_model, vision
from .store import store

app = FastAPI(title="GridSense", version="0.1.0",
              description="Hausa/English outage & fault agent for Nigerian distribution companies")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ReportIn(BaseModel):
    text: str
    customer_name: str = ""
    phone: str = ""
    area_hint: str = ""
    channel: str = "text"


def _new_report(**kw):
    return {"id": f"RPT-{uuid.uuid4().hex[:8].upper()}",
            "created_at": datetime.now(timezone.utc).isoformat(), **kw}


@app.get("/health")
def health():
    return {"ok": True, "feeders": len(store.feeders), "technicians": len(store.technicians),
            "fast_lane": open_model.enabled()}


@app.post("/reports")
def create_report(body: ReportIn):
    report = _new_report(**body.model_dump())
    try:
        result = agent.triage(report)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {"report_id": report["id"], "result": result}


@app.post("/reports/voice")
async def create_voice_report(audio: UploadFile = File(...), area_hint: str = Form(""),
                              phone: str = Form("")):
    data = await audio.read()
    try:
        asr = meta_client.transcribe(data, audio.filename or "audio.wav")
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    text = (asr.get("transcript") or "").strip()
    if not text:
        raise HTTPException(422, "No speech detected in the voice note")
    report = _new_report(text=text, area_hint=area_hint, phone=phone, channel="voice")
    return {"report_id": report["id"], "transcript": text, "result": agent.triage(report)}


class BulkIn(BaseModel):
    reports: list[ReportIn]


@app.post("/reports/bulk")
def bulk_reports(body: BulkIn):
    """Outage spike: Muse Spark Open classifies in parallel; risky reports escalate to the agent."""
    reports = [_new_report(**r.model_dump()) for r in body.reports]
    try:
        return fastlane.process_bulk(reports)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.get("/faults")
def list_faults():
    return sorted(store.faults.values(), key=lambda f: f["created_at"], reverse=True)


@app.get("/faults/{fault_id}")
def get_fault(fault_id: str):
    f = store.faults.get(fault_id)
    if not f:
        raise HTTPException(404, "fault not found")
    reports = [store.reports[r] for r in f["report_ids"] if r in store.reports]
    events = [e for e in store.events if e["report_id"] in f["report_ids"] or e["report_id"] == fault_id]
    return {**f, "reports": reports, "events": events}


@app.post("/faults/{fault_id}/inspection")
async def inspect_fault(fault_id: str, after: UploadFile = File(...),
                        before: UploadFile | None = File(None)):
    """Technician uploads repair photo(s): SAM 3.1 locates equipment, Muse Spark judges."""
    f = store.faults.get(fault_id)
    if not f:
        raise HTTPException(404, "fault not found")
    after_b = await after.read()
    before_b = await before.read() if before else None
    try:
        result = vision.inspect(f, after_b, before_b or None)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    v = result["verdict"]
    store.inspections.setdefault(fault_id, []).append(result)
    store.log(fault_id, "inspection", {"phrase": result["phrase"], "detections": result["detections"],
                                       "verdict": v})
    if v.get("verdict") == "resolved" and float(v.get("confidence", 0)) >= 0.6:
        f["status"] = "closed"
        if f["technician_id"] in store.technicians:
            store.technicians[f["technician_id"]]["available"] = True
    elif v.get("verdict") == "unsafe":
        f["status"] = "unsafe_followup"
    f["updated_at"] = datetime.now(timezone.utc).isoformat()
    return {"fault_id": fault_id, "status": f["status"], **result}


@app.get("/events")
def events(limit: int = 100):
    return store.events[-limit:]


@app.get("/feeders")
def feeders():
    return {"feeders": list(store.feeders.values()), "areas": list(store.areas.values())}


@app.get("/demo/samples")
def samples(n: int = 20, seed: int = 0, labels: bool = False, scenario: str = "storm"):
    """Synthetic reports for storm-mode demos. labels=true adds ground truth for scoring.

    scenario=storm: a realistic burst around a few incidents (collapses into a handful of faults).
    scenario=spread: reports spread evenly across every area and fault type (stress test).
    """
    import random
    pool = list(store.storm_reports if scenario == "storm" else store.sample_reports)
    random.Random(seed).shuffle(pool)
    keep = ("customer_name", "phone", "channel", "text", "area_hint")
    keep += ("expected_fault_type",) if labels else ()
    return [{k: r[k] for k in keep} for r in pool[:n]]


@app.post("/demo/reset")
def reset():
    store.reset()
    return {"ok": True}
