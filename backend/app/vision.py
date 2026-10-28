"""Technician photo inspection: SAM 3.1 finds the equipment, Muse Spark judges the repair.

SAM 3.1 is called through the Responses API and returns special-token text:
  <|box;x1=..;y1=..;x2=..;y2=..;w=W;h=H[;c=conf]|><|mask;...|>
Docs: https://dev.meta.ai/docs/media-segmentation
      https://dev.meta.ai/docs/sam/reading-segmentation
We use the boxes (robust, enough for overlays + grounding the judge).
"""
import base64
import io
import json
import re

import httpx
from PIL import Image, ImageDraw, ImageFont

from .config import AGENT_MODEL, META_BASE_URL, MODEL_API_KEY, SAM_MODEL
from .meta_client import chat_client

# What SAM should look for, per fault type (SAM takes a short noun phrase)
SAM_PROMPT = {
    "transformer": "electrical transformer",
    "line_down": "power line",
    "no_supply": "electric pole",
    "low_voltage": "electrical transformer",
    "meter": "electricity meter",
    "other": "electric pole",
}
BOX_RE = re.compile(r"<\|box;([^|]*)\|>")


def data_url(img: bytes) -> str:
    fmt = (Image.open(io.BytesIO(img)).format or "JPEG").lower()
    return f"data:image/{fmt};base64,{base64.b64encode(img).decode()}"


def parse_boxes(text: str) -> list[dict]:
    """Extract boxes from SAM special-token output. Fields parsed by key."""
    boxes = []
    for m in BOX_RE.finditer(text or ""):
        kv = dict(p.split("=", 1) for p in m.group(1).split(";") if "=" in p)
        try:
            box = {k: int(float(kv[k])) for k in ("x1", "y1", "x2", "y2", "w", "h")}
        except (KeyError, ValueError):
            continue
        if "c" in kv:
            box["conf"] = round(float(kv["c"]), 3)
        boxes.append(box)
    return boxes


def _output_text(resp: dict) -> str:
    if isinstance(resp.get("output_text"), str):
        return resp["output_text"]
    parts = []
    for item in resp.get("output", []):
        for c in item.get("content", []) or []:
            if c.get("type") in ("output_text", "text"):
                parts.append(c.get("text", ""))
    return "\n".join(parts)


def segment(img: bytes, phrase: str) -> dict:
    """Run SAM 3.1 on one image for one noun phrase."""
    body = {
        "model": SAM_MODEL,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": phrase},
            {"type": "input_image", "image_url": data_url(img)},
        ]}],
        "metadata": {"include_confidence": "true"},
        "stream": False,
    }
    r = httpx.post(f"{META_BASE_URL}/responses", json=body, timeout=120,
                   headers={"Authorization": f"Bearer {MODEL_API_KEY}"})
    r.raise_for_status()
    text = _output_text(r.json())
    return {"phrase": phrase, "boxes": parse_boxes(text), "raw": text[:2000]}


def overlay(img: bytes, boxes: list[dict], label: str) -> str:
    """Draw SAM boxes on the photo; return a PNG data URL for the UI."""
    im = Image.open(io.BytesIO(img)).convert("RGB")
    d = ImageDraw.Draw(im)
    for b in boxes:
        sx, sy = im.width / max(b["w"], 1), im.height / max(b["h"], 1)
        xy = [b["x1"] * sx, b["y1"] * sy, b["x2"] * sx, b["y2"] * sy]
        d.rectangle(xy, outline=(255, 196, 0), width=max(3, im.width // 200))
        tag = f" SAM 3.1 · {label}" + (f" · {b['conf']:.2f} " if "conf" in b else " ")
        font = ImageFont.load_default(size=max(16, im.width // 40))
        tb = d.textbbox((0, 0), tag, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1] + 8
        ty = xy[1] - th if xy[1] - th >= 0 else xy[1]  # above the box, or inside if no room
        d.rectangle([xy[0], ty, xy[0] + tw, ty + th], fill=(255, 196, 0))
        d.text((xy[0], ty + 2), tag, fill=(20, 20, 20), font=font)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


JUDGE = """You are a senior electrical distribution inspector in Nigeria.
A technician was dispatched to this fault: {fault}
SAM 3.1 located these objects ("{phrase}") in the photos: {detections}
Look at the photo(s). The LAST image is the AFTER-repair photo{before_note}.
Decide if the repair is complete and safe. Return ONLY JSON:
{{"verdict": "resolved" | "not_resolved" | "unsafe" | "unclear",
  "confidence": 0.0-1.0,
  "findings": ["short observations"],
  "safety_issues": ["anything dangerous, e.g. exposed conductor, leaning pole, burn marks"],
  "customer_message_en": "one sentence for affected customers",
  "customer_message_ha": "the same sentence in Hausa"}}
Use "unclear" if the photo does not show the equipment well enough."""


def judge(fault: dict, after: bytes, before: bytes | None, detections: dict, client=None) -> dict:
    client = client or chat_client()
    content = [{"type": "text", "text": JUDGE.format(
        fault=json.dumps({k: fault[k] for k in ("id", "fault_type", "summary", "areas")}),
        phrase=detections.get("phrase", ""),
        detections=json.dumps({k: [b for b in v.get("boxes", [])] for k, v in detections.get("by_photo", {}).items()}),
        before_note="; the first image is BEFORE" if before else "")}]
    if before:
        content.append({"type": "image_url", "image_url": {"url": data_url(before)}})
    content.append({"type": "image_url", "image_url": {"url": data_url(after)}})
    resp = client.chat.completions.create(model=AGENT_MODEL, messages=[{"role": "user", "content": content}])
    text = resp.choices[0].message.content or ""
    s, e = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[s:e + 1])
    except (ValueError, json.JSONDecodeError):
        return {"verdict": "unclear", "confidence": 0, "findings": [text[:300]], "safety_issues": []}


def inspect(fault: dict, after: bytes, before: bytes | None = None,
            seg_fn=segment, judge_fn=judge) -> dict:
    """Full inspection: SAM on each photo -> overlays -> Muse Spark verdict."""
    phrase = SAM_PROMPT.get(fault["fault_type"], "electric pole")
    photos = {"after": after, **({"before": before} if before else {})}
    by_photo, overlays = {}, {}
    for name, img in photos.items():
        try:
            seg = seg_fn(img, phrase)
        except Exception as e:  # keep going: the judge can still look at the photo
            seg = {"phrase": phrase, "boxes": [], "error": str(e)}
        by_photo[name] = seg
        overlays[name] = overlay(img, seg["boxes"], phrase)
    detections = {"phrase": phrase, "by_photo": by_photo}
    verdict = judge_fn(fault, after, before, detections)
    return {"phrase": phrase, "detections": {k: v["boxes"] for k, v in by_photo.items()},
            "sam_errors": {k: v["error"] for k, v in by_photo.items() if "error" in v},
            "overlays": overlays, "verdict": verdict}
