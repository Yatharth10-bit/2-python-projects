"""Run this file with Python 3 to try SeatRace and Transit Cut offline."""

import sqlite3
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier


# SeatRace: one database connection per student, competing for the same seat.
def label(value):
    value = value.strip()
    if not value:
        raise ValueError("slot and student names must contain text")
    return value


@contextmanager
def desk(path):
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS slots (
                name TEXT PRIMARY KEY, capacity INTEGER NOT NULL CHECK(capacity > 0)
            );
            CREATE TABLE IF NOT EXISTS bookings (
                ticket INTEGER PRIMARY KEY AUTOINCREMENT,
                slot TEXT NOT NULL REFERENCES slots(name),
                student TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('confirmed','waiting')),
                UNIQUE(slot, student)
            );
            CREATE INDEX IF NOT EXISTS queue ON bookings(slot, state, ticket);
        """)
        yield db
    finally:
        db.close()


def capacity_for(db, slot):
    row = db.execute("SELECT capacity FROM slots WHERE name=?", (slot,)).fetchone()
    if row is None:
        raise ValueError(f"unknown slot: {slot}")
    return row[0]


def change(db, action, slot, student=None, capacity=None):
    slot = label(slot)
    if action != "create":
        student = label(student)
    db.execute("BEGIN IMMEDIATE")
    try:
        if action == "create":
            if capacity is None or capacity < 1:
                raise ValueError("capacity must be positive")
            if db.execute("SELECT 1 FROM slots WHERE name=?", (slot,)).fetchone():
                raise ValueError("slot already exists")
            db.execute("INSERT INTO slots VALUES (?, ?)", (slot, capacity))
            result = {"slot": slot, "capacity": capacity}
        elif action == "book":
            limit = capacity_for(db, slot)
            old = db.execute("SELECT state FROM bookings WHERE slot=? AND student=?",
                             (slot, student)).fetchone()
            if old:
                result = {"student": student, "state": old[0], "repeat": True}
            else:
                used = db.execute("SELECT COUNT(*) FROM bookings WHERE slot=? AND state='confirmed'",
                                  (slot,)).fetchone()[0]
                state = "confirmed" if used < limit else "waiting"
                db.execute("INSERT INTO bookings(slot,student,state) VALUES(?,?,?)",
                           (slot, student, state))
                result = {"student": student, "state": state, "repeat": False}
        elif action == "cancel":
            capacity_for(db, slot)
            row = db.execute("SELECT state FROM bookings WHERE slot=? AND student=?",
                             (slot, student)).fetchone()
            if row is None:
                raise ValueError("student has no booking for this slot")
            db.execute("DELETE FROM bookings WHERE slot=? AND student=?", (slot, student))
            promoted = None
            if row[0] == "confirmed":
                next_row = db.execute("""
                    SELECT ticket, student FROM bookings
                    WHERE slot=? AND state='waiting' ORDER BY ticket LIMIT 1
                """, (slot,)).fetchone()
                if next_row:
                    promoted = next_row["student"]
                    db.execute("UPDATE bookings SET state='confirmed' WHERE ticket=?",
                               (next_row["ticket"],))
            result = {"cancelled": student, "promoted": promoted}
        else:
            raise ValueError("unknown change")
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise


def status(db, slot):
    slot = label(slot)
    limit = capacity_for(db, slot)
    rows = db.execute("SELECT student,state FROM bookings WHERE slot=? ORDER BY ticket",
                      (slot,)).fetchall()
    return {"slot": slot, "capacity": limit,
            "confirmed": [r["student"] for r in rows if r["state"] == "confirmed"],
            "waiting": [r["student"] for r in rows if r["state"] == "waiting"]}


def race(students, seats):
    if not (1 <= students <= 24 and 1 <= seats <= 24):
        raise ValueError("students and seats must each be between 1 and 24")
    names = [f"student-{i:02}" for i in range(1, students + 1)]
    start = Barrier(students, timeout=15)
    with TemporaryDirectory() as folder:
        path = Path(folder) / "race.sqlite3"
        with desk(path) as db:
            change(db, "create", "robotics", capacity=seats)

            def claim(name):
                with desk(path) as worker:
                    start.wait()
                    change(worker, "book", "robotics", name)

            with ThreadPoolExecutor(max_workers=students) as pool:
                list(pool.map(claim, names))
            before = status(db, "robotics")
            cancellation = change(db, "cancel", "robotics", before["confirmed"][0])
            after = status(db, "robotics")
    waiting = before["waiting"]
    expected_promotion = waiting[0] if waiting else None
    expected_confirmed = before["confirmed"][1:] + waiting[:1]
    checks = {
        "all_claims_accounted_for": sorted(before["confirmed"] + waiting) == names,
        "confirmed_at_capacity_or_below": len(before["confirmed"]) == min(students, seats),
        "first_waiter_promoted": cancellation["promoted"] == expected_promotion,
        "queue_order_preserved": after["waiting"] == waiting[1:],
        "confirmed_after_cancellation": after["confirmed"] == expected_confirmed,
    }
    if not all(checks.values()):
        raise RuntimeError(f"booking invariant failed: {checks}")
    return {
        "summary": (f"{students} simultaneous claims for {seats} seat(s): "
                    f"{len(before['confirmed'])} confirmed, {len(waiting)} waiting. "
                    f"Promoted after cancellation: {cancellation['promoted'] or 'nobody'}."),
        "before": before, "cancellation": cancellation, "after": after, "checks": checks,
    }


# Transit Cut: compare shortest paths before and after a simulated route closure.
def distances(edges, origin, closed):
    hops = {origin: 0}
    queue = deque([origin])
    while queue:
        stop = queue.popleft()
        for dest, serving_routes in edges.get(stop, {}).items():
            if dest not in hops and serving_routes - closed:
                hops[dest] = hops[stop] + 1
                queue.append(dest)
    return hops


def compare(network, origin, closed):
    stops, routes, edges = network
    if origin not in stops:
        raise ValueError(f"Unknown origin stop: {origin}")
    if closed - routes:
        raise ValueError("Unknown route(s): " + ", ".join(sorted(closed - routes)))
    before = distances(edges, origin, set())
    after = distances(edges, origin, closed)
    return {
        "origin": {"id": origin, "name": stops[origin]},
        "closed_routes": sorted(closed),
        "reachable_before": len(before) - 1,
        "reachable_after": len(after) - 1,
        "lost_stops": [{"id": s, "name": stops[s]} for s in sorted(before.keys() - after.keys())],
        "longer_paths": [
            {"id": s, "name": stops[s], "before_hops": before[s], "after_hops": after[s]}
            for s in sorted(after) if after[s] > before[s]
        ],
    }


def standalone_demo():
    """Use the same fictional routes when only this Python file was downloaded."""
    stops = {"A": "Library", "B": "Clock Tower", "C": "West Gate", "D": "Observatory",
             "E": "Market", "F": "Lake Walk", "X": "Depot"}
    routes = {"GREEN": "ABCD", "BLUE": "AB", "GOLD": "BEFC", "DEPOT": "XA"}
    edges = defaultdict(lambda: defaultdict(set))
    for route, sequence in routes.items():
        for origin, dest in zip(sequence, sequence[1:]):
            edges[origin][dest].add(route)
    return stops, set(routes), edges


def main():
    print("SeatRace | Campus lab booking")
    bookings = race(students=8, seats=1)
    print(bookings["summary"])

    print("\nTransit Cut | Green Line closure (fictional bus network)")
    closure = compare(standalone_demo(), "A", {"GREEN"})
    print(f"Stops reachable from Library: {closure['reachable_before']} before, "
          f"{closure['reachable_after']} after.")
    for stop in closure["lost_stops"]:
        print(f"Lost connection: {stop['name']}")
    for stop in closure["longer_paths"]:
        print(f"Detour to {stop['name']}: {stop['before_hops']} hops -> {stop['after_hops']} hops")


if __name__ == "__main__":
    main()
