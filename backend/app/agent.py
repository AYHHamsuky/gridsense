"""GridSense triage agent: Muse Spark + tool calling.

For each customer report the agent: understands it (Hausa or English),
locates the feeder, merges duplicates into an existing fault or opens a new
one, dispatches a technician, and writes a reply in the customer's language.
Every tool call is logged to store.events so the dashboard can show reasoning.
"""
import json

from .config import AGENT_MODEL
from .meta_client import chat_client
from .store import store
from .tools import REGISTRY, TOOL_SCHEMAS

SYSTEM = """You are GridSense, the outage triage agent for an electricity distribution company in Kaduna, Nigeria.
Customers write in Hausa or English. For EACH report:
1. Work out the fault_type: no_supply, transformer, line_down, low_voltage, meter or other.
2. Call locate_feeder with the area/landmark mentioned.
3. Call find_open_fault for that feeder and fault_type.
   - If one exists, call attach_report_to_fault (it is a duplicate report).
   - Otherwise call create_fault with a short English summary.
4. If the fault has no technician, call assign_technician.
5. Finish with ONLY a JSON object:
   {"fault_id": "...", "fault_type": "...", "severity": "...", "language": "ha|en",
    "reply": "<short reply to the customer IN THEIR LANGUAGE with the fault ID>",
    "reasoning": "<one sentence on what you decided and why>"}
Safety: if a wire is down or there is fire, tell the customer to keep away and not touch it.
Never invent feeder or fault IDs; only use IDs returned by tools."""

MAX_STEPS = 8


def _parse_final(text: str) -> dict:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {"reply": text, "reasoning": "model did not return JSON"}


def triage(report: dict, client=None) -> dict:
    client = client or chat_client()
    rid = report["id"]
    store.reports[rid] = {**report, "status": "processing"}
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps({
            "report_id": rid, "channel": report.get("channel", "text"),
            "text": report["text"], "area_hint": report.get("area_hint", "")},
            ensure_ascii=False)},
    ]
    for step in range(MAX_STEPS):
        resp = client.chat.completions.create(
            model=AGENT_MODEL, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto")
        msg = resp.choices[0].message
        if not msg.tool_calls:
            result = _parse_final(msg.content)
            store.reports[rid].update(status="triaged", result=result)
            store.log(rid, "final", result)
            return result
        # Replay the assistant turn with its tool_calls, then one tool message per call
        messages.append({
            "role": "assistant", "content": msg.content or "",
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name,
                                         "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            fn = REGISTRY.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments or "{}")
                out = fn(**args) if fn else {"error": f"unknown tool {tc.function.name}"}
            except Exception as e:  # surface tool errors to the model, don't crash
                args, out = {}, {"error": str(e)}
            store.log(rid, "tool", {"name": tc.function.name, "args": args, "result": out})
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(out, ensure_ascii=False)})
    store.reports[rid].update(status="failed")
    return {"error": "agent exceeded max steps"}
