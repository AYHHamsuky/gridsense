"""GridSense dashboard. Run the API first, then: streamlit run frontend/streamlit_app.py"""
import base64
import json
import os
import time
from pathlib import Path

import httpx
import pandas as pd
import pydeck as pdk
import streamlit as st

API_URL = os.getenv("GRIDSENSE_API_URL", "http://127.0.0.1:8000").rstrip("/")
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}
SEVERITY_RGB = {"critical": [220, 38, 38], "high": [234, 88, 12],
                "medium": [234, 179, 8], "low": [59, 130, 246]}
VERDICT_ICON = {"resolved": "✅", "unsafe": "⛔", "not_resolved": "❌"}

st.set_page_config(page_title="GridSense", page_icon="⚡", layout="wide")


def api(method, path, **kw):
    try:
        r = httpx.request(method, f"{API_URL}{path}", timeout=120, **kw)
    except httpx.HTTPError as e:
        st.error(f"Cannot reach the GridSense API at {API_URL}: {e}")
        st.stop()
    if r.status_code >= 400:
        detail = r.json().get("detail", r.text) if "json" in r.headers.get("content-type", "") else r.text
        raise RuntimeError(f"{r.status_code}: {detail}")
    return r.json()


@st.cache_data
def sample_reports():
    p = DATA_DIR / "reports.json"
    return json.loads(p.read_text()) if p.exists() else []


@st.cache_data(ttl=300)
def geo():
    return api("GET", "/feeders")


def fault_frame(faults):
    areas = {a["name"]: a for a in geo()["areas"]}
    rows = []
    for f in faults:
        a = areas.get(f["areas"][0], {}) if f["areas"] else {}
        rows.append({
            "id": f["id"], "severity": f["severity"], "type": f["fault_type"],
            "feeder": f["feeder_id"], "areas": ", ".join(f["areas"]),
            "reports": len(f["report_ids"]), "status": f["status"],
            "technician": f["technician_id"] or "—", "summary": f["summary"],
            "updated": f["updated_at"], "lat": a.get("lat"), "lon": a.get("lon"),
            "color": SEVERITY_RGB.get(f["severity"], [120, 120, 120]),
            "radius": 250 + 120 * len(f["report_ids"]),
        })
    return pd.DataFrame(rows)


def fault_map(df):
    """Open faults sized by report count and coloured by severity, over all service areas."""
    areas = pd.DataFrame(geo()["areas"])
    layers = [pdk.Layer("ScatterplotLayer", areas, get_position=["lon", "lat"],
                        get_fill_color=[48, 164, 108], get_radius=120, opacity=0.5)]
    if not df.empty:
        layers.append(pdk.Layer("ScatterplotLayer", df.dropna(subset=["lat"]),
                                get_position=["lon", "lat"], get_fill_color="color",
                                get_radius="radius", opacity=0.6, pickable=True))
    st.pydeck_chart(pdk.Deck(
        layers=layers, map_style=None,
        initial_view_state=pdk.ViewState(latitude=10.52, longitude=7.44, zoom=11),
        tooltip={"text": "{id} · {type} ({severity})\n{areas}\nReports: {reports} · Tech: {technician}"}))
    st.caption("🟢 service area · larger circles = more reports · colour = severity")


def triage_reply(result):
    """Turn an agent result into a chat message for the customer view."""
    if "error" in result:
        return f"⚠️ {result['error']}"
    reply = result.get("reply", "")
    if result.get("fault_id"):
        sev = result.get("severity", "")
        reply += (f"\n\n`{result['fault_id']}` · {result.get('fault_type', '')} · "
                  f"{SEVERITY_ICON.get(sev, '')} {sev}")
    return reply


def send_to_chat(user_text, call):
    st.session_state.chat.append(("user", user_text))
    try:
        out = call()
        if "transcript" in out:
            st.session_state.chat[-1] = ("user", f"🎙️ {out['transcript']}")
        st.session_state.chat.append(("assistant", triage_reply(out["result"])))
    except RuntimeError as e:
        st.session_state.chat.append(("assistant", f"⚠️ {e}"))


if "chat" not in st.session_state:
    st.session_state.chat = []

# ---------- sidebar ----------
with st.sidebar:
    st.title("⚡ GridSense")
    st.caption("Hausa/English outage triage for Kaduna")
    health = api("GET", "/health")
    st.success(f"API online · {health['feeders']} feeders · {health['technicians']} technicians")
    st.caption(f"API: `{API_URL}`")
    if st.button("Reset demo state", width="stretch"):
        api("POST", "/demo/reset")
        st.session_state.chat = []
        st.toast("Demo state reset")
        st.rerun()

tab_report, tab_dispatch, tab_inspect, tab_storm, tab_log = st.tabs(
    ["📨 Report an outage", "🗺️ Dispatch", "🔧 Inspection", "🌩️ Storm mode", "📜 Agent log"])

# ---------- customer report (chat) ----------
with tab_report:
    st.caption("Write or speak in Hausa or English — e.g. *Babu wuta a Barnawa tun safe.*")
    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    area_hint = c1.text_input("Area hint (optional)", placeholder="e.g. Barnawa, Kawo, Television")
    samples = sample_reports()
    if samples and c2.button("Use a random sample report", width="stretch"):
        s = samples[int(time.time()) % len(samples)]
        with st.spinner("Muse Spark is triaging…"):
            send_to_chat(s["text"], lambda: api("POST", "/reports", json={
                k: s[k] for k in ("text", "customer_name", "phone", "area_hint", "channel")}))

    with st.container(border=True):
        audio = st.audio_input("Record a voice note", sample_rate=16000)
        if audio and st.button("Send voice note", type="primary"):
            with st.spinner("Transcribing and triaging…"):
                send_to_chat("🎙️ (voice note)", lambda: api(
                    "POST", "/reports/voice",
                    files={"audio": ("voice_note.wav", audio.getvalue(), "audio/wav")},
                    data={"area_hint": area_hint}))

    for role, text in st.session_state.chat:
        st.chat_message(role).markdown(text)

    if msg := st.chat_input("Describe the problem and your area…"):
        with st.spinner("Muse Spark is triaging…"):
            send_to_chat(msg, lambda: api("POST", "/reports", json={
                "text": msg, "area_hint": area_hint, "channel": "text"}))
        st.rerun()

# ---------- dispatch ----------
with tab_dispatch:
    df = fault_frame(api("GET", "/faults"))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open faults", int((df["status"] != "closed").sum()) if not df.empty else 0)
    c2.metric("Dispatched", int((df["status"] == "dispatched").sum()) if not df.empty else 0)
    c3.metric("Reports merged", int(df["reports"].sum()) if not df.empty else 0)
    c4.metric("Critical", int((df["severity"] == "critical").sum()) if not df.empty else 0)

    left, right = st.columns([3, 2])
    with left:
        fault_map(df)
    with right:
        if df.empty:
            st.info("No faults yet. Submit a report to get started.")
        else:
            st.dataframe(df[["id", "severity", "type", "status", "areas", "reports", "technician"]],
                         width="stretch", hide_index=True)

    if not df.empty:
        fid = st.selectbox("Fault detail", df["id"])
        detail = api("GET", f"/faults/{fid}")
        st.subheader(f"{SEVERITY_ICON.get(detail['severity'], '')} {detail['id']} · {detail['summary']}")
        st.write(f"Feeder **{detail['feeder_id']}** · areas {', '.join(detail['areas'])} · "
                 f"status **{detail['status']}** · technician **{detail['technician_id'] or '—'}**")
        st.markdown("**Customer reports**")
        st.dataframe(pd.DataFrame(detail["reports"]).drop(columns=["result"], errors="ignore"),
                     width="stretch", hide_index=True)
        st.markdown("**Agent steps**")
        for e in detail["events"]:
            d = e["detail"]
            if e["kind"] == "tool":
                with st.expander(f"🔧 {d['name']} · {e['report_id']}"):
                    st.json(d)
            elif e["kind"] == "inspection":
                v = d["verdict"]
                st.warning(f"📷 Photo inspection ({d['phrase']}): **{v.get('verdict')}** "
                           f"({v.get('confidence')}) — {'; '.join(v.get('findings', []))}")
            elif e["kind"] == "classify":
                with st.expander(f"⚡ Muse Spark Open: {d.get('fault_type')} "
                                 f"({d.get('confidence')}) · {e['report_id']}"):
                    st.json(d)
            elif e["kind"] == "escalate":
                st.info(f"↗️ {e['report_id']} escalated to Muse Spark agent: {d['reason']}")
            elif e["kind"] == "notify":
                st.success(f"📲 {d['kind']} message sent to {d['customers']} customers "
                           f"({d['hausa']} Hausa, {d['english']} English)")
            else:
                with st.expander(f"✅ final answer · {e['report_id']}"):
                    st.json(d)

# ---------- technician inspection ----------
with tab_inspect:
    st.caption("Upload the repair photo. SAM 3.1 finds the equipment; Muse Spark checks "
               "the repair is complete and safe, then closes the fault.")
    open_faults = [f for f in api("GET", "/faults") if f["status"] != "closed"]
    if not open_faults:
        st.info("No open faults. Send some reports first.")
    else:
        labels = {f"{f['id']} · {f['fault_type']} · {', '.join(f['areas'])}": f["id"] for f in open_faults}
        fid = labels[st.selectbox("Fault", list(labels))]
        c1, c2 = st.columns(2)
        before = c1.file_uploader("Before photo (optional)", type=["jpg", "jpeg", "png", "webp"])
        after = c2.file_uploader("After-repair photo", type=["jpg", "jpeg", "png", "webp"])
        if st.button("Run inspection", type="primary", disabled=not after):
            files = {"after": (after.name, after.getvalue(), after.type)}
            if before:
                files["before"] = (before.name, before.getvalue(), before.type)
            with st.spinner("SAM 3.1 segmenting · Muse Spark inspecting…"):
                try:
                    res = api("POST", f"/faults/{fid}/inspection", files=files)
                except RuntimeError as e:
                    st.error(str(e))
                    res = None
            if res:
                v = res["verdict"]
                st.subheader(f"{VERDICT_ICON.get(v.get('verdict'), '❔')} "
                             f"{v.get('verdict', '?').replace('_', ' ').capitalize()} · "
                             f"confidence {v.get('confidence')} · fault now **{res['status']}**")
                cols = st.columns(len(res["overlays"]))
                for col, (name, url) in zip(cols, sorted(res["overlays"].items(), reverse=True)):
                    n = len(res["detections"].get(name, []))
                    col.image(base64.b64decode(url.split(",", 1)[1]),
                              caption=f"{name}: {n} × {res['phrase']} found by SAM 3.1")
                if res.get("sam_errors"):
                    st.caption(f"SAM issues: {res['sam_errors']}")
                st.markdown("**Findings**\n" + "\n".join(f"- {x}" for x in v.get("findings", [])))
                if v.get("safety_issues"):
                    st.error("Safety: " + "; ".join(v["safety_issues"]))
                st.info(f"📣 {v.get('customer_message_en', '')}\n\n🗣️ {v.get('customer_message_ha', '')}")
                sent = res.get("notifications", [])
                if sent:
                    ha = sum(n["language"] == "ha" for n in sent)
                    st.success(f"📲 Notified **{len(sent)}** customers on {fid} "
                               f"({ha} in Hausa, {len(sent) - ha} in English)")
                    st.dataframe(pd.DataFrame(sent)[["customer_name", "phone", "language", "message"]],
                                 width="stretch", hide_index=True)

# ---------- storm mode ----------
with tab_storm:
    st.caption("Replay a burst of synthetic customer reports and watch them merge "
               "into a handful of faults.")
    fast_ok = bool(health.get("fast_lane"))
    engines = {"hybrid": "⚡ Hybrid: Muse Spark Open fast lane + Muse Spark escalation",
               "agent": "🧠 Full agent: Muse Spark on every report"}
    engine = st.segmented_control("Engine", list(engines), format_func=engines.get,
                                  default="hybrid" if fast_ok else "agent", required=True)
    hybrid = engine == "hybrid"
    if hybrid and not fast_ok:
        st.warning("Fast lane not configured. Set OPEN_BASE_URL / OPEN_API_KEY / OPEN_MODEL in .env.")
    scenarios = {"storm": "Realistic outage: 3 incidents + noise",
                 "spread": "Stress test: evenly spread across all areas"}
    scenario = st.segmented_control("Scenario", list(scenarios), format_func=scenarios.get,
                                    default="storm", required=True)
    n = st.slider("Number of reports", 5, 150, 150 if scenario == "storm" else 30)
    if st.button(f"Send {n} reports", type="primary"):
        burst = api("GET", "/demo/samples", params={"n": n, "seed": int(time.time()),
                                                    "labels": True, "scenario": scenario})
        bar, feed = st.progress(0.0), st.empty()
        results, t0 = [], time.time()
        if hybrid:
            stats = {"fast_lane": 0, "escalated": 0}
            for i in range(0, len(burst), 50):
                chunk = burst[i:i + 50]
                try:
                    res = api("POST", "/reports/bulk", json={"reports": chunk})
                except RuntimeError as e:
                    st.error(str(e))
                    break
                results += res["results"]
                for k in stats:
                    stats[k] += res["stats"][k]
                feed.markdown(f"**{len(results)}/{len(burst)}** classified by Muse Spark Open · "
                              f"{stats['fast_lane']} fast lane · {stats['escalated']} escalated to agent")
                bar.progress(len(results) / len(burst))
        else:
            for i, s in enumerate(burst, 1):
                try:
                    results.append(api("POST", "/reports", json=s)["result"])
                except RuntimeError as e:
                    results.append({"error": str(e)})
                feed.markdown(f"**{i}/{len(burst)}** · {s['text']}  →  "
                              f"`{results[-1].get('fault_id', 'error')}`")
                bar.progress(i / len(burst))
        secs = time.time() - t0
        faults = api("GET", "/faults")
        scored = [(s["expected_fault_type"], r.get("fault_type")) for s, r in zip(burst, results)]
        acc = sum(a == b for a, b in scored) / max(len(scored), 1)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Reports", len(results))
        c2.metric("Faults", len(faults))
        c3.metric("Fault-type accuracy", f"{acc:.0%}")
        c4.metric("Time", f"{secs:.1f}s", f"{secs / max(len(results), 1):.2f}s / report",
                  delta_color="off")
        if hybrid and results:
            esc = [r for r in results if r.get("lane") == "agent"]
            safe = [r for r in results if r.get("lane") == "fast" and r.get("danger")]
            with st.expander(f"Escalated to Muse Spark agent ({len(esc)})"):
                for r in esc:
                    st.markdown(f"`{r['report_id']}` · {r.get('escalation_reason')} → `{r.get('fault_id')}`")
            with st.expander(f"Repeat danger reports attached in the fast lane with a safety reply ({len(safe)})"):
                for r in safe:
                    st.markdown(f"`{r['report_id']}` → `{r['fault_id']}` · {r['reply']}")
        fault_map(fault_frame(faults))

# ---------- audit log ----------
with tab_log:
    limit = st.slider("Events to show", 10, 500, 100)
    events = api("GET", "/events", params={"limit": limit})
    if not events:
        st.info("No agent activity yet.")
    for e in reversed(events):
        label = {"tool": e["detail"].get("name", "tool"), "inspection": "photo inspection",
                 "classify": "Muse Spark Open classify", "escalate": "escalated to agent",
                 "notify": "customers notified"}.get(e["kind"], "final")
        with st.expander(f"{e['at'][11:19]} · {e['report_id']} · {label}"):
            st.json(e["detail"])
