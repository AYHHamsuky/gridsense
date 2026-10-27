"""Offline tests for photo inspection (SAM + judge are stubbed)."""
import io
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from backend.app import tools, vision
from backend.app.main import app
from backend.app.store import store

SAM_TEXT = ("<0f>1<|box;x1=10;y1=20;x2=110;y2=220;w=200;h=300;c=0.91|><|mask;x=0;y=0;data=2,2,!abc|>,"
            "2<|box;x1=5;y1=5;x2=50;y2=60;w=200;h=300|><|mask;x=0;y=0;data=2,2,~xyz|>")


def _jpg(w=200, h=300):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 120, 160)).save(buf, format="JPEG")
    return buf.getvalue()


def test_parse_boxes_with_and_without_confidence():
    boxes = vision.parse_boxes(SAM_TEXT)
    assert len(boxes) == 2
    assert boxes[0] == {"x1": 10, "y1": 20, "x2": 110, "y2": 220, "w": 200, "h": 300, "conf": 0.91}
    assert "conf" not in boxes[1]
    assert vision.parse_boxes("no tokens here") == []


def test_overlay_scales_to_display_size():
    url = vision.overlay(_jpg(400, 600), vision.parse_boxes(SAM_TEXT), "transformer")
    assert url.startswith("data:image/png;base64,")


def _setup_fault():
    store.reset()
    fid = tools.create_fault("R1", "FDR-02", "Kawo", "transformer", "Transformer burning")["fault"]["id"]
    tools.assign_technician(fid)
    return fid


def test_inspection_resolved_closes_fault_and_frees_technician():
    fid = _setup_fault()
    tech = store.faults[fid]["technician_id"]
    seg = lambda img, phrase: {"phrase": phrase, "boxes": vision.parse_boxes(SAM_TEXT), "raw": SAM_TEXT}
    verdict = {"verdict": "resolved", "confidence": 0.85, "findings": ["new fuse fitted"], "safety_issues": []}
    with patch.object(vision, "segment", seg), patch.object(vision, "judge", lambda *a, **k: verdict):
        # inspect() binds defaults at definition time, so patch the call path used by the API
        orig = vision.inspect
        with patch.object(vision, "inspect", lambda f, a, b=None: orig(f, a, b, seg_fn=seg,
                                                                     judge_fn=lambda *x: verdict)):
            r = TestClient(app).post(f"/faults/{fid}/inspection",
                                     files={"after": ("a.jpg", _jpg(), "image/jpeg"),
                                            "before": ("b.jpg", _jpg(), "image/jpeg")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "closed"
    assert body["phrase"] == "electrical transformer"
    assert len(body["detections"]["after"]) == 2 and set(body["overlays"]) == {"after", "before"}
    assert store.technicians[tech]["available"] is True
    assert any(e["kind"] == "inspection" for e in store.events)


def test_inspection_survives_sam_failure_and_flags_unsafe():
    fid = _setup_fault()

    def boom(img, phrase):
        raise RuntimeError("SAM down")

    verdict = {"verdict": "unsafe", "confidence": 0.7, "findings": [], "safety_issues": ["exposed conductor"]}
    res = vision.inspect(store.faults[fid], _jpg(), None, seg_fn=boom, judge_fn=lambda *a: verdict)
    assert res["sam_errors"]["after"] == "SAM down"
    assert res["verdict"]["verdict"] == "unsafe"
