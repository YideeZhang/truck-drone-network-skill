"""Provider-neutral source noding, service insertion, and one-time DEM costs."""
from __future__ import annotations

import json
import math
from collections import defaultdict

import geopandas as gpd
import networkx as nx
import numpy as np
from pyproj import CRS
from shapely.geometry import Point
from shapely.ops import substring
from shapely.strtree import STRtree

import road_core
from portable_core import GateError, safe_path, slope_metrics, smooth_profile, stable_id, truck_energy


def canonical(root, profile, boundary, provider):
    cfg = profile["roads"][provider]
    source = gpd.read_file(safe_path(root, cfg["path"]), layer=cfg.get("layer"))
    if source.crs is None or source.empty:
        raise GateError("Road source is empty or has no CRS")
    metric = source.to_crs(profile["region"]["processing_crs"])
    clip = boundary.union_all().buffer(profile["roads"]["context_buffer_m"])
    metric = metric.loc[metric.intersects(clip)].reset_index(drop=True)
    f = cfg["fields"]
    for required in ["id", "class"]:
        if not f.get(required) or f[required] not in metric:
            raise GateError(f"Road source needs a real {required} field")
        if metric[f[required]].isna().any() or (metric[f[required]].astype(str).str.strip()=="").any():
            raise GateError(f"Road source has missing {required} evidence; do not fabricate values")
    fields = {k:f.get(k) if f.get(k) in metric else None
              for k in ["id","class","name","ref","access","oneway","layer","bridge","tunnel"]}
    # Existing mature noding implementation, with STRtree rather than quadratic lineage scans.
    road_core.EXCLUDED_HIGHWAYS = set(profile["roads"]["excluded_classes"])
    road_core.EXCLUDED_ACCESS = set(profile["roads"]["excluded_access"])
    records = road_core.line_records(metric, clip, fields)
    if not records:
        raise GateError("no_valid_roads")
    segments = road_core.noded_segments(records, profile["roads"]["minimum_segment_m"])
    nodes, edges = road_core.build_graph_tables(segments, CRS(profile["region"]["processing_crs"]),
                        set(cfg["secondary_classes"]), profile["roads"]["coordinate_decimals"])
    name = cfg["provider_name"]
    prefix = "OSM" if provider == "osm" else "LOCAL"
    for frame in [nodes, edges]:
        frame["source_provider"] = name
        frame["geometry_provider"] = name
        frame["lineage_json"] = frame["lineage_json"].map(lambda s:s.replace('"OSM"', json.dumps(name)))
        for col in ["node_id","road_node_id","edge_id","road_edge_id","from_node","to_node","component_id"]:
            if col in frame:
                frame[col] = frame[col].str.replace("OSM_", prefix+"_", regex=False)
    edges["attribute_provider"] = name + "_direct"
    edges["source_url"] = cfg["source_url"]
    edges["source_license"] = cfg["license"]
    edges["direction_evidence"] = "source_direction_retained_not_heavy_truck_certification"
    nodes["source_status"] = name + "_direct_topology_node"
    for col in ["class_evidence","classification_evidence"]:
        edges[col] = edges[col].str.replace("OSM_direct",name+"_direct",regex=False)
    return nodes, edges


def insert_services(nodes, edges, services, config):
    """Insert points on real lines, never add a centroid-to-road connector."""
    edges = edges.sort_values("edge_id").reset_index(drop=True)
    graph = nx.Graph()
    for r in edges.itertuples():
        graph.add_edge(r.from_node, r.to_node)
    node_rows = {r["node_id"]:r for r in nodes.to_dict("records")}
    junctions = sorted(n for n, d in graph.degree() if d >= 3)
    jgeoms = [node_rows[n]["geometry"] for n in junctions]
    jtree = STRtree(jgeoms) if jgeoms else None
    tree = STRtree(list(edges.geometry))
    splits, snaps = defaultdict(list), []
    for service in sorted(services, key=lambda r:r["terminal_id"]):
        point = Point(service["access_reference_x_m"],service["access_reference_y_m"])
        nearest = tree.query_nearest(point, all_matches=True)
        index = min(int(i) for i in nearest)
        edge = edges.iloc[index]
        offset = float(edge.geometry.project(point))
        snapped = edge.geometry.interpolate(offset)
        junction = False
        node_id = ""
        if jtree is not None and config["junction_priority"]:
            ji = min(int(i) for i in jtree.query_nearest(point, all_matches=True))
            jp = jgeoms[ji]
            if (point.distance(jp) <= config["junction_radius_m"] and
                point.distance(jp) <= point.distance(snapped)+config["junction_extra_m"] and
                point.distance(jp) <= config["access_threshold_m"]):
                snapped, node_id, junction = jp, junctions[ji], True
        if not node_id:
            if offset <= 1e-7:
                node_id = edge.from_node
                snapped = node_rows[node_id]["geometry"]
            elif edge.length_m-offset <= 1e-7:
                node_id = edge.to_node
                snapped = node_rows[node_id]["geometry"]
            else:
                # Deterministic numeric precision, never a user-sized snap tolerance.
                offset = round(offset, config["coordinate_decimals"])
                offset = min(edge.length_m, max(0., offset))
                if offset <= 0:
                    node_id, snapped = edge.from_node, node_rows[edge.from_node]["geometry"]
                elif offset >= edge.length_m:
                    node_id, snapped = edge.to_node, node_rows[edge.to_node]["geometry"]
                else:
                    snapped = edge.geometry.interpolate(offset)
                    node_id = stable_id("ACCESS_", edge.edge_id, offset)
                    splits[index].append((offset, node_id))
                    node_rows[node_id] = {"node_id":node_id,"x_m":snapped.x,"y_m":snapped.y,
                                          "geometry":snapped, "source_provider":edge.source_provider}
        distance = float(point.distance(snapped))
        snaps.append({**service, "truck_anchor_id":node_id, "snapped_node_id":node_id,
                      "anchor_coordinate_id":"ANCHOR::"+service["terminal_id"],
                      "anchor_x_m":snapped.x,"anchor_y_m":snapped.y,"snap_distance_m":distance,
                      "distance_covered":distance <= config["access_threshold_m"]+1e-9,
                      "junction_preferred":junction,
                      "anchor_semantics":"real_road_access_not_customer_delivery_coordinate"})
    result = []
    for index, row in enumerate(edges.to_dict("records")):
        if not splits[index]:
            result.append(row)
            continue
        intervals = [(0.,row["from_node"])] + sorted(set(splits[index])) + [(row["length_m"],row["to_node"])]
        for number, ((a,u),(b,v)) in enumerate(zip(intervals[:-1], intervals[1:])):
            if b <= a or u == v:
                raise GateError("Service splitting made a zero-length physical edge")
            geom = substring(row["geometry"], a, b)
            edge_id = stable_id("EDGE_", row["edge_id"], a, b)
            result.append({**row, "edge_id":edge_id,"road_edge_id":edge_id,
                           "parent_edge_id":row["edge_id"],"from_node":u,"to_node":v,
                           "length_m":geom.length,"geometry":geom,"geometry_wkt_m":geom.wkt,
                           "geometry_wkt":geom.wkt,
                           "lineage_json":json.dumps({"parent":json.loads(row["lineage_json"]),
                                                      "source_chainage_m":[a,b]},sort_keys=True)})
    return node_rows, sorted(result,key=lambda r:r["edge_id"]), snaps


def fill_secondary_gaps(edges, maximum_m):
    """Only degree-two residential chains between secondary endpoints qualify."""
    by_id = {r["edge_id"]:r for r in edges}
    incident = defaultdict(list)
    for r in edges:
        incident[r["from_node"]].append(r["edge_id"])
        incident[r["to_node"]].append(r["edge_id"])
    changed = True
    while changed:
        changed = False
        seeds = {n for n, values in incident.items() if any(by_id[e]["final_road_class"] == "secondary" for e in values)}
        for start in sorted(seeds):
            for first in sorted(incident[start]):
                if by_id[first]["final_road_class"] != "residential":
                    continue
                chain, current, eid, length = [], start, first, 0.
                while eid not in chain:
                    r = by_id[eid]; chain.append(eid); length += r["length_m"]
                    target = r["to_node"] if r["from_node"] == current else r["from_node"]
                    if target in seeds:
                        if target != start and length <= maximum_m and all(by_id[e]["final_road_class"] == "residential" for e in chain):
                            for e in chain:
                                by_id[e]["final_road_class"] = "secondary"
                                by_id[e]["classification_evidence"] = "profile_secondary_gap_fill_research_class_not_observed_upgrade"
                            changed = True
                        break
                    if len(incident[target]) != 2 or length > maximum_m:
                        break
                    nxt = next(e for e in incident[target] if e != eid)
                    if by_id[nxt]["final_road_class"] != "residential":
                        break
                    current, eid = target, nxt
    return edges


def physical_costs(edges, sampler, config):
    profiles, arcs = [], []
    for row in edges:
        distances, raw = sampler.line_profile(row["geometry"], config["dem_interval_m"])
        z = smooth_profile(raw)
        dz = np.diff(z)
        up, down = float(np.maximum(dz,0).sum()), float(np.maximum(-dz,0).sum())
        smax, smean = slope_metrics(distances,z)
        profiles.append({"edge_id":row["edge_id"],"sample_distances_m_json":distances.tolist(),
                         "smoothed_elevations_m_json":z.tolist(), "raw_elevations_m_json":raw.tolist(),
                         "smax_abs_gradient":smax,"smean_abs_gradient":smean})
        speed = config["speeds_kmh"][row["final_road_class"]]
        for forward in [True,False]:
            if not row["forward_allowed" if forward else "reverse_allowed"]:
                continue
            a,b = (row["from_node"],row["to_node"]) if forward else (row["to_node"],row["from_node"])
            ascent, descent = (up,down) if forward else (down,up)
            arc_id = row["edge_id"] + ("_F" if forward else "_R")
            reverse = row["edge_id"] + ("_R" if forward else "_F") if row["forward_allowed"] and row["reverse_allowed"] else ""
            time_min = row["length_m"] / (speed*1000/60)
            energy = truck_energy(row["length_m"],ascent,config)
            arcs.append({"arc_id":arc_id,"directed_physical_arc_id":arc_id,"edge_id":row["edge_id"],
                         "reverse_directed_physical_arc_id":reverse,"from_node":a,"to_node":b,
                         "traversal_direction":1 if forward else -1,"distance_m":row["length_m"],
                         "time_min":time_min,"travel_time_min":time_min,"truck_energy_kwh":energy,
                         "cumulative_ascent_m":ascent,"cumulative_descent_m":descent,
                         "nominal_speed_kmh":speed,"base_final_road_class":row["final_road_class"]})
    return profiles, sorted(arcs,key=lambda r:r["arc_id"])


def assess(snaps, arcs, depot, fallback_ratio):
    graph = nx.DiGraph()
    graph.add_edges_from((r["from_node"],r["to_node"]) for r in arcs)
    d = next(r for r in snaps if r["terminal_id"] == depot)
    attached = d["distance_covered"] and d["truck_anchor_id"] in graph
    outward = nx.descendants(graph,d["truck_anchor_id"]) | {d["truck_anchor_id"]} if attached else set()
    inward = nx.ancestors(graph,d["truck_anchor_id"]) | {d["truck_anchor_id"]} if attached else set()
    bad = []
    for row in snaps:
        row["depot_connected"] = row["truck_anchor_id"] in outward & inward
        row["truck_service_status"] = "active" if row["distance_covered"] and row["depot_connected"] else "deferred_road_access"
        if row["terminal_id"] != depot and row["truck_service_status"] != "active":
            bad.append(row["terminal_id"])
    count = len(snaps)-1
    if count <= 0:
        raise GateError("No external population component; do not split the Depot artificially")
    return {"depot_terminal_id":depot,"depot_attached":bool(attached),"demand_denominator":count,
            "inaccessible_count":len(bad),"inaccessible_ratio":len(bad)/count,
            "inaccessible_ids":sorted(bad),"qualified":bool(attached and len(bad)/count < fallback_ratio),
            "rule":"distance > 200 m OR not bidirectionally Depot-connected; fail ratio >= 0.20"}
