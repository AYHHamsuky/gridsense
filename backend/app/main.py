"""GridSense API. Run: uvicorn backend.app.main:app --reload"""
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import agent, meta_client
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
    return {"ok": True, "feeders": len(store.feeders), "technicians": len(store.technicians)}


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
    # TranscribeResponse shape: confirm on Day 1 and adjust this extraction
    text = asr.get("text") or asr.get("transcript") or str(asr)
    report = _new_report(text=text, area_hint=area_hint, phone=phone, channel="voice")
    return {"report_id": report["id"], "transcript": text, "result": agent.triage(report)}


@app.get("/faults")
def list_faults():
    return sorted(store.faults.values(), key=lambda f: f["created_at"], reverse=True)


@app.get("/faults/{fault_id}")
def get_fault(fault_id: str):
    f = store.faults.get(fault_id)
    if not f:
        raise HTTPException(404, "fault not found")
    reports = [store.reports[r] for r in f["report_ids"] if r in store.reports]
    events = [e for e in store.events if e["report_id"] in f["report_ids"]]
    return {**f, "reports": reports, "events": events}


@app.get("/events")
def events(limit: int = 100):
    return store.events[-limit:]


@app.get("/feeders")
def feeders():
    return {"feeders": list(store.feeders.values()), "areas": list(store.areas.values())}


@app.post("/demo/reset")
def reset():
    store.reset()
    return {"ok": True}
