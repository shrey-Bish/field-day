#!/usr/bin/env python3
"""Fleet coordinator: the central controller. Plans the field, watches every tractor agent,
commands the tractors and serves the dashboard.

    tractor agents  --telemetry JSON (GPS, fuel, battery), 10 Hz-->  this program   :10200
    this program    --JSON commands----------------------------->  each agent     :10301-10303
    this program    --drive commands (UDP text)------------------>  fleet bridge   :10100
    browser         <--JSON over HTTP-----------------------------  http://localhost:8080

Everything the coordinator knows about a tractor (where it is, fuel, battery) comes from that
tractor's agent reports. See tractor_agent.py. Standard library only (Python 3.9+).
"""
import json
import math
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import recovery_agent
from tractor_agent import (AGENT_PORTS, BATTERY_MOVE_PCT_PER_KM, BATTERY_WORK_PCT_PER_KM, EMPTY_PCT,
                           FUEL_MOVE_L_PER_KM, FUEL_WORK_L_PER_KM, LOW_PCT, TANK_L, TELEMETRY_ADDR,
                           TractorAgent)

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = ("127.0.0.1", 10100)
HTTP_PORT = 8080
COLORS = {1: "#ffb000", 2: "#00d0ff", 3: "#ff5fb0"}
ICONS = os.path.join(HERE, "..", "AgOpenWeb", "Shared", "AgOpenWeb.RemoteServer", "wwwroot", "icons")
IMAGES = {"/img/tractor.png": "TractorAoG.png", "/img/wheel.png": "FrontWheels.png"}

R = 6371008.8
RAD = math.pi / 180
MAX_PASSES = 600
ARRIVE_M = 0.3          # GPS within this of the goal counts as arrived
CONTACT_TIMEOUT_S = 2.0  # no agent report for this long = out of contact
FAULT_AFTER_S = 5.0      # out of contact this long while working = faulty; its work is reassigned


class Tractor:
    """What the coordinator holds about one tractor: its agent's latest report plus its job."""

    def __init__(self, tid):
        self.id = tid
        self.name = "Tractor %d" % tid
        self.color = COLORS[tid]
        # operator settings
        self.init_fuel = 100.0
        self.init_battery = 100.0
        self.speed_kmh = 10.0
        # latest agent report
        self.lat = self.lon = None
        self.heading = 0.0
        self.speed = 0.0
        self.fix = 0
        self.sats = 0
        self.gps_ok = False
        self.fuel_pct = 100.0
        self.fuel_l = TANK_L
        self.battery_pct = 100.0
        self.odometer_m = 0.0
        self.agent_status = "no_report"
        self.last_report = 0.0
        self.reports = 0
        self.report_lag = 0.0
        self.contact = False
        self.lost_logged = False
        self.u = self.v = 0.0
        self.hold_until = 0.0
        self.last_mode = 0.0
        self.reset()

    def reset(self):
        self.state = "idle"       # idle | transit | working | broken | done
        self.broken = False
        self.reason = ""          # why it is out of service
        self.pass_ = None
        self.dir = 1
        self.goal = None          # (u, v) it has been told to drive to
        self.path = []            # the curve it follows to get there, while moving between passes
        self.seg = None           # the swath it is painting right now
        self.travel = 0.0         # metres driven between passes
        self.idle = 0.0           # field seconds spent waiting for work
        self.prev = None
        self.last_cmd = 0.0
        self.warned = set()       # low-level warnings already logged this run

    def in_contact(self, now):
        return self.reports > 0 and now - self.last_report <= CONTACT_TIMEOUT_S


class Pass:
    def __init__(self, pid, v, a, b):
        self.id, self.v, self.a, self.b = pid, v, a, b
        self.a0, self.b0 = a, b   # original extent; a/b shrink if a tractor stops partway
        self.owner = None
        self.busy = False
        self.done = False
        self.rescue = False       # handed over by the recovery agent after a fault
        self.seq = 0              # order the recovery agent wants rescued passes done in


# --------------------------------------------------------------------------- coordinator
class Fleet:
    def __init__(self):
        self.lock = threading.RLock()
        self.tractors = [Tractor(i) for i in (1, 2, 3)]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.field = None
        self.passes = []
        self.segments = []
        self.log = []
        self.width_m = 12.0
        self.rate = 20.0
        self.running = self.started = self.finished = False
        self.sim_time = 0.0
        self.field_id = self.plan_id = self.seg_id = 0
        self.note = ""
        self.last_recovery = None
        self.rescue_seq = 0
        os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
        self.log_path = os.path.join(HERE, "logs", "event-log.txt")

    # ---- outgoing messages
    def send(self, text):
        """A drive command to the fleet bridge."""
        try:
            self.sock.sendto(text.encode("ascii"), BRIDGE)
        except OSError:
            pass

    def tell_agent(self, t, **msg):
        """A JSON command to one tractor's agent."""
        try:
            self.sock.sendto(json.dumps(msg).encode("utf-8"), ("127.0.0.1", AGENT_PORTS[t.id]))
        except OSError:
            pass

    def reset_agent(self, t):
        self.tell_agent(t, cmd="reset", fuel_pct=t.init_fuel, battery_pct=t.init_battery)
        t.fuel_pct, t.fuel_l, t.battery_pct = t.init_fuel, TANK_L * t.init_fuel / 100, t.init_battery
        t.warned = set()
        t.hold_until = time.time() + 0.6        # until its next reports reflect the reset

    def goto(self, t):
        t.last_cmd = time.time()
        if t.state == "transit" and t.path:
            # Send the turn from wherever the tractor is on it now (this is also the re-send).
            i = min(range(len(t.path)), key=lambda k: math.hypot(t.path[k][0] - t.u, t.path[k][1] - t.v))
            rest = t.path[i + 1:] or t.path[-1:]
            pts = " ".join("%.9f %.9f" % self.to_ll(u, v) for u, v in rest)
            self.send("path %d %.2f %s" % (t.id, t.speed_kmh, pts))
            return
        lat, lon = self.to_ll(*t.goal)
        self.send("goto %d %.9f %.9f %.2f" % (t.id, lat, lon, t.speed_kmh))

    def turn_path(self, t, end, end_dir):
        """A smooth curve from where the tractor is, in the direction it is facing, to `end`
        arriving along the pass (end_dir = +1 or -1 along u). Between neighbouring passes this
        is a U-turn that swings out past the field edge, as AgOpenGPS drives its headland turns."""
        p0 = (t.u, t.v)
        d = math.hypot(end[0] - p0[0], end[1] - p0[1])
        if d < 0.5:
            return [end]
        a = (90 - t.heading) * RAD - self.field["theta"]        # GPS compass heading -> angle in the pass frame
        m = min(max(2 * d, 20.0), 45.0)                         # how far the curve carries on before bending
        t0 = (math.cos(a) * m, math.sin(a) * m)
        t1 = (end_dir * m, 0.0)
        n = max(8, min(70, int(d + m)))                         # points about a metre apart
        out = []
        for i in range(1, n + 1):
            s = i / n
            h00, h10 = 2 * s ** 3 - 3 * s ** 2 + 1, s ** 3 - 2 * s ** 2 + s
            h01, h11 = -2 * s ** 3 + 3 * s ** 2, s ** 3 - s ** 2
            out.append((h00 * p0[0] + h10 * t0[0] + h01 * end[0] + h11 * t1[0],
                        h00 * p0[1] + h10 * t0[1] + h01 * end[1] + h11 * t1[1]))
        out[-1] = end
        return out

    # ---- incoming: one agent report
    def on_telemetry(self, msg):
        with self.lock:
            tid = msg.get("id")
            if msg.get("type") != "telemetry" or tid not in (1, 2, 3):
                return
            t = self.tractors[tid - 1]
            now = time.time()
            t.reports += 1
            t.last_report = now
            t.report_lag = max(0.0, now - float(msg.get("sent", now)))
            t.gps_ok = bool(msg.get("gps_ok"))
            if t.gps_ok and msg.get("lat") is not None:
                t.lat, t.lon = float(msg["lat"]), float(msg["lon"])
                t.heading = float(msg.get("heading") or 0.0)
            t.speed = float(msg.get("speed") or 0.0)
            t.fix, t.sats = int(msg.get("fix") or 0), int(msg.get("sats") or 0)
            if now >= t.hold_until:                      # right after a reset, older reports still arrive
                t.fuel_pct, t.fuel_l = float(msg["fuel_pct"]), float(msg["fuel_l"])
                t.battery_pct = float(msg["battery_pct"])
                t.odometer_m = float(msg.get("odometer_m") or 0.0)
            t.agent_status = str(msg.get("status", "ok"))

    # ---- geometry: flat local plane in metres, rotated so passes run along +u
    def to_xy(self, lat, lon):
        f = self.field
        return (lon - f["lon0"]) * RAD * R * math.cos(f["lat0"] * RAD), (lat - f["lat0"]) * RAD * R

    def to_uv(self, lat, lon):
        x, y = self.to_xy(lat, lon)
        c, s = math.cos(self.field["theta"]), math.sin(self.field["theta"])
        return x * c + y * s, -x * s + y * c

    def to_ll(self, u, v):
        f = self.field
        c, s = math.cos(f["theta"]), math.sin(f["theta"])
        x, y = u * c - v * s, u * s + v * c
        return f["lat0"] + y / (RAD * R), f["lon0"] + x / (RAD * R * math.cos(f["lat0"] * RAD))

    # ---- log
    def say(self, text, kind=""):
        self.log.append({"t": self.sim_time, "text": text, "kind": kind})
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write("%s  field %s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), clock(self.sim_time), text))
        except OSError:
            pass

    # ---- field and plan
    def set_field(self, ring):
        with self.lock:
            ring = [[float(p[0]), float(p[1])] for p in ring]
            if len(ring) > 1 and ring[0] == ring[-1]:
                ring = ring[:-1]
            if len(ring) < 3:
                raise ValueError("A field needs at least three corners.")
            lat0 = sum(p[0] for p in ring) / len(ring)
            lon0 = sum(p[1] for p in ring) / len(ring)
            self.field = {"ring": ring, "lat0": lat0, "lon0": lon0, "theta": 0.0}
            xy = [self.to_xy(*p) for p in ring]
            best = area = 0.0
            for i, a in enumerate(xy):
                b = xy[(i + 1) % len(xy)]
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                if length > best:                      # passes run parallel to the longest edge
                    best = length
                    self.field["theta"] = math.atan2(b[1] - a[1], b[0] - a[0])
                area += a[0] * b[1] - b[0] * a[1]
            self.field["area"] = abs(area) / 2
            self.field["uv"] = [self.to_uv(*p) for p in ring]
            self.field_id += 1
            self.log = []
            self.build_plan()

    def clear_field(self):
        with self.lock:
            for t in self.tractors:
                self.send("stop %d" % t.id)
                self.tell_agent(t, cmd="mode", working=False)
                t.reset()
            self.field = None
            self.passes, self.segments, self.log = [], [], []
            self.running = self.started = self.finished = False
            self.sim_time = 0.0
            self.note = ""
            self.last_recovery = None
            self.plan_id += 1

    def build_plan(self):
        with self.lock:
            f = self.field
            if not f:
                return
            self.running = self.started = self.finished = False
            self.sim_time = 0.0
            self.passes, self.segments = [], []
            self.plan_id += 1
            self.note = ""
            self.last_recovery = None
            uv = f["uv"]
            v_min, v_max = min(p[1] for p in uv), max(p[1] for p in uv)
            u_min = min(p[0] for p in uv)
            n = max(1, round((v_max - v_min) / self.width_m))
            step = (v_max - v_min) / n                 # spread the passes evenly edge to edge
            f["width"] = step
            if n > MAX_PASSES:
                self.note = "Too large for a %g m implement (%d passes). Draw a smaller area or raise the width." % (self.width_m, n)
            else:
                for i in range(n):
                    v = v_min + step * (i + 0.5)
                    xs = []
                    for k, p in enumerate(uv):
                        q = uv[(k + 1) % len(uv)]
                        if (p[1] - v) * (q[1] - v) < 0:
                            xs.append(p[0] + (v - p[1]) / (q[1] - p[1]) * (q[0] - p[0]))
                    if len(xs) >= 2 and max(xs) - min(xs) >= 2:
                        self.passes.append(Pass(len(self.passes) + 1, v, min(xs), max(xs)))
            # Rough positions across the field decide which block each tractor gets...
            for i, t in enumerate(self.tractors):
                t.reset()
                t.u, t.v = u_min - 10, v_min + (v_max - v_min) * (i + 0.5) / len(self.tractors)
            self.send("rate %g" % self.rate)
            before = self.snapshot()
            self.assign()
            self.park()                                 # ...then each one waits at the edge of its own block
            if self.passes:
                self.say("Plan ready: %d passes split into three side-by-side blocks: %s." % (len(self.passes), self.describe(before)), "plan")

    def park(self):
        """Before work starts: put each tractor just outside the field at the first pass of
        its own block, facing along it, so it works its block from that edge across. Each
        agent starts from its configured fuel and battery."""
        heading = (90 - self.field["theta"] / RAD) % 360
        for t in self.tractors:
            mine = [p for p in self.passes if p.owner is t]
            if mine:
                first = min(mine, key=lambda p: p.v)
                t.u, t.v = first.a - 10, first.v
            t.prev = None
            lat, lon = self.to_ll(t.u, t.v)
            self.send("place %d %.9f %.9f %.1f" % (t.id, lat, lon, heading))
            self.reset_agent(t)

    def remaining_on(self, t):
        p = t.pass_
        if not p:
            return 0.0
        if t.state != "working":
            return p.b - p.a
        return p.b - t.u if t.dir > 0 else t.u - p.a

    def assign(self):
        """Give every pass nobody is on right now to the tractors that can still work:
        neighbouring passes stay together, and each tractor's share of the length is in
        proportion to its speed, so they finish at about the same time. Passes the recovery
        agent handed over, and passes waiting on a faulty tractor, are left where they are."""
        active = [t for t in self.tractors if not t.broken]
        open_ = sorted((p for p in self.passes if not p.done and not p.busy and not p.rescue
                        and not (p.owner and p.owner.broken)), key=lambda p: p.v)
        for p in open_:
            p.owner = None
        if not active:
            return
        order = sorted(active, key=lambda t: t.v)       # blocks follow the tractors' positions
        have = [self.remaining_on(t) if t.pass_ and not t.pass_.done else 0.0 for t in order]
        grand = sum(p.b - p.a for p in open_) + sum(have)
        speed_sum = sum(t.speed_kmh for t in order)
        shares = [grand * t.speed_kmh / speed_sum for t in order]
        k, acc = 0, have[0]
        for p in open_:
            length = p.b - p.a
            while k < len(order) - 1 and acc + length / 2 > shares[k]:
                k += 1
                acc = have[k]
            p.owner = order[k]
            acc += length

    def snapshot(self):
        return {t.id: sum(1 for p in self.passes if not p.done and p.owner is t) for t in self.tractors}

    def describe(self, before):
        parts = []
        for t in self.tractors:
            if t.broken:
                parts.append("%s out of service" % t.name)
                continue
            now = sum(1 for p in self.passes if not p.done and p.owner is t)
            d = now - before.get(t.id, 0)
            parts.append("%s %d passes" % (t.name, now) + (" (%+d)" % d if d and self.started else ""))
        return ", ".join(parts)

    # ---- run control
    def start(self):
        with self.lock:
            if not self.field or not self.passes:
                return
            if self.started:
                self.build_plan()
            self.started = self.running = True
            self.say("Work started. " + ", ".join(
                "%s: fuel %.0f%%, battery %.0f%%, %g km/h" % (t.name, t.init_fuel, t.init_battery, t.speed_kmh)
                for t in self.tractors) + ".", "good")

    def pause(self):
        with self.lock:
            if not self.started or self.finished:
                return
            self.running = not self.running
            for t in self.tractors:
                if self.running:
                    t.last_cmd = 0.0                    # re-send its goal on the next tick
                else:
                    self.send("stop %d" % t.id)

    def configure(self, width=None, rate=None):
        with self.lock:
            if rate is not None:
                self.rate = max(1.0, min(200.0, float(rate)))
                self.send("rate %g" % self.rate)
            if width is not None and abs(float(width) - self.width_m) > 1e-9:
                self.width_m = max(2.0, min(60.0, float(width)))
                self.build_plan()

    def set_tractor(self, tid, fuel=None, battery=None, speed=None):
        """Operator settings for one tractor. Fuel and battery are starting levels: they are
        loaded into the agent now if work has not started, otherwise at the next Reset/Restart."""
        with self.lock:
            t = self.tractors[tid - 1]
            if fuel is not None:
                t.init_fuel = max(0.0, min(100.0, float(fuel)))
            if battery is not None:
                t.init_battery = max(0.0, min(100.0, float(battery)))
            if (fuel is not None or battery is not None) and not self.started:
                self.reset_agent(t)
            if speed is not None:
                t.speed_kmh = max(2.0, min(30.0, float(speed)))
                t.last_cmd = 0.0                        # re-send its goal at the new speed
                if self.field and not self.started:
                    self.assign()                       # faster tractors get a larger share
                    self.park()                         # and wait at the edge of their new blocks

    def refill(self, tid):
        with self.lock:
            t = self.tractors[tid - 1]
            self.tell_agent(t, cmd="refill")
            t.fuel_pct, t.fuel_l, t.battery_pct = 100.0, TANK_L, 100.0
            t.warned = set()
            t.hold_until = max(t.hold_until, time.time() + 0.3)
            self.say("%s refuelled and recharged to 100%%." % t.name, "good")

    # ---- the loop: check every agent's report, decide, command
    def tick(self, dt):
        with self.lock:
            now = time.time()
            for t in self.tractors:
                self.watch_contact(t, now)
            if not self.field:
                return
            if self.running:
                self.sim_time += dt * self.rate
            for t in self.tractors:
                if now - t.last_mode > 0.5:              # keep the agent's idea of its task current
                    t.last_mode = now
                    self.tell_agent(t, cmd="mode", working=t.state == "working")
                if t.lat is None or not t.gps_ok or not t.in_contact(now) or now < t.hold_until:
                    continue
                u, v = self.to_uv(t.lat, t.lon)
                if t.prev and t.state == "transit":
                    t.travel += math.hypot(u - t.prev[0], v - t.prev[1])
                t.prev = (u, v)
                t.u, t.v = u, v
                if t.state == "working" and t.seg:
                    t.seg["u1"] = max(t.pass_.a, min(t.pass_.b, u))
                if self.started and not self.finished and not t.broken:
                    self.watch_levels(t)
                if self.running and not t.broken:
                    self.advance(t, dt, now)
            if self.started and not self.finished and self.passes and all(p.done for p in self.passes):
                self.finished = True
                self.running = False
                w = self.field["width"]
                self.say("Field complete in %s. %s." % (clock(self.sim_time), ", ".join(
                    "%s: %.2f ha, fuel %.0f%%, battery %.0f%%" % (t.name, self.worked_m(t) * w / 1e4, t.fuel_pct, t.battery_pct)
                    for t in self.tractors)), "good")
            elif self.running and all(t.broken for t in self.tractors):
                self.running = False
                self.say("All tractors are out of service. Repair or refuel one to continue.", "event")

    def watch_contact(self, t, now):
        fresh = t.in_contact(now)
        if fresh and not t.contact:
            t.contact = True
            if t.lost_logged:
                t.lost_logged = False
                self.say("%s's agent is reporting again." % t.name, "good")
        elif not fresh and t.contact:
            t.contact = False
            if self.started and not self.finished:
                t.lost_logged = True
                self.say("Lost contact with %s's agent (no report for %.0f s)." % (t.name, CONTACT_TIMEOUT_S), "event")
        # Silent for too long while there is work: treat it as faulty and hand its work on.
        if (self.started and not self.finished and not t.broken and t.reports
                and now - t.last_report > FAULT_AFTER_S):
            self.breakdown(t.id, "stopped reporting for %.0f s" % FAULT_AFTER_S, "not reporting")

    def watch_levels(self, t):
        """Fuel and battery, as the tractor's agent reports them."""
        if t.fuel_pct <= EMPTY_PCT:
            self.breakdown(t.id, "ran out of fuel", "out of fuel")
        elif t.battery_pct <= EMPTY_PCT:
            self.breakdown(t.id, "ran out of battery charge", "battery flat")
        else:
            if t.fuel_pct <= LOW_PCT and "fuel" not in t.warned:
                t.warned.add("fuel")
                self.say("%s reports low fuel: %.0f%% (%.0f L) left." % (t.name, t.fuel_pct, t.fuel_l), "event")
            if t.battery_pct <= LOW_PCT and "battery" not in t.warned:
                t.warned.add("battery")
                self.say("%s reports low battery: %.0f%% left." % (t.name, t.battery_pct), "event")

    def next_pass(self, t):
        """Its own passes first, nearest next; then any passes the recovery agent gave it,
        in the order it chose (it priced them from where this tractor finishes its own)."""
        mine = [p for p in self.passes if not p.done and not p.busy and p.owner is t]
        own = [p for p in mine if not p.rescue]
        best, at_a = None, True
        if own:
            best_d = float("inf")
            for p in own:
                d_a, d_b = math.hypot(p.a - t.u, p.v - t.v), math.hypot(p.b - t.u, p.v - t.v)
                if min(d_a, d_b) < best_d:
                    best, best_d, at_a = p, min(d_a, d_b), d_a <= d_b
        elif mine:
            best = min(mine, key=lambda p: p.seq)
            at_a = math.hypot(best.a - t.u, best.v - t.v) <= math.hypot(best.b - t.u, best.v - t.v)
        if not best:
            return False
        best.busy = True
        t.pass_, t.dir, t.state = best, (1 if at_a else -1), "transit"
        t.goal = (best.a if at_a else best.b, best.v)
        t.path = self.turn_path(t, t.goal, t.dir)
        self.goto(t)
        return True

    def advance(self, t, dt, now):
        for _ in range(3):
            if t.state in ("idle", "done"):
                # Out of work while passes are open elsewhere: re-split so nobody sits idle.
                if not self.next_pass(t) and any(not p.done and not p.busy for p in self.passes):
                    self.assign()
                    self.next_pass(t)
                if not t.pass_:
                    t.state = "done" if all(p.done for p in self.passes) else "idle"
                    if t.state == "idle":
                        t.idle += dt * self.rate
                    return
            at_goal = math.hypot(t.goal[0] - t.u, t.goal[1] - t.v) <= ARRIVE_M
            if not at_goal:
                if now - t.last_cmd > 1.0:              # UDP can drop a command; repeat it
                    self.goto(t)
                return
            p = t.pass_
            if t.state == "transit":                    # reached the start of the pass: begin work
                t.state = "working"
                self.tell_agent(t, cmd="mode", working=True)
                self.seg_id += 1
                t.seg = {"id": self.seg_id, "tid": t.id, "v": p.v, "u0": t.goal[0], "u1": t.goal[0], "live": True}
                self.segments.append(t.seg)
                t.goal = (p.b if t.dir > 0 else p.a, p.v)
                self.goto(t)
                return
            # reached the far end: the pass is finished
            t.seg["u1"] = t.goal[0]
            t.seg["live"] = False
            p.done, p.busy = True, False
            t.pass_ = t.seg = None
            t.state = "idle"
            self.tell_agent(t, cmd="mode", working=False)

    # ---- events
    def breakdown(self, tid, what="broke down", reason="broken down"):
        with self.lock:
            t = self.tractors[tid - 1]
            if not self.field or t.broken:
                return
            self.send("stop %d" % t.id)
            self.tell_agent(t, cmd="mode", working=False)
            before = self.snapshot()
            note = ""
            p = t.pass_
            stop = (t.u, t.v)                            # where it stopped, from its agent's last GPS
            interrupted = None
            if t.state == "working" and p:               # keep what it finished, release the rest
                cur = max(p.a, min(p.b, t.u))
                pct = round(100 * abs(cur - t.seg["u0"]) / max(1.0, p.b - p.a))
                t.seg["u1"], t.seg["live"] = cur, False
                if t.dir > 0:
                    p.a = cur
                else:
                    p.b = cur
                if p.b - p.a < 1:
                    p.done = True
                else:
                    interrupted = p
                p.busy = False
                note = " partway through pass %d (%d%% of it done and kept)" % (p.id, pct)
            elif p:
                p.busy = False
            t.pass_ = t.seg = t.goal = None
            t.path = []
            t.broken, t.state, t.reason = True, "broken", reason
            self.say("%s %s%s." % (t.name, what, note), "event")
            self.recover(t, what, stop, interrupted, before)

    # ---- recovery agent
    def projected_route(self, t):
        """Where `t` will be when its own remaining path is done, and how much spraying and
        driving that path still takes (its current pass, then its own passes nearest first)."""
        pos, work, travel = (t.u, t.v), 0.0, 0.0
        p = t.pass_
        if p and not p.done:
            exit_u = p.b if t.dir > 0 else p.a
            if t.state == "working":
                work += abs(exit_u - t.u)
            else:
                if t.goal:
                    travel += math.hypot(t.goal[0] - t.u, t.goal[1] - t.v)
                work += p.b - p.a
            pos = (exit_u, p.v)
        rest = [q for q in self.passes if q.owner is t and not q.done and not q.busy]
        own, rescued = [q for q in rest if not q.rescue], sorted((q for q in rest if q.rescue), key=lambda q: q.seq)
        while own:
            q = min(own, key=lambda q: min(math.hypot(q.a - pos[0], q.v - pos[1]), math.hypot(q.b - pos[0], q.v - pos[1])))
            near_a = math.hypot(q.a - pos[0], q.v - pos[1]) <= math.hypot(q.b - pos[0], q.v - pos[1])
            travel += math.hypot((q.a if near_a else q.b) - pos[0], q.v - pos[1])
            work += q.b - q.a
            pos = ((q.b if near_a else q.a), q.v)
            own.remove(q)
        for q in rescued:                                # passes it already took over earlier
            near_a = math.hypot(q.a - pos[0], q.v - pos[1]) <= math.hypot(q.b - pos[0], q.v - pos[1])
            travel += math.hypot((q.a if near_a else q.b) - pos[0], q.v - pos[1])
            work += q.b - q.a
            pos = ((q.b if near_a else q.a), q.v)
        return pos, work, travel

    def recover(self, faulty, what, stop, interrupted, before):
        """Triggered by every fault. Prices each of the faulty tractor's unfinished passes for
        each healthy tractor (distance from where that tractor's own path ends to the work,
        fuel left, battery left, all from the tractor agents' reports) and assigns them with
        the Hungarian algorithm. See recovery_agent.py."""
        now = time.time()
        orphans = [q for q in self.passes if not q.done and not q.busy and q.owner is faulty]
        healthy = [x for x in self.tractors if not x.broken and x.in_contact(now)]
        rec = {"at": self.sim_time, "faulty": faulty.name, "reason": what, "cols": [x.name for x in healthy],
               "rows": [], "assignments": [], "unassigned": [], "total": 0, "rounds": 0}
        self.last_recovery = rec
        if not orphans:
            self.say("Recovery agent: %s had no unfinished passes, so nothing needs reassigning." % faulty.name, "plan")
            return
        if not healthy:
            rec["unassigned"] = ["pass %d" % q.id for q in orphans]
            self.say("Recovery agent: no healthy tractor is reporting, so %s's %d unfinished passes wait for a repair."
                     % (faulty.name, len(orphans)), "plan")
            return

        items = []
        for q in sorted(orphans, key=lambda q: q.v):
            if q is interrupted:                         # pick up exactly where the faulty tractor stopped
                far = (q.b if abs(stop[0] - q.a) <= abs(stop[0] - q.b) else q.a, q.v)
                near = (q.a if far[0] == q.b else q.b, q.v)
                items.append({"pass": q, "label": "Pass %d (rest, from where %s stopped)" % (q.id, faulty.name),
                              "options": [(near, far)], "length": q.b - q.a})
            else:
                items.append({"pass": q, "label": "Pass %d" % q.id,
                              "options": [((q.a, q.v), (q.b, q.v)), ((q.b, q.v), (q.a, q.v))], "length": q.b - q.a})
        states = []
        for x in healthy:
            finish, work, travel = self.projected_route(x)
            states.append({"finish": finish,
                           "fuel_l": x.fuel_l - work / 1000 * FUEL_WORK_L_PER_KM - travel / 1000 * FUEL_MOVE_L_PER_KM,
                           "batt_pct": x.battery_pct - work / 1000 * BATTERY_WORK_PCT_PER_KM - travel / 1000 * BATTERY_MOVE_PCT_PER_KM})

        result = recovery_agent.plan(items, states)

        self.say("Recovery agent: %s %s. Pricing its %d unfinished pass%s for %s on distance from where each one "
                 "finishes its own path, fuel left and battery left, then assigning with the Hungarian algorithm."
                 % (faulty.name, what, len(items), "" if len(items) == 1 else "es", " and ".join(x.name for x in healthy)), "plan")
        chosen = {}
        for a in sorted(result["assignments"], key=lambda a: (a["round"], a["item"])):
            item, x = items[a["item"]], healthy[a["tractor"]]
            q = item["pass"]
            q.owner, q.rescue = x, True
            self.rescue_seq += 1
            q.seq = self.rescue_seq
            chosen[a["item"]] = a["tractor"]
            rec["assignments"].append({"label": item["label"], "to": x.name, "color": x.color, "round": a["round"],
                                       "cost": round(a["cost"]), "dist": round(a["dist"]),
                                       "fuel": round(a["fuel_after"]), "batt": round(a["batt_after"])})
            self.say("%s goes to %s: %.0f m to drive after its own passes, then %.0f%% fuel and %.0f%% battery left (cost %.0f)."
                     % (item["label"], x.name, a["dist"], a["fuel_after"], a["batt_after"], a["cost"]), "plan")
        for i in result["unassigned"]:
            rec["unassigned"].append(items[i]["label"])     # stays with the faulty tractor until it is fixed
            self.say("%s is not reassigned: no healthy tractor has the fuel and battery to reach it and finish it. "
                     "It waits for a refuel or repair." % items[i]["label"], "event")
        for r, row in enumerate(result["first_round"]):
            rec["rows"].append({"label": items[r]["label"], "cells": [
                {"cost": round(cell["cost"]) if cell["ok"] else None, "chosen": chosen.get(r) == c}
                for c, cell in enumerate(row)]})
        rec["total"], rec["rounds"] = round(result["total"]), result["rounds"]
        self.say("New plan: %s. Total cost %.0f over %d Hungarian round%s. Finished work is untouched and no pass has two owners."
                 % (self.describe(before), result["total"], result["rounds"], "" if result["rounds"] == 1 else "s"), "plan")

    def repair(self, tid):
        with self.lock:
            t = self.tractors[tid - 1]
            if not t.broken:
                return
            if t.fuel_pct <= LOW_PCT or t.battery_pct <= LOW_PCT:
                self.refill(tid)                         # no point returning it to work nearly empty
            before = self.snapshot()
            t.broken, t.state, t.reason = False, "idle", ""
            self.assign()
            self.say("%s is back in service." % t.name, "good")
            self.say("New plan: the remaining passes were re-split across all running tractors (passes the recovery agent "
                     "already handed over stay where they are): %s." % self.describe(before), "plan")
            if self.started and not self.finished and not self.running:
                self.running = True

    # ---- reporting
    def worked_m(self, t):
        return sum(abs(s["u1"] - s["u0"]) for s in self.segments if s["tid"] == t.id)

    def state(self):
        with self.lock:
            now = time.time()
            f = self.field
            out = {"fieldId": self.field_id, "planId": self.plan_id, "running": self.running, "started": self.started,
                   "finished": self.finished, "simTime": self.sim_time, "width": self.width_m,
                   "rate": self.rate, "note": self.note, "log": self.log[-60:], "logCount": len(self.log),
                   "tankL": TANK_L, "lowPct": LOW_PCT, "recovery": self.last_recovery,
                   "field": None, "passes": [], "segments": [], "tractors": []}
            w = f["width"] if f else 0.0
            if f:
                out["field"] = {"ring": f["ring"], "areaHa": f["area"] / 1e4, "passWidth": w,
                                "bearing": ((90 - f["theta"] / RAD) % 180 + 180) % 180}
                for p in self.passes:
                    out["passes"].append({"id": p.id, "a": self.to_ll(p.a, p.v), "b": self.to_ll(p.b, p.v),
                                          "owner": p.owner.id if p.owner else 0, "done": p.done})
                h = w / 2
                for s in self.segments:
                    out["segments"].append({"id": s["id"], "tid": s["tid"], "live": s["live"], "poly": [
                        self.to_ll(s["u0"], s["v"] - h), self.to_ll(s["u1"], s["v"] - h),
                        self.to_ll(s["u1"], s["v"] + h), self.to_ll(s["u0"], s["v"] + h)]})
            total = sum(p.b0 - p.a0 for p in self.passes)
            worked_all = 0.0
            for t in self.tractors:
                left = [p for p in self.passes if not p.done and p.owner is t]
                left_m = sum(p.b - p.a for p in left)
                if t.state == "working" and t.seg:
                    left_m -= abs(t.seg["u1"] - t.seg["u0"])
                wm = self.worked_m(t)
                worked_all += wm
                contact = t.in_contact(now)
                out["tractors"].append({
                    "id": t.id, "name": t.name, "color": t.color, "lat": t.lat, "lon": t.lon,
                    "heading": t.heading, "speed": t.speed, "fix": t.fix, "sats": t.sats,
                    "gps": contact and t.gps_ok and t.lat is not None,
                    "contact": contact, "reportAge": (now - t.last_report) if t.reports else None,
                    "reports": t.reports, "reportLagMs": t.report_lag * 1000, "agentStatus": t.agent_status if contact else "no_contact",
                    "fuelPct": t.fuel_pct, "fuelL": t.fuel_l, "batteryPct": t.battery_pct, "odometerKm": t.odometer_m / 1000,
                    "initFuel": t.init_fuel, "initBattery": t.init_battery, "speedSet": t.speed_kmh,
                    "state": t.state, "broken": t.broken, "reason": t.reason,
                    "workedHa": wm * w / 1e4, "workedM": wm,
                    "leftM": max(0.0, left_m), "passesLeft": len(left), "travelKm": t.travel / 1000, "idleS": t.idle})
            out["workedHa"] = worked_all * w / 1e4
            out["totalHa"] = total * w / 1e4
            out["pct"] = min(100.0, 100 * worked_all / total) if total else 0.0
            return out


def clock(s):
    return "%02d:%02d" % (int(s // 3600), int(s % 3600 // 60))


FLEET = Fleet()


# --------------------------------------------------------------------------- threads
def telemetry_listener():
    """Every agent report lands here."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(TELEMETRY_ADDR)
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            FLEET.on_telemetry(json.loads(data.decode("utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            continue


def ticker():
    last = time.time()
    while True:
        time.sleep(0.1)
        now = time.time()
        try:
            FLEET.tick(min(0.5, now - last))
        except Exception as ex:                          # keep the loop alive; show what went wrong
            print("tick error:", repr(ex))
        last = now


# --------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "dashboard.html"), "rb") as fh:
                self.reply(200, fh.read(), "text/html; charset=utf-8")
        elif path == "/state":
            self.reply(200, FLEET.state())
        elif path in IMAGES:                             # AgOpenGPS's own tractor artwork
            try:
                with open(os.path.join(ICONS, IMAGES[path]), "rb") as fh:
                    self.reply(200, fh.read(), "image/png")
            except OSError:
                self.reply(404, {"error": "image not found"})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            action = self.path.split("?")[0].replace("/api/", "", 1)
            if action in ("tractor", "refill", "breakdown", "repair") and int(body["id"]) not in (1, 2, 3):
                raise ValueError("There is no tractor %s." % body["id"])
            if action == "field":
                FLEET.set_field(body["ring"])
            elif action == "clear":
                FLEET.clear_field()
            elif action == "config":
                FLEET.configure(body.get("width"), body.get("rate"))
            elif action == "tractor":
                FLEET.set_tractor(int(body["id"]), body.get("fuel"), body.get("battery"), body.get("speed"))
            elif action == "refill":
                FLEET.refill(int(body["id"]))
            elif action == "start":
                FLEET.start()
            elif action == "pause":
                FLEET.pause()
            elif action == "reset":
                FLEET.build_plan()
            elif action == "breakdown":
                FLEET.breakdown(int(body["id"]))
            elif action == "repair":
                FLEET.repair(int(body["id"]))
            else:
                return self.reply(404, {"error": "unknown action"})
            self.reply(200, {"ok": True})
        except (KeyError, ValueError, TypeError, IndexError) as ex:
            self.reply(400, {"error": str(ex)})


def main():
    threading.Thread(target=telemetry_listener, daemon=True).start()
    for tid in (1, 2, 3):                                # one on-board agent per tractor
        TractorAgent(tid).start()
    threading.Thread(target=ticker, daemon=True).start()
    print("Agents   : 3 tractor agents, reporting to UDP %s:%d" % TELEMETRY_ADDR)
    print("Bridge   : drive commands to UDP %s:%d" % BRIDGE)
    print("Dashboard: http://localhost:%d" % HTTP_PORT, flush=True)
    ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
