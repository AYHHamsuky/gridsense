"""In-memory store seeded from synthetic JSON. Swap for PostgreSQL later."""
import json
import threading
from datetime import datetime, timezone

from .config import DATA_DIR


def _load(name):
    p = DATA_DIR / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else []


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.feeders = {f["id"]: f for f in _load("feeders")}
        self.areas = {a["name"].lower(): a for a in _load("areas")}
        self.technicians = {t["id"]: t for t in _load("technicians")}
        self.sample_reports = _load("reports")
        self.storm_reports = _load("storm")  # realistic outage burst for storm mode
        self.reports = {}
        self.faults = {}
        self.inspections = {}  # fault_id -> list of inspection results
        self.events = []  # agent audit log: every tool call + reason
        self._fault_seq = 0

    def next_fault_id(self):
        self._fault_seq += 1
        return f"FLT-{self._fault_seq:04d}"

    def log(self, report_id, kind, detail):
        self.events.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "report_id": report_id,
            "kind": kind,
            "detail": detail,
        })


store = Store()
