"""Offline tests for customer notifications when a fault is closed or flagged."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import notify, tools, vision
from backend.app.main import app
from backend.app.store import store


def _fault_with_reports():
    store.reset()
    store.reports["R1"] = {"id": "R1", "customer_name": "Aisha", "phone": "0801",
                           "result": {"language": "ha", "fault_id": "FLT-0001"}}
    store.reports["R2"] = {"id": "R2", "customer_name": "John", "phone": "0802",
                           "classification": {"language": "en"},
                           "result": {"fault_id": "FLT-0001"}}
    store.reports["R3"] = {"id": "R3", "result": {"fault_id": "FLT-0001"}}  # language unknown
    fid = tools.create_fault("R1", "FDR-02", "Kawo", "transformer", "Transformer burnt")["fault"]["id"]
    tools.attach_report_to_fault("R2", fid)
    tools.attach_report_to_fault("R3", fid)
    tools.assign_technician(fid)
    return fid


def test_restored_uses_muse_spark_wording_per_language():
    fid = _fault_with_reports()
    v = {"customer_message_en": "Power is back in Kawo.", "customer_message_ha": "Wuta ta dawo a Kawo."}
    out = notify.notify_fault(store.faults[fid], "restored", v)
    by = {n["report_id"]: n for n in out}
    assert by["R1"]["message"] == "Wuta ta dawo a Kawo." and by["R1"]["language"] == "ha"
    assert by["R2"]["message"] == "Power is back in Kawo." and by["R2"]["source"] == "muse-spark"
    assert by["R3"]["language"] == "en"  # default
    assert store.events[-1]["kind"] == "notify" and store.events[-1]["detail"]["hausa"] == 1


def test_template_fallback_and_idempotent():
    fid = _fault_with_reports()
    first = notify.notify_fault(store.faults[fid], "restored", {})
    assert all(n["source"] == "template" and fid in n["message"] for n in first)
    assert "Albishir" in first[0]["message"]
    assert notify.notify_fault(store.faults[fid], "restored", {}) == []  # no double send
    assert len(store.notifications) == 3


def test_inspection_close_notifies_all_customers_and_status_endpoint():
    fid = _fault_with_reports()
    verdict = {"verdict": "resolved", "confidence": 0.9, "findings": ["new fuse"], "safety_issues": [],
               "customer_message_en": "Fixed.", "customer_message_ha": "An gyara."}
    fake = {"phrase": "electrical transformer", "detections": {}, "overlays": {}, "verdict": verdict}
    with patch.object(vision, "inspect", lambda f, a, b=None: fake):
        c = TestClient(app)
        r = c.post(f"/faults/{fid}/inspection", files={"after": ("a.jpg", b"x", "image/jpeg")})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "closed" and len(r.json()["notifications"]) == 3
        s = c.get("/reports/R1").json()
        assert s["fault"]["status"] == "closed" and s["notifications"][0]["message"] == "An gyara."
        assert len(c.get("/notifications", params={"fault_id": fid}).json()) == 3
        assert c.get("/reports/NOPE").status_code == 404


def test_unsafe_sends_followup_not_restored():
    fid = _fault_with_reports()
    fake = {"phrase": "p", "detections": {}, "overlays": {},
            "verdict": {"verdict": "unsafe", "confidence": 0.8, "safety_issues": ["exposed wire"]}}
    with patch.object(vision, "inspect", lambda f, a, b=None: fake):
        r = TestClient(app).post(f"/faults/{fid}/inspection", files={"after": ("a.jpg", b"x", "image/jpeg")})
    assert r.json()["status"] == "unsafe_followup"
    assert {n["kind"] for n in store.notifications} == {"followup"}
