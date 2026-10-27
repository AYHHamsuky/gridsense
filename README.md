# GridSense

**A Hausa/English AI agent that turns electricity customers' outage reports into clustered faults and technician dispatches for Nigerian power distribution companies.**

Built for the [Meta Global AI Developer Hackathon](https://cerebralvalley.ai/e/meta-ai-developer-hackathon) (Indie track · *Build for Your World*).

> All code in this repository was written during the hackathon (Oct 27 – Nov 4, 2026). The commit history shows the timeline.

## The problem
When a transformer fails in a Kaduna neighbourhood, hundreds of customers call or message at once, many in Hausa. The same fault gets logged again and again, technicians are sent late, and customers hear nothing back.

## What GridSense does
1. **Intake:** customers report by text or voice note, in Hausa or English.
2. **Triage agent:** Muse Spark works out the fault type, finds the right feeder, and either opens a new fault or merges the report into an existing one.
3. **Dispatch:** assigns a technician with the right skill, preferring someone who covers that feeder.
4. **Repair check:** the technician uploads a photo; SAM 3.1 finds the transformer/pole/meter and Muse Spark decides if the repair is complete and safe before closing the fault.
5. **Reply:** answers the customer in their own language with a fault ID and safety advice.
6. **Audit trail:** every tool call the agent makes is logged and shown on the dashboard.

## Meta models used
| Feature | Model | Status |
|---|---|---|
| Triage + dispatch agent (tool calling) | Muse Spark (`muse-spark-1.3`) | ✅ Day 1 |
| Hausa/English voice notes | Muse Voice Transcribe (`muse-voice-transcribe-1.0`) | ✅ Day 1 |
| Technician repair-photo check | SAM 3.1 (`sam-3.1`) + Muse Spark vision | ✅ Day 1 |
| Bulk classification in outage spikes | Muse Spark Open (Baseten/Modal) | 🔜 |

## Quick start
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add MODEL_API_KEY from https://dev.meta.ai
python data/generate_synthetic.py
python scripts/smoke_test.py  # one Muse Spark call + one full agent run
uvicorn backend.app.main:app --reload   # API docs at http://127.0.0.1:8000/docs
streamlit run ui/app.py                 # UI at http://localhost:8501 (2nd terminal)
pytest                        # offline tests (no API key needed)
```

## UI (Streamlit)
- **Report an outage:** customer chat in Hausa/English, plus voice notes
- **Dispatch dashboard:** live fault map, fault table, and the agent's step-by-step reasoning
- **Technician inspection:** upload before/after photos; SAM 3.1 boxes the equipment, Muse Spark gives a verdict and closes the fault
- **Storm mode:** replay up to 150 synthetic reports and watch them merge into a few faults

## API
| Method | Path | Purpose |
|---|---|---|
| POST | `/reports` | Text report → agent triage |
| POST | `/reports/voice` | WAV voice note → transcribe → triage |
| GET | `/faults` · `/faults/{id}` | Fault list / detail with reports + agent events |
| POST | `/faults/{id}/inspection` | Repair photos → SAM 3.1 + Muse Spark verdict |
| GET | `/events` | Agent audit log |
| GET | `/feeders` | Feeders and areas (for the map) |
| POST | `/demo/reset` | Reset demo state |

## Layout
```
ui/           Streamlit UI (calls the API)
backend/app/   FastAPI app, Meta client, agent loop, tools, in-memory store
data/          synthetic data generator + generated JSON
scripts/       smoke test
tests/         offline tests with a scripted fake model
```

## Data
All feeders, customers, technicians and reports are **synthetic**. Neighbourhood names are public place names used only as locations. No real utility or customer data is used.

## License
MIT
