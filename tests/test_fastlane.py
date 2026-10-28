"""Offline tests for the open-weight fast lane + escalation routing."""
import json
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app import fastlane, open_model, tools
from backend.app.main import app
from backend.app.store import store


@pytest.fixture(autouse=True)
def fresh():
    store.reset()


def cls(ft="no_supply", area="Barnawa", conf=0.9, danger=False):
    return {"fault_type": ft, "area": area, "landmark": "", "language": "ha",
            "danger": danger, "confidence": conf, "summary_en": f"{ft} in {area}"}


def fake_agent(report):
    return {"fault_id": "AGENT", "fault_type": "line_down", "reply": "Keep away"}


def test_routine_reports_cluster_in_fast_lane():
    reports = [{"id": f"R{i}", "text": "Babu wuta"} for i in range(3)]
    out = fastlane.process_bulk(reports, classify_fn=lambda rs: [cls() for _ in rs],
                                agent_fn=fake_agent)
    assert out["stats"]["fast_lane"] == 3 and out["stats"]["escalated"] == 0
    assert len(store.faults) == 1
    f = next(iter(store.faults.values()))
    assert f["report_ids"] == ["R0", "R1", "R2"] and f["status"] == "dispatched"
    assert [r["action"] for r in out["results"]] == ["created", "attached", "attached"]


@pytest.mark.parametrize("c,reason", [
    (cls(ft="line_down", danger=True), "danger"),
    (cls(conf=0.3), "low confidence"),
    (cls(area="Lagos Island"), "could not locate"),
])
def test_risky_reports_escalate_to_agent(c, reason):
    out = fastlane.process_bulk([{"id": "R1", "text": "Waya ta tsinke"}],
                                classify_fn=lambda rs: [c], agent_fn=fake_agent)
    r = out["results"][0]
    assert r["lane"] == "agent" and reason in r["escalation_reason"]
    assert any(e["kind"] == "escalate" for e in store.events)


def test_parse_handles_bad_model_output():
    assert open_model._parse("not json")["fault_type"] == "other"
    assert open_model._parse('{"fault_type":"banana","confidence":0.9}')["confidence"] == 0.0
    ok = open_model._parse('Sure! {"fault_type":"meter","confidence":"0.8","danger":0}')
    assert ok["fault_type"] == "meter" and ok["confidence"] == 0.8 and ok["danger"] is False


def test_classify_many_survives_errors():
    calls = iter(['{"fault_type":"meter","confidence":0.9}', RuntimeError("boom")])

    def create(**kw):
        nxt = next(calls)
        if isinstance(nxt, Exception):
            raise nxt
        return NS(choices=[NS(message=NS(content=nxt))])

    cli = NS(chat=NS(completions=NS(create=create)))
    out = open_model.classify_many([{"text": "a"}, {"text": "b"}], workers=1, cli=cli, batch=1)
    assert out[0]["fault_type"] == "meter" and "error" in out[1]


def test_bulk_endpoint_and_labels():
    c = TestClient(app)
    s = c.get("/demo/samples", params={"n": 3, "labels": True}).json()
    assert all("expected_fault_type" in x for x in s)
    with patch.object(fastlane, "process_bulk", lambda reps: {"results": [], "stats": {"reports": len(reps)}}):
        r = c.post("/reports/bulk", json={"reports": s})
    assert r.status_code == 200 and r.json()["stats"]["reports"] == 3
    # not configured -> clear 503 (patched so a configured .env doesn't hit the real endpoint)
    with patch.object(open_model, "OPEN_BASE_URL", ""):
        r = c.post("/reports/bulk", json={"reports": s})
    assert r.status_code == 503 and "OPEN_BASE_URL" in r.json()["detail"]


def test_repeat_danger_attaches_in_fast_lane_with_safety_reply():
    def agent_opens_fault(report):
        fid = tools.create_fault(report["id"], "FDR-02", "Kawo", "transformer", "Transformer fire")["fault"]["id"]
        return {"fault_id": fid, "fault_type": "transformer", "reply": "Keep away"}

    fire = cls(ft="transformer", area="Kawo", danger=True)
    reports = [{"id": f"R{i}", "text": "Transformer yana ci da wuta"} for i in range(3)]
    out = fastlane.process_bulk(reports, classify_fn=lambda rs: [fire for _ in rs],
                                agent_fn=agent_opens_fault)
    lanes = [r["lane"] for r in out["results"]]
    assert lanes == ["agent", "fast", "fast"]  # only the first report of the fire escalates
    assert len(store.faults) == 1 and len(store.faults["FLT-0001"]["report_ids"]) == 3
    assert all("taba" in r["reply"] for r in out["results"][1:])  # Hausa safety warning


def test_fast_lane_reply_language_and_safety():
    assert "FLT-0009" in fastlane.reply("en", "FLT-0009", False)
    assert "touch" in fastlane.reply("en", "FLT-0009", True)
    assert "touch" not in fastlane.reply("en", "FLT-0009", False)
    assert fastlane.reply(None, "FLT-0009", False).startswith("Thank you")


def test_storm_scenario_collapses_into_few_faults():
    """Ground truth alone (perfect classifier): 150 storm reports -> a handful of faults."""
    c = TestClient(app)
    burst = c.get("/demo/samples", params={"n": 150, "labels": True, "scenario": "storm"}).json()
    keys = {(store.areas[r["area_hint"].lower()]["feeder_id"], r["expected_fault_type"]) for r in burst}
    assert 5 <= len(keys) <= 8
    spread = c.get("/demo/samples", params={"n": 150, "labels": True, "scenario": "spread"}).json()
    assert len({r["area_hint"] for r in spread}) > len({r["area_hint"] for r in burst})


def fake_cli(*replies):
    calls, prompts = iter(replies), []

    def create(**kw):
        prompts.append(kw["messages"][0]["content"])
        return NS(choices=[NS(message=NS(content=next(calls)))])
    return NS(chat=NS(completions=NS(create=create))), prompts


def test_classify_many_batches_reports_into_one_call():
    arr = json.dumps([{"fault_type": "meter", "confidence": 0.9},
                      {"fault_type": "line_down", "confidence": 0.8, "danger": True}])
    cli, prompts = fake_cli(arr)
    out = open_model.classify_many([{"text": "a"}, {"text": "b", "area_hint": "Kawo"}], cli=cli)
    assert len(prompts) == 1 and "1. a" in prompts[0] and "2. b (customer area: Kawo)" in prompts[0]
    assert [o["fault_type"] for o in out] == ["meter", "line_down"] and out[1]["danger"] is True


def test_bad_batch_reply_falls_back_to_one_call_per_report():
    cli, prompts = fake_cli('[{"fault_type": "meter"}]',  # wrong length -> fallback
                            '{"fault_type": "meter", "confidence": 0.9}',
                            '{"fault_type": "no_supply", "confidence": 0.7}')
    out = open_model.classify_many([{"text": "a"}, {"text": "b"}], cli=cli)
    assert len(prompts) == 3 and [o["fault_type"] for o in out] == ["meter", "no_supply"]
