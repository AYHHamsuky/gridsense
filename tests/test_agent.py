"""Offline tests: a scripted fake model drives the real agent loop and tools."""
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from backend.app import agent
from backend.app.main import app
from backend.app.store import store


def _call(i, name, args):
    return NS(id=f"call_{i}", function=NS(name=name, arguments=json.dumps(args)))


class FakeClient:
    """Plays back a fixed sequence of assistant turns."""

    def __init__(self, turns):
        self.turns = iter(turns)
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        tool_calls, content = next(self.turns)
        return NS(choices=[NS(message=NS(tool_calls=tool_calls, content=content))])


@pytest.fixture(autouse=True)
def fresh():
    store.reset()


def _script(rid, fault_lookup_then):
    return [
        ([_call(1, "locate_feeder", {"area": "Kawo"})], None),
        ([_call(2, "find_open_fault", {"feeder_id": "FDR-02", "fault_type": "transformer"})], None),
        fault_lookup_then,
        (None, json.dumps({"fault_id": "FLT-0001", "language": "ha", "reply": "Mun karba.",
                           "reasoning": "ok"})),
    ]


def test_new_report_creates_fault_and_dispatches():
    turns = _script("R1", ([
        _call(3, "create_fault", {"report_id": "R1", "feeder_id": "FDR-02", "area": "Kawo",
                                  "fault_type": "transformer", "summary": "Transformer burning"}),
        _call(4, "assign_technician", {"fault_id": "FLT-0001"})], None))
    out = agent.triage({"id": "R1", "text": "Transformer ya kone a Kawo"}, client=FakeClient(turns))
    assert out["fault_id"] == "FLT-0001"
    f = store.faults["FLT-0001"]
    assert f["status"] == "dispatched" and f["technician_id"]
    assert sum(e["kind"] == "tool" for e in store.events) == 4


def test_duplicate_report_is_clustered():
    from backend.app import tools
    tools.create_fault("R1", "FDR-02", "Kawo", "transformer", "first")
    assert tools.find_open_fault("FDR-02", "transformer")["found"]
    turns = _script("R2", ([_call(3, "attach_report_to_fault",
                                  {"report_id": "R2", "fault_id": "FLT-0001", "area": "Mando"})], None))
    agent.triage({"id": "R2", "text": "Transformer exploded near Mando junction"}, client=FakeClient(turns))
    f = store.faults["FLT-0001"]
    assert f["report_ids"] == ["R1", "R2"] and "Mando" in f["areas"]
    assert len(store.faults) == 1


def test_locate_feeder_by_landmark():
    from backend.app.tools import locate_feeder
    assert locate_feeder(landmark="Kawo bridge")["feeder_id"] == "FDR-02"
    assert not locate_feeder(area="Lagos Island")["found"]


def test_api_health_and_faults():
    c = TestClient(app)
    assert c.get("/health").json()["feeders"] == 5
    assert c.get("/faults").json() == []
