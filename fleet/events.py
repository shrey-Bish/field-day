"""Structured event bus (CLEAR-inspired): event-driven messages instead of constant chatter.

Every inbound message (operator event, tractor report) carries an id, a source, a per-source
sequence number and a time. The bus drops exact duplicates (same id) and stale messages
(sequence number not newer than the last one processed from that source), so a message
delivered twice is processed once. Safety-critical types are never dropped for being
'low value'; only true duplicates are dropped.
"""
import heapq

SAFETY_CRITICAL = {"TRACTOR_FAILED", "IMPLEMENT_FAILED", "OBSTACLE_DETECTED", "ROUTE_BLOCKED",
                   "LOW_ENERGY", "COMMUNICATION_LOST", "TRACTOR_STOP_CONFIRMED"}
PRIORITY = {k: 0 for k in SAFETY_CRITICAL}  # lower number = handled first at the same minute

# scenario.json event names -> protocol event types
SCENARIO_TYPES = {
    "breakdown": "TRACTOR_FAILED", "implement_failure": "IMPLEMENT_FAILED",
    "blocked_route": "ROUTE_BLOCKED", "obstacle_reported": "OBSTACLE_DETECTED",
    "route_cleared": "ROUTE_CLEARED", "priority_change": "PRIORITY_CHANGED",
    "new_job": "JOB_SUBMITTED", "fuel_drop": "LOW_ENERGY",
    "comm_lost": "COMMUNICATION_LOST", "comm_restored": "COMMUNICATION_RESTORED",
    "delay": "TRACTOR_DELAYED", "fuel_delivered": "FUEL_DELIVERED",
}


def from_scenario(events):
    """Turn scenario events into protocol messages with ids and sequence numbers."""
    msgs, seq = [], {}
    for i, ev in enumerate(sorted(events, key=lambda e: (e["t"], events.index(e)))):
        src = ev.get("tractor") or "operator"
        seq[src] = seq.get(src, 0) + 1
        msgs.append(dict(id=ev.get("id", f"M{i + 1:03d}"), t=ev["t"], source=src, seq=ev.get("seq", seq[src]),
                         type=SCENARIO_TYPES.get(ev["type"], ev["type"].upper()), payload=dict(ev)))
    return msgs


class EventBus:
    def __init__(self):
        self._q = []
        self._n = 0
        self.seen = set()
        self.last_seq = {}
        self.dropped = []

    def publish(self, msg):
        self._n += 1
        heapq.heappush(self._q, (msg["t"], PRIORITY.get(msg["type"], 1), self._n, msg))

    def peek_time(self):
        return self._q[0][0] if self._q else float("inf")

    def pop(self):
        """Next message that is not a duplicate, or None."""
        while self._q:
            _, _, _, msg = heapq.heappop(self._q)
            if msg["id"] in self.seen:
                self.dropped.append(dict(id=msg["id"], why="duplicate id"))
                continue
            src, sq = msg.get("source"), msg.get("seq")
            if sq is not None and not msg.get("internal") and sq <= self.last_seq.get(src, 0):
                self.dropped.append(dict(id=msg["id"], why="stale sequence number"))
                continue
            self.seen.add(msg["id"])
            if sq is not None and not msg.get("internal"):
                self.last_seq[src] = sq
            return msg
        return None

    def empty(self):
        return not self._q
