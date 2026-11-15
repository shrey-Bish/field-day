#!/usr/bin/env python3
"""Tractor agent: the on-board software of one tractor.

Each agent runs on its own thread and talks to the rest of the system only over UDP:

    its GPS receiver  --$PANDA, 10 Hz-->  agent   (port 10001 / 10002 / 10003)
    agent  --telemetry JSON, every fix-->  coordinator   (port 10200)
    coordinator  --JSON commands------->  agent   (port 10301 / 10302 / 10303)

The agent owns what only the tractor can know: where its GPS says it is, how much fuel is in
the tank and how much battery charge is left. It reports all of that with every GPS fix (and
at least twice a second without one), so the coordinator can watch every tractor all the time.

Commands it accepts:
    {"cmd": "reset", "fuel_pct": 80, "battery_pct": 100}   fill to these levels, clear the odometer
    {"cmd": "refill"}                                       full tank, full charge
    {"cmd": "set", "fuel_pct": 0}                           change a level now (operator, mid-run)
    {"cmd": "mode", "working": true}                        implement in the ground or not
"""
import json
import math
import select
import socket
import threading
import time

GPS_PORTS = {1: 10001, 2: 10002, 3: 10003}
AGENT_PORTS = {1: 10301, 2: 10302, 3: 10303}
TELEMETRY_ADDR = ("127.0.0.1", 10200)

TANK_L = 200.0                    # fuel tank size
FUEL_WORK_L_PER_KM = 10.0         # implement in the ground
FUEL_MOVE_L_PER_KM = 4.0          # driving between passes
BATTERY_WORK_PCT_PER_KM = 1.5     # guidance, implement control and hydraulics
BATTERY_MOVE_PCT_PER_KM = 0.8
LOW_PCT = 20.0                    # "running low" warning level
EMPTY_PCT = 2.0                   # at or below this the tractor must not carry on
GPS_TIMEOUT_S = 2.0


def parse_panda(line):
    """$PANDA,time,lat,N/S,lon,E/W,fix,sats,hdop,alt,age,speedKnots,heading*10,roll*10,pitch,yaw*CS"""
    if not line.startswith("$PANDA"):
        return None
    body, star, cs = line[1:].partition("*")
    if star:
        calc = 0
        for ch in body:
            calc ^= ord(ch)
        try:
            if int(cs[:2], 16) != calc:
                return None
        except ValueError:
            return None
    f = body.split(",")
    if len(f) < 13:
        return None
    try:
        def dm(s):                       # DDMM.MMMMM -> degrees
            v = float(s)
            d = int(v / 100)
            return d + (v - d * 100) / 60
        lat = dm(f[2]) * (-1 if f[3] == "S" else 1)
        lon = dm(f[4]) * (-1 if f[5] == "W" else 1)
        raw = int(f[12])
        return {"lat": lat, "lon": lon, "fix": int(f[6]), "sats": int(f[7]), "speed": float(f[11]) * 1.852,
                "heading": None if raw == 65535 else raw / 10.0}
    except ValueError:
        return None


class TractorAgent(threading.Thread):
    def __init__(self, tid):
        super().__init__(daemon=True, name="tractor-agent-%d" % tid)
        self.id = tid
        self.lat = self.lon = None
        self.heading = 0.0
        self.speed = 0.0
        self.fix = 0
        self.sats = 0
        self.last_gps = 0.0
        self.fuel_l = TANK_L
        self.battery_pct = 100.0
        self.odometer_m = 0.0
        self.working = False
        self.seq = 0
        self.last_report = 0.0
        self.prev = None                 # last position used for distance
        self.hold_until = 0.0            # ignore movement until then (the tractor was just re-placed)

    # ---- inputs
    def on_gps(self, data, out):
        for line in data.decode("ascii", "ignore").splitlines():
            fix = parse_panda(line.strip())
            if not fix:
                continue                 # steer/machine module packets and anything else
            now = time.time()
            if self.prev and now >= self.hold_until:
                self.consume(self.metres(self.prev, (fix["lat"], fix["lon"])))
            self.prev = (fix["lat"], fix["lon"])
            self.lat, self.lon, self.fix, self.sats, self.speed = fix["lat"], fix["lon"], fix["fix"], fix["sats"], fix["speed"]
            if fix["heading"] is not None:
                self.heading = fix["heading"]
            self.last_gps = now
            self.report(out)

    def on_command(self, data):
        try:
            msg = json.loads(data.decode("utf-8"))
        except ValueError:
            return
        cmd = msg.get("cmd")
        if cmd == "reset":
            self.fuel_l = TANK_L * max(0.0, min(100.0, float(msg.get("fuel_pct", 100)))) / 100
            self.battery_pct = max(0.0, min(100.0, float(msg.get("battery_pct", 100))))
            self.odometer_m = 0.0
            self.working = False
            self.prev = None
            self.hold_until = time.time() + 0.5
        elif cmd == "refill":
            self.fuel_l, self.battery_pct = TANK_L, 100.0
        elif cmd == "set":                                   # operator changes a level mid-run
            if "fuel_pct" in msg:
                self.fuel_l = TANK_L * max(0.0, min(100.0, float(msg["fuel_pct"]))) / 100
            if "battery_pct" in msg:
                self.battery_pct = max(0.0, min(100.0, float(msg["battery_pct"])))
        elif cmd == "mode":
            self.working = bool(msg.get("working"))

    # ---- the tractor's own bookkeeping
    @staticmethod
    def metres(a, b):
        d_n = (b[0] - a[0]) * 111320.0
        d_e = (b[1] - a[1]) * 111320.0 * math.cos(a[0] * math.pi / 180)
        return math.hypot(d_n, d_e)

    def consume(self, d_m):
        if d_m <= 0:
            return
        km = d_m / 1000
        self.odometer_m += d_m
        self.fuel_l = max(0.0, self.fuel_l - km * (FUEL_WORK_L_PER_KM if self.working else FUEL_MOVE_L_PER_KM))
        self.battery_pct = max(0.0, self.battery_pct - km * (BATTERY_WORK_PCT_PER_KM if self.working else BATTERY_MOVE_PCT_PER_KM))

    def status(self, gps_ok):
        fuel_pct = 100 * self.fuel_l / TANK_L
        if not gps_ok:
            return "no_gps"
        if fuel_pct <= EMPTY_PCT:
            return "out_of_fuel"
        if self.battery_pct <= EMPTY_PCT:
            return "battery_flat"
        if fuel_pct <= LOW_PCT:
            return "low_fuel"
        if self.battery_pct <= LOW_PCT:
            return "low_battery"
        return "ok"

    def report(self, out):
        now = time.time()
        gps_ok = self.lat is not None and now - self.last_gps <= GPS_TIMEOUT_S
        self.seq += 1
        self.last_report = now
        msg = {"type": "telemetry", "id": self.id, "seq": self.seq, "sent": now,
               "gps_ok": gps_ok, "lat": self.lat, "lon": self.lon, "heading": self.heading,
               "speed": self.speed if gps_ok else 0.0, "fix": self.fix, "sats": self.sats,
               "fuel_l": round(self.fuel_l, 2), "fuel_pct": round(100 * self.fuel_l / TANK_L, 2),
               "battery_pct": round(self.battery_pct, 2), "odometer_m": round(self.odometer_m, 1),
               "working": self.working, "status": self.status(gps_ok)}
        try:
            out.sendto(json.dumps(msg).encode("utf-8"), TELEMETRY_ADDR)
        except OSError:
            pass

    def run(self):
        gps = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        gps.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        gps.bind(("127.0.0.1", GPS_PORTS[self.id]))
        cmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        cmd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        cmd.bind(("127.0.0.1", AGENT_PORTS[self.id]))
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while True:
            try:
                ready, _, _ = select.select([gps, cmd], [], [], 0.25)
                for s in ready:
                    data, _ = s.recvfrom(4096)
                    if s is gps:
                        self.on_gps(data, out)
                    else:
                        self.on_command(data)
                if time.time() - self.last_report > 0.5:     # heartbeat even with no GPS
                    self.report(out)
            except OSError:
                continue
            except Exception as ex:                          # an agent must never die silently
                print("agent %d error: %r" % (self.id, ex))
