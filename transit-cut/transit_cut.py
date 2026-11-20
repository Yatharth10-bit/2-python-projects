"""Find the stops disconnected by a route closure in a static GTFS network."""

import argparse
import csv
import io
import json
import zipfile
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path
from html import escape


@contextmanager
def rows(feed, name, required):
    if isinstance(feed, zipfile.ZipFile):
        try:
            stream = io.TextIOWrapper(feed.open(name), encoding="utf-8-sig", newline="")
        except KeyError as exc:
            raise ValueError(f"Missing {name} in the root of the feed") from exc
    else:
        stream = (feed / name).open(encoding="utf-8-sig", newline="")
    with stream:
        reader = csv.DictReader(stream)
        missing = set(required) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{name} lacks columns: {', '.join(sorted(missing))}")
        yield reader


def build(feed):
    with rows(feed, "stops.txt", ["stop_id", "stop_name"]) as table:
        stops = {r["stop_id"]: r["stop_name"] or r["stop_id"] for r in table}
    with rows(feed, "routes.txt", ["route_id"]) as table:
        routes = {r["route_id"] for r in table}
    with rows(feed, "trips.txt", ["trip_id", "route_id"]) as table:
        trips = {r["trip_id"]: r["route_id"] for r in table}
    if not set(trips.values()) <= routes:
        raise ValueError("trips.txt references an unknown route")
    by_trip = defaultdict(list)
    with rows(feed, "stop_times.txt", ["trip_id", "stop_id", "stop_sequence"]) as table:
        for r in table:
            if r["trip_id"] not in trips or r["stop_id"] not in stops:
                raise ValueError("stop_times.txt references an unknown trip or stop")
            by_trip[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"]))
    edges = defaultdict(lambda: defaultdict(set))
    for trip, sequence in by_trip.items():
        sequence.sort()
        for (a, origin), (b, dest) in zip(sequence, sequence[1:]):
            if a == b:
                raise ValueError(f"Duplicate stop_sequence in trip {trip}")
            edges[origin][dest].add(trips[trip])
    return stops, routes, edges


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


def load(path):
    if path.is_dir():
        return build(path)
    with zipfile.ZipFile(path) as feed:
        return build(feed)


def draw(network, result, label):
    stops, _, edges = network
    origin = result["origin"]["id"]
    closed = set(result["closed_routes"])
    before = distances(edges, origin, set())
    after = distances(edges, origin, closed)
    columns = defaultdict(list)
    for stop in sorted(before, key=lambda s: (before[s], s)):
        depth = before[stop]
        if depth < 6 and len(columns[depth]) < 5:
            columns[depth].append(stop)
    positions = {}
    for depth, group in columns.items():
        for idx, stop in enumerate(group):
            positions[stop] = (78 + depth * 148, 260 + (idx - (len(group) - 1) / 2) * 62)
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 490" role="img">',
        '<title>Directed transit links and reachable stops after closure</title>',
        '<rect width="900" height="490" rx="18" fill="#101d30"/>',
        '<g font-family="system-ui, sans-serif" fill="#e6f0fa">',
        '<text x="32" y="49" font-size="30" font-weight="bold">Transit Cut</text>',
        f'<text x="32" y="81" font-size="16">{escape(label)} · Closed: {escape(", ".join(sorted(closed)))}</text>',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="4" markerHeight="4" orient="auto"><path d="M0,0 L10,5 L0,10" fill="#aab9ca"/></marker></defs>',
    ]
    for start, (x, y) in positions.items():
        for end, services in edges.get(start, {}).items():
            if end not in positions or start == end:
                continue
            end_x, end_y = positions[end]
            length = ((end_x - x) ** 2 + (end_y - y) ** 2) ** 0.5
            dx, dy = (end_x - x) / length, (end_y - y) / length
            style = 'stroke="#66d3c0"' if services - closed else 'stroke="#fc9b9b" stroke-dasharray="5 4"'
            svg.append(f'<line x1="{x + 10*dx:.1f}" y1="{y + 10*dy:.1f}" '
                       f'x2="{end_x - 12*dx:.1f}" y2="{end_y - 12*dy:.1f}" {style} '
                       'stroke-width="3" marker-end="url(#arrow)"/>')
    for stop, (x, y) in positions.items():
        color = "#fc9b9b" if stop not in after else "#f6ca82" if after[stop] > before[stop] else "#66d3c0"
        name = stops[stop]
        short_name = name if len(name) <= 17 else name[:16] + "…"
        svg.append(f'<circle cx="{x}" cy="{y}" r="8" fill="{color}"><title>{escape(name)}</title></circle>'
                   f'<text x="{x}" y="{y+23}" text-anchor="middle" font-size="13">{escape(short_name)}</text>')
    svg.extend([
        '<text x="32" y="429" font-size="15">Teal = reachable · Gold = longer path · Pink = disconnected · Dashed = closed link</text>',
        f'<text x="32" y="460" font-size="14" fill="#b5c8da">Showing {len(positions)} of {len(before)} initially reachable '
        'stops including origin. Columns show pre-closure hop distance.</text>', '</g></svg>',
    ])
    return "\n".join(svg)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", type=Path, default=Path(__file__).with_name("demo"))
    parser.add_argument("--origin", default="A", help="GTFS stop_id")
    parser.add_argument("--close", action="append", help="GTFS route_id, repeat for multiple closures")
    parser.add_argument("--list", action="store_true", help="List valid stop IDs, names, and route IDs")
    parser.add_argument("--svg", type=Path, help="write a small closure diagram")
    args = parser.parse_args()
    try:
        network = load(args.feed)
        if args.list:
            result = {"stops": network[0], "routes": sorted(network[1])}
        else:
            result = compare(network, args.origin, set(args.close or ["GREEN"]))
            if args.svg:
                label = "Synthetic demo" if args.feed == Path(__file__).with_name("demo") else "Imported GTFS"
                args.svg.write_text(draw(network, result, label), encoding="utf-8")
    except (ValueError, OSError, zipfile.BadZipFile, csv.Error) as exc:
        parser.exit(2, f"Transit Cut: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
