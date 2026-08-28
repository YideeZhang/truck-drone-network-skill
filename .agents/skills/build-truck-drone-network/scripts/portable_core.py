"""Portable preprocessing primitives; no project Model or routing-solver imports."""
from __future__ import annotations

import csv
import hashlib
import heapq
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window


class GateError(RuntimeError):
    """A failed scientific or input gate; never replace with invented data."""


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, rows, fields=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    fields = list(fields or (list(rows[0]) if rows else []))
    if not fields:
        raise GateError(f"Empty table requires a schema: {path.name}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":"), allow_nan=False)
                             if isinstance(value, (list, dict, tuple)) else value
                             for key, value in row.items()})


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def stable_id(prefix, *parts):
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    return prefix + hashlib.sha256(encoded).hexdigest()[:24]


def safe_path(root, configured):
    if not configured:
        raise GateError("A required path has not been configured")
    root = Path(root).resolve()
    result = (root / configured).resolve()
    if not result.is_relative_to(root):
        raise GateError("Configured path escapes project root")
    return result


def goods_kg(population, rule):
    return str((Decimal(str(population)) * Decimal(str(rule))).quantize(
        Decimal("0.001"), rounding=ROUND_HALF_UP))


def demand_energy(population, config):
    return (float(population) / config["people_per_household"] *
            config["annual_household_kwh"] / config["annual_hours"] *
            config["outage_hours"] * config["critical_fraction"])


def truck_energy(distance_m, ascent_m, config):
    return (config["flat_kwh_per_km"] * distance_m / 1000 +
            config["mass_kg"] * config["gravity_m_s2"] * ascent_m /
            (3.6e6 * config["uphill_efficiency"]))


def smooth_profile(values):
    """Centered three-sample mean; preserve shared endpoint heights exactly."""
    result = np.asarray(values, dtype=float).copy()
    if len(result) > 2:
        result[1:-1] = (result[:-2] + result[1:-1] + result[2:]) / 3
    return result


def slope_metrics(distances, elevations):
    """Within ONE physical edge only; no cross-edge endpoint jumps."""
    dd = np.diff(np.asarray(distances, dtype=float))
    dz = np.diff(np.asarray(elevations, dtype=float))
    if not len(dd) or not np.isfinite(dd).all() or not np.isfinite(dz).all() or (dd <= 0).any():
        raise GateError("Invalid physical elevation profile")
    gradient = np.abs(dz / dd)
    return float(gradient.max()), float(np.abs(dz).sum() / dd.sum())


class DEMSampler:
    """Windowed bilinear sampling; no zero/sea-level replacement and no gap filling."""

    def __init__(self, path, projected_crs, vertical_unit, datum):
        if vertical_unit != "m" or not datum or datum == "REQUIRED":
            raise GateError("DEM needs explicit metre units and a verified vertical datum")
        self.dataset = rasterio.open(path)
        if self.dataset.crs is None:
            raise GateError("DEM has no CRS")
        self.transformer = Transformer.from_crs(projected_crs, self.dataset.crs, always_xy=True)

    def close(self):
        self.dataset.close()

    def sample(self, points):
        coords = np.asarray(points, dtype=float)
        x, y = self.transformer.transform(coords[:, 0], coords[:, 1])
        col, row = (~self.dataset.transform) * (np.asarray(x), np.asarray(y))
        col, row = np.asarray(col) - .5, np.asarray(row) - .5
        c0, r0 = np.floor(col).astype(int), np.floor(row).astype(int)
        # Nodata and outside coverage are scientific blockers. Zero is valid.
        if (c0 < 0).any() or (r0 < 0).any() or (c0 + 1 >= self.dataset.width).any() or (r0 + 1 >= self.dataset.height).any():
            raise GateError("DEM does not cover a road/drone corridor plus interpolation margin")
        result = np.empty(len(points), dtype=float)
        # Spatial tiles bound memory even for country-wide diagonals.
        groups = {}
        for i, (r, c) in enumerate(zip(r0, c0)):
            groups.setdefault((int(r) // 256, int(c) // 256), []).append(i)
        for indices in groups.values():
            idx = np.asarray(indices)
            rr, cc = r0[idx], c0[idx]
            top, left = int(rr.min()), int(cc.min())
            data = self.dataset.read(1, window=Window(left, top, int(cc.max())-left+2,
                                                      int(rr.max())-top+2), masked=True)
            a, b = rr-top, cc-left
            values = np.ma.vstack([data[a,b], data[a,b+1], data[a+1,b], data[a+1,b+1]])
            if np.ma.getmaskarray(values).any() or not np.isfinite(values).all():
                raise GateError("DEM nodata/nonfinite value on required geometry")
            dx, dy = col[idx]-cc, row[idx]-rr
            weights = np.vstack([(1-dx)*(1-dy), dx*(1-dy), (1-dx)*dy, dx*dy])
            result[idx] = np.sum(np.asarray(values)*weights, axis=0)
        return result

    def line_profile(self, geometry, interval):
        count = max(1, math.ceil(geometry.length / interval))
        distances = np.linspace(0, geometry.length, count + 1)
        coords = [geometry.interpolate(float(d)).coords[0][:2] for d in distances]
        return distances, self.sample(coords)


def shortest_paths(arcs, origin, blockers=(), distance_first=False):
    """Canonical Dijkstra: time, distance, ordered arc-ID sequence.

    A blocker can be a reached destination but cannot be expanded. Thus one
    run computes all strict-direct destinations for one origin without an
    O(N^2) loop of graph copies. Positive costs prevent zero-cost cycles.
    """
    adjacency = {}
    for arc in arcs:
        if float(arc["time_min"]) <= 0 or float(arc["distance_m"]) <= 0:
            raise GateError("Dijkstra received a nonpositive physical/logical arc")
        adjacency.setdefault(arc["from_node"], []).append(arc)
    for values in adjacency.values():
        values.sort(key=lambda r: r["arc_id"])
    blockers = set(blockers) - {origin}
    first, second = ("distance_m", "time_min") if distance_first else ("time_min", "distance_m")
    zero = (Decimal(0), Decimal(0), ())
    best, queue = {origin: zero}, [(*zero, origin)]
    while queue:
        primary, secondary, ids, node = heapq.heappop(queue)
        if best.get(node) != (primary, secondary, ids):
            continue
        if node in blockers:
            continue
        for arc in adjacency.get(node, ()):
            label = (primary + Decimal(str(arc[first])), secondary + Decimal(str(arc[second])),
                     ids + (arc["arc_id"],))
            target = arc["to_node"]
            if target not in best or label < best[target]:
                best[target] = label
                heapq.heappush(queue, (*label, target))
    return best


def flight_cost(distance_m, start_z, end_z, max_z, payload, config):
    cruise_z = max(max_z, start_z, end_z) + config["clearance_m"]
    up = (cruise_z-start_z) / config["climb_m_s"]
    down = (cruise_z-end_z) / config["descent_m_s"]
    cruise = distance_m / config["cruise_m_s"]
    phase = config["states"][payload]
    nominal = config["nominal_battery_kwh"]
    horizontal_rate = nominal / phase["depletion_range_km"]
    hover_kw = nominal / (phase["depletion_hover_min"]/60)
    vertical = hover_kw * (up + down) / 3600
    energy = horizontal_rate * distance_m / 1000 + vertical
    return {"distance_m": distance_m, "time_s": up+cruise+down,
            "climb_time_s": up, "cruise_time_s": cruise, "descent_time_s": down,
            "cruise_altitude_m": cruise_z, "energy_kwh": energy,
            "vertical_energy_kwh": vertical,
            "horizontal_energy_kwh": horizontal_rate*distance_m/1000,
            "payload_kg": phase["payload_kg"],
            "arc_energy_necessary_feasible": energy <= nominal*(1-config["reserve_fraction"]),
            "platform_altitude_necessary_feasible": cruise_z <= config["maximum_altitude_m"],
            "reserve_fraction": config["reserve_fraction"]}


def midrank(values, reference):
    reference = np.sort(np.asarray(reference, dtype=float))
    if not len(reference) or not np.isfinite(reference).all():
        raise GateError("Empty or invalid frozen rank reference")
    values = np.asarray(values, dtype=float)
    return (np.searchsorted(reference, values, side="left") +
            np.searchsorted(reference, values, side="right")) / (2*len(reference))


def stable_uniform(*identity):
    return int(stable_id("", *identity), 16) / 2**96
