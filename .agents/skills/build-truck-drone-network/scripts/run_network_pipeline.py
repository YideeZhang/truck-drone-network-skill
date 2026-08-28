"""Portable Wuding-style regional mother graph and deterministic scope builder."""
from __future__ import annotations

import argparse
import copy
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse,parse_qs

import geopandas as gpd
import networkx as nx
import numpy as np
from pyproj import CRS, Transformer
from shapely import from_wkt
from shapely.geometry import Point, LineString, MultiLineString

from build_demand_master import build_demand_master
from drone import build_drone
from microgrid import select_sites
from portable_core import (DEMSampler, GateError, demand_energy, digest, goods_kg,
                           read_csv, read_json, safe_path, shortest_paths, stable_id,
                           write_csv, write_json)
from portable_roads import canonical, insert_services, fill_secondary_gaps, physical_costs, assess

VERSION = "portable-network-2.0.0"
DEVELOPMENT_VENV = "C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv"
SCENARIO_FIELDS = ["network_id","scenario_id","generator_version","scenario_path"]


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_profile(profile, root):
    for name in ["region","execution","inputs","approval","dem","demand","roads","truck","energy","goods","microgrid","drone","scopes"]:
        if name not in profile:
            raise GateError(f"Missing configuration section: {name}")
    if profile["approval"]["status"] != "approved_for_preprocessing":
        raise GateError("Regional assumptions require explicit preprocessing approval")
    crs = CRS(profile["region"]["processing_crs"])
    if not crs.is_projected or any(a.unit_conversion_factor != 1 for a in crs.axis_info[:2]):
        raise GateError("Processing CRS must be projected in metres")
    configured = profile["execution"]["python_interpreter"]
    if configured != "current" and Path(configured).resolve() != Path(sys.executable).resolve():
        raise GateError("Run with the profile-selected Python interpreter")
    if profile["execution"].get("create_environment") is not False:
        raise GateError("This pipeline never creates a virtual environment")
    if profile["demand"]["connectivity"] != 8 or profile["demand"]["target_resolution_m"] != 100:
        raise GateError("Demand contract requires 100 m target and 8-neighbour components")
    if profile["demand"].get("boundary_cell_inclusion") != "cell_center_all_touched_false":
        raise GateError("Legacy Huanzhou-only rasterization is not a portable regional rule")
    if CRS(profile["demand"]["target_projected_crs"]) != crs:
        raise GateError("Demand and network processing CRS must match")
    if not profile.get("synthetic_fixture") and profile["demand"].get("official_population_total") is not None:
        if not profile["demand"].get("official_total_source") or not profile["demand"].get("official_total_reference_year"):
            raise GateError("Official population requires source and reference year")
    if not profile.get("synthetic_fixture"):
        if not all(profile["energy"].get(k) for k in ["household_source","electricity_source","reference_year"]):
            raise GateError("Regional household/electricity assumptions require explicit source and year evidence")
    for key in ["coverage_time_min","coverage_distance_m","max_candidates","solver_time_limit_s","objective_tolerance"]:
        if not isinstance(profile["microgrid"].get(key),(int,float)) or profile["microgrid"][key]<=0:
            raise GateError(f"Missing positive microgrid parameter: {key}")
    if not isinstance(profile["goods"].get("kg_per_person_24h"),(int,float)) or profile["goods"]["kg_per_person_24h"]<=0:
        raise GateError("Missing approved goods demand rule")
    dirs=[s["directory"] for s in profile["scopes"]]
    combos=[s["combination_id"] for s in profile["scopes"]]
    if len(set(dirs))!=len(dirs) or len(set(combos))!=len(combos):
        raise GateError("Scope directory/combination IDs must be unique")
    for scope in profile["scopes"]:
        identifiers=[scope["combination_id"]]+list(scope.get("network_ids",{}).values())
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+",i) for i in identifiers):
            raise GateError("Unsafe network/combination ID")
    if profile["roads"]["access_threshold_m"] != 200 or profile["roads"]["fallback_ratio"] != .2:
        raise GateError("Source gate must retain inclusive 200 m and >=20% fallback")
    if set(profile["truck"]["speeds_kmh"]) != {"secondary","residential"}:
        raise GateError("Exactly two truck road classes are required")
    required_positive = [(profile["truck"],k) for k in ["mass_kg","flat_kwh_per_km","gravity_m_s2","uphill_efficiency","dem_interval_m"]]
    required_positive += [(profile["energy"],k) for k in ["annual_household_kwh","people_per_household","annual_hours","outage_hours","critical_fraction"]]
    required_positive += [(profile["drone"],k) for k in ["cruise_m_s","climb_m_s","descent_m_s","clearance_m","nominal_battery_kwh","dem_interval_m","maximum_altitude_m"]]
    for mapping,key in required_positive:
        value=mapping.get(key)
        if not isinstance(value,(float,int)) or not math.isfinite(value) or value <= 0:
            raise GateError(f"Required positive regional parameter is missing/invalid: {key}")
    if not 0 <= profile["drone"]["reserve_fraction"] < 1:
        raise GateError("Invalid battery reserve")
    if not 0 < profile["energy"]["critical_fraction"] <= 1 or not 0 < profile["truck"]["uphill_efficiency"] <= 1:
        raise GateError("Invalid critical-load fraction or uphill efficiency")
    if any(not isinstance(v,(int,float)) or v <= 0 for v in profile["truck"]["speeds_kmh"].values()):
        raise GateError("Road speed must be positive")
    if set(profile["drone"]["states"]) != {"empty","full"}:
        raise GateError("Exactly empty/full payload calibration is required")
    for state in profile["drone"]["states"].values():
        if state["depletion_range_km"] <= 0 or state["depletion_hover_min"] <= 0:
            raise GateError("Invalid official-endpoint/proxy drone calibration")
    if profile["drone"]["states"]["empty"]["payload_kg"] != 0 or profile["drone"]["states"]["full"]["payload_kg"] <= 0:
        raise GateError("Invalid two-payload mass states")
    text=json.dumps(profile).lower()
    if re.search(r'"(?:key|api_key|token|password|amap_web_key)"\s*:',text):
        raise GateError("Profile contains a forbidden credential field")
    for url in re.findall(r'https?://[^"\s]+',json.dumps(profile)):
        parsed=urlparse(url)
        if parsed.username or parsed.password or any(any(w in k.lower() for w in ["token","signature","credential","api_key"]) or k.lower()=="key" for k in parse_qs(parsed.query)):
            raise GateError("Do not archive credential-bearing/signed URLs in a profile")
    for item in profile["inputs"]:
        for field in ["role","path","source_url","source_name","license","acquired_at","units"]:
            if not item.get(field) or item[field] == "REQUIRED":
                raise GateError(f"Input provenance incomplete: {item.get('role')} / {field}")
        path=safe_path(root,item["path"])
        if not path.is_file():
            raise GateError(f"Required {item['role']} missing: {item['path']}; obtain from {item['source_url']}")
        if item["path"]==profile["demand"]["population_raster_path"] and item["units"] not in {"people_per_cell","persons_per_cell"}:
            raise GateError("Population input must explicitly declare counts per cell, not density")


def input_inventory(root,profile):
    rows=[]
    for item in profile["inputs"]:
        path=safe_path(root,item["path"])
        actual=digest(path)
        if item.get("sha256") and item["sha256"] != actual:
            raise GateError(f"Input hash mismatch: {item['role']}")
        rows.append({**item,"sha256":actual,"size_bytes":path.stat().st_size})
    required={profile["demand"]["boundary_path"],profile["demand"]["population_raster_path"],profile["dem"]["path"],profile["roads"]["osm"]["path"]}
    if profile.get("units"):
        required.add(profile["units"]["path"])
    if profile["roads"].get("licensed"):
        required.add(profile["roads"]["licensed"]["path"])
    if not required <= {r["path"] for r in rows}:
        raise GateError("Every source used by generation must appear in the input inventory")
    known={safe_path(root,r["path"]) for r in rows}
    for path in list(known):
        if path.suffix.lower()==".shp":
            if any(path.with_suffix(s) not in known for s in [".shx",".dbf",".prj"]):
                raise GateError("Shapefile inventory must include .shx/.dbf/.prj, or use a single-file GeoPackage")
    return rows


def build_terminals(root,profile,demand_dir):
    rows=read_csv(demand_dir/"demand_master.csv")
    components={r["component_label"]:r for r in read_csv(demand_dir/"demand_components.csv")}
    transform=Transformer.from_crs("EPSG:4326",profile["region"]["processing_crs"],always_xy=True)
    units=None
    if profile.get("units"):
        cfg=profile["units"]
        units=gpd.read_file(safe_path(root,cfg["path"]),layer=cfg.get("layer")).to_crs(profile["region"]["processing_crs"])
        if units[cfg["id_field"]].astype(str).duplicated().any():
            raise GateError("Administrative subunit IDs are not unique")
    result=[]
    for r in rows:
        c=components[r["component_label"]]
        x,y=transform.transform(float(c["centroid_longitude"]),float(c["centroid_latitude"]))
        ax,ay=transform.transform(float(r["longitude"]),float(r["latitude"]))
        unit=profile["region"]["region_id"]
        if units is not None:
            matches=units.loc[units.geometry.covers(Point(x,y))]
            if len(matches) != 1:
                raise GateError(f"Population centroid has ambiguous/missing subunit: {r['node_id']}")
            unit=str(matches.iloc[0][profile["units"]["id_field"]])
        result.append({"terminal_id":r["node_id"],"source_county_component_id":r["node_id"],
                       "source_raster_component_label":r["component_label"],"home_township_id":unit,
                       "population":float(r["population"]),"raw_population":float(r["raw_population"]),
                       "population_evidence":r["population_value_status"],
                       "customer_coordinate_id":"CUSTOMER::"+r["node_id"],
                       "delivery_x_m":x,"delivery_y_m":y,"crs":profile["region"]["processing_crs"],
                       "access_reference_x_m":ax,"access_reference_y_m":ay,
                       "source_lineage":f"population_component:{r['component_label']};weighted_cell_centres;no_road_coordinate_substitution"})
    return result


def topology_arcs(edges,truck):
    result=[]
    for e in edges:
        for forward in [True,False]:
            if e["forward_allowed" if forward else "reverse_allowed"]:
                a,b=(e["from_node"],e["to_node"]) if forward else (e["to_node"],e["from_node"])
                result.append({"arc_id":e["edge_id"]+("_F" if forward else "_R"),"from_node":a,"to_node":b,
                               "distance_m":e["length_m"],"time_min":e["length_m"]/(truck["speeds_kmh"][e["final_road_class"]]*1000/60)})
    return result


def make_mother(root,profile,profile_path,stage):
    mother=stage/"registry/mother_network"
    demand_dir=mother/"population"
    build_demand_master(project_root=root,profile_path=profile_path,output_dir=demand_dir)
    services=build_terminals(root,profile,demand_dir)
    boundary=gpd.read_file(safe_path(root,profile["demand"]["boundary_path"]),layer=profile["demand"].get("boundary_layer")).to_crs(profile["region"]["processing_crs"])
    selector=profile["demand"].get("boundary_selector")
    if selector:
        boundary=boundary.loc[boundary[selector["field"]].astype(str)==str(selector["equals"])]
    if boundary.empty:
        raise GateError("Administrative boundary selector is empty")
    attempts=[]
    selected=None
    for provider in ["osm","licensed"]:
        if provider not in profile["roads"]:
            continue
        cfg=profile["roads"][provider]
        if provider == "licensed":
            if not cfg.get("license_verified"):
                raise GateError("licensed_local_source_required: license has not been verified")
            if "amap" in cfg["provider_name"].lower():
                if profile["region"]["country_code"] != "CN" or not cfg.get("written_authorization_confirmed"):
                    raise GateError("AMap forbidden outside authorized mainland China")
        try:
            nodes,edges=canonical(root,profile,boundary,provider)
            _,edges,snaps=insert_services(nodes,edges,services,profile["roads"])
            gate=assess(snaps,topology_arcs(edges,profile["truck"]),profile["demand"]["depot"]["id"],profile["roads"]["fallback_ratio"])
        except GateError as error:
            if str(error) != "no_valid_roads":
                raise
            gate={"qualified":False,"inaccessible_ratio":1.,"reason":"no_valid_roads"}
        gate["provider"]=cfg["provider_name"]
        attempts.append(gate)
        write_json(stage/"registry/road_source_assessment.json",attempts)
        if gate["qualified"]:
            selected=(edges,snaps)
            break
    if selected is None:
        state="amap_authorization_required" if profile["region"]["country_code"] == "CN" else "licensed_local_source_required"
        raise GateError(state+": no eligible source passed; no connector or new API call was made")
    edges,snaps=selected
    edges=fill_secondary_gaps(edges,profile["roads"]["secondary_gap_max_m"])
    # Geometry has been source-selected before any expensive full DEM operation.
    sampler=DEMSampler(safe_path(root,profile["dem"]["path"]),profile["region"]["processing_crs"],profile["dem"]["vertical_unit"],profile["dem"]["vertical_datum"])
    try:
        profiles,arcs=physical_costs(edges,sampler,profile["truck"])
    finally:
        sampler.close()
    write_csv(mother/"terminal_registry.csv",snaps)
    edge_rows=[]
    for row in edges:
        out={k:v for k,v in row.items() if k != "geometry"}
        out["parent_edge_id"]=out.get("parent_edge_id",out["edge_id"])
        edge_rows.append(out)
    fields=sorted({k for row in edge_rows for k in row})
    write_csv(mother/"physical_edges.csv",edge_rows,fields)
    write_csv(mother/"directed_arc_nominal_costs.csv",arcs)
    write_csv(mother/"physical_edge_elevation_profiles.csv",profiles)
    road_nodes={}
    graph=nx.Graph()
    for e in edges:
        graph.add_edge(e["from_node"],e["to_node"])
        for node,coordinate in [(e["from_node"],e["geometry"].coords[0]),(e["to_node"],e["geometry"].coords[-1])]:
            road_nodes[node]={"node_id":node,"x_m":coordinate[0],"y_m":coordinate[1],"crs":profile["region"]["processing_crs"]}
    for component,nodes in enumerate(sorted(nx.connected_components(graph),key=lambda c:(-len(c),min(c))),1):
        for node in nodes:
            road_nodes[node].update(component_id=f"C{component:05d}",degree=graph.degree(node))
    write_csv(mother/"road_nodes.csv",[road_nodes[n] for n in sorted(road_nodes)])
    gis=gpd.GeoDataFrame(edge_rows,geometry=[r["geometry"] for r in edges],crs=profile["region"]["processing_crs"])
    gis.to_file(mother/"mother_network.gpkg",layer="physical_edges",driver="GPKG")
    gis.to_crs(4326).to_file(mother/"physical_edges_wgs84.geojson",driver="GeoJSON")
    tg=gpd.GeoDataFrame(snaps,geometry=[Point(r["delivery_x_m"],r["delivery_y_m"]) for r in snaps],crs=gis.crs)
    tg.to_file(mother/"mother_network.gpkg",layer="customer_centroids",driver="GPKG")
    draw_map(stage/"registry/mother_network/overview.png",gis,tg,boundary)
    return mother, {"terminals":len(snaps),"physical_edges":len(edges),"physical_arcs":len(arcs),
                    "selected_provider":attempts[-1]["provider"],"source_gate":attempts[-1]}


def draw_map(path,edges,terminals,boundary=None):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    fig,ax=plt.subplots(figsize=(10,10))
    if boundary is not None:
        boundary.boundary.plot(ax=ax,color="black",linewidth=.7)
    edges.plot(ax=ax,color="#6b8297",linewidth=.5)
    terminals.plot(ax=ax,color="#d94930",markersize=12)
    ax.set_aspect("equal"); ax.set_title("Road-source geometry and population-derived customer locations")
    ax.set_xlabel("Projected easting (m)"); ax.set_ylabel("Projected northing (m)")
    fig.tight_layout(); fig.savefig(path,dpi=160); plt.close(fig)


def load_mother(path):
    edges=read_csv(path/"physical_edges.csv")
    for row in edges:
        row["length_m"]=float(row["length_m"])
        row["geometry"]=from_wkt(row["geometry_wkt_m"])
    arcs=read_csv(path/"directed_arc_nominal_costs.csv")
    for row in arcs:
        for field in ["distance_m","time_min","travel_time_min","truck_energy_kwh","cumulative_ascent_m","cumulative_descent_m","nominal_speed_kmh"]:
            row[field]=float(row[field])
    terms=read_csv(path/"terminal_registry.csv")
    for row in terms:
        for field in ["population","raw_population","delivery_x_m","delivery_y_m","access_reference_x_m","access_reference_y_m","anchor_x_m","anchor_y_m","snap_distance_m"]:
            row[field]=float(row[field])
        for field in ["distance_covered","depot_connected","junction_preferred"]:
            row[field]=str(row[field]).lower()=="true"
    return edges,arcs,terms


def logical_network(arcs,terminals,combination):
    anchors=sorted({r["truck_anchor_id"] for r in terminals if r["truck_service_status"] == "active"})
    arc_index={r["arc_id"]:r for r in arcs}
    rows,lineage,audit=[],[],[]
    for origin in anchors:
        labels=shortest_paths(arcs,origin,blockers=anchors)
        for dest in anchors:
            if origin == dest:
                continue
            accepted=dest in labels
            aid=stable_id("LOGICAL_",combination,origin,dest)
            audit.append({"combination_id":combination,"from_truck_anchor_id":origin,"to_truck_anchor_id":dest,
                          "accepted":accepted,"reason":"no_other_scope_anchor_internal" if accepted else "no_path_without_other_scope_anchors"})
            if not accepted:
                continue
            ids=list(labels[dest][2]); path=[arc_index[i] for i in ids]
            nodes=[origin]+[a["to_node"] for a in path]
            if nodes[-1] != dest or any(n in anchors for n in nodes[1:-1]):
                raise GateError("Strict-direct lineage contains another service anchor")
            if any(a["to_node"] != b["from_node"] for a,b in zip(path,path[1:])):
                raise GateError("Directed path lineage is discontinuous")
            distance=math.fsum(a["distance_m"] for a in path)
            t=math.fsum(a["time_min"] for a in path)
            energy=math.fsum(a["truck_energy_kwh"] for a in path)
            connection=stable_id("CONNECTION_",combination,*sorted([origin,dest]))
            rows.append({"combination_id":combination,"contracted_logical_arc_id":aid,
                         "contracted_logical_connection_id":connection,"truck_route_arc_id":aid,
                         "arc_id":aid,"from_node":origin,"to_node":dest,
                         "from_truck_anchor_id":origin,"to_truck_anchor_id":dest,
                         "distance_m":distance,"time_min":t,"nominal_distance_km":distance/1000,
                         "nominal_time_min":t,"nominal_energy_kwh":energy,
                         "cumulative_ascent_m":math.fsum(a["cumulative_ascent_m"] for a in path),
                         "cumulative_descent_m":math.fsum(a["cumulative_descent_m"] for a in path),
                         "physical_arc_count":len(ids)})
            lineage.append({"combination_id":combination,"truck_route_arc_id":aid,
                            "contracted_logical_arc_id":aid,"contracted_logical_connection_id":connection,
                            "from_truck_anchor_id":origin,"to_truck_anchor_id":dest,
                            "canonical_directed_physical_arc_ids_json":ids,
                            "canonical_physical_edge_ids_json":[a["edge_id"] for a in path],
                            "canonical_physical_node_ids_json":nodes,
                            "strict_direct_rule_version":"other_current_scope_anchors_blocked",
                            "path_rule_version":"time_distance_ordered_directed_arc_ID_lex"})
    lookup={(r["from_node"],r["to_node"]):r for r in rows}
    for r in rows:
        reverse=lookup.get((r["to_node"],r["from_node"]))
        r["reverse_contracted_logical_arc_id"]=reverse["arc_id"] if reverse else ""
    return rows,lineage,audit


def energy_overlay(logical,terms,depot,profile):
    demands=[r for r in terms if r["terminal_id"] != depot and r["truck_service_status"] == "active"]
    ids=sorted(r["terminal_id"] for r in demands)
    by_id={r["terminal_id"]:r for r in terms}
    labels={r["truck_anchor_id"]:shortest_paths(logical,r["truck_anchor_id"]) for r in demands}
    eligible,times,populations,coverage={},{},{},[]
    for site in ids:
        populations[site]=by_id[site]["population"]
        for d in ids:
            value=labels[by_id[site]["truck_anchor_id"]].get(by_id[d]["truck_anchor_id"])
            ok=value is not None and value[0] <= Decimal(str(profile["microgrid"]["coverage_time_min"])) and value[1] <= Decimal(str(profile["microgrid"]["coverage_distance_m"]))
            eligible[(site,d)]=ok
            times[(site,d)]=value[0] if value is not None else Decimal("Infinity")
            coverage.append({"site_terminal_id":site,"demand_terminal_id":d,"eligible":ok,
                             "time_min":float(value[0]) if value else "","distance_m":float(value[1]) if value else "",
                             "logical_path_ids_json":list(value[2]) if value else [],"same_canonical_path_for_both_limits":True})
    sites,assignment,selection=select_sites(ids,eligible,times,populations,profile["microgrid"])
    for row in coverage:
        row["assigned"]=assignment.get(row["demand_terminal_id"])==row["site_terminal_id"]
    out=[]
    for site in sites:
        members=sorted(d for d,s in assignment.items() if s==site)
        population=math.fsum(populations[d] for d in members)
        out.append({"energy_site_id":site,"site_terminal_id":site,"truck_anchor_id":by_id[site]["truck_anchor_id"],
                    "service_aggregated_population":population,
                    "service_aggregated_critical_energy_demand_kwh":demand_energy(population,profile["energy"]),
                    "member_terminal_ids_json":members,"coverage_not_physical_electrical_grid":True})
    if not math.isclose(sum(r["service_aggregated_population"] for r in out),sum(populations.values()),rel_tol=1e-10):
        raise GateError("Microgrid catchment population does not conserve active local population")
    return out,coverage,selection


def build_scope(stage,final,root,profile,mother,scope,sampler,parameter_hash):
    edges,arcs,all_terms=load_mother(mother)
    units=set(map(str,scope["unit_ids"]))
    terms=[copy.deepcopy(r) for r in all_terms if not units or r["home_township_id"] in units]
    if scope.get("terminal_ids"):
        required=set(scope["terminal_ids"])
        terms=[r for r in terms if r["terminal_id"] in required]
        if {r["terminal_id"] for r in terms} != required:
            raise GateError("Scope terminal IDs absent from mother universe")
    if len(terms) < 2 or len(terms) > profile["execution"]["max_scope_terminals"]:
        raise GateError("Scope cardinality outside configured limits; never force a target node count")
    combination=scope["combination_id"]
    area_rel=Path(scope["directory"])
    if area_rel.is_absolute() or ".." in area_rel.parts or not re.fullmatch(r"\d+_town",area_rel.parts[0]) or len(area_rel.parts)!=2:
        raise GateError("Scope directory must be <n>_town/<slug>")
    area=stage/area_rel; base=area/"base"; base.mkdir(parents=True,exist_ok=False)
    ids={r["terminal_id"] for r in terms}
    depots=scope["depot_terminal_ids"]
    if not depots or not set(depots)<=ids or len(set(depots)) != len(depots):
        raise GateError("Each configured Depot must be an existing scope terminal")
    # Depot rotations must use the same mutually connected anchor set; gate each rotation.
    assessments=[]
    active_sets=[]
    for depot in depots:
        assessed=copy.deepcopy(terms)
        gate=assess(assessed,arcs,depot,profile["roads"]["fallback_ratio"])
        assessments.append(gate)
        if not gate["qualified"]:
            raise GateError(f"Scope source gate failed for Depot {depot}: {gate['inaccessible_ratio']}")
        active_sets.append({r["terminal_id"] for r in assessed if r["truck_service_status"]=="active"})
    if any(s != active_sets[0] for s in active_sets):
        raise GateError("Depot rotations have different active subsets; publish separate scopes")
    for r in terms:
        r["truck_service_status"]="active" if r["terminal_id"] in active_sets[0] else "deferred_road_access"
        r["combination_id"]=combination
    logical,lineage,audit=logical_network(arcs,terms,combination)
    if not logical:
        raise GateError("No positive strict-direct arcs")
    write_csv(base/"terminal_registry.csv",terms)
    unique={r["truck_anchor_id"]:r for r in terms if r["truck_service_status"]=="active"}
    write_csv(base/"truck_anchor_registry.csv",[{"truck_anchor_id":a,"x_m":r["anchor_x_m"],"y_m":r["anchor_y_m"],
                                                "member_service_ids_json":sorted(t["terminal_id"] for t in terms if t["truck_anchor_id"]==a)} for a,r in sorted(unique.items())])
    write_csv(base/"customer_coordinates.csv",[{k:r[k] for k in ["terminal_id","customer_coordinate_id","source_county_component_id","delivery_x_m","delivery_y_m","crs","source_lineage"]} for r in terms])
    write_csv(base/"truck_logical_arcs_nominal.csv",logical)
    write_csv(base/"fixed_path_lineage.csv",lineage)
    write_csv(base/"strict_direct_pair_audit.csv",audit)
    anchors=sorted(unique)
    accepted={(r["from_node"],r["to_node"]):r for r in logical}
    for name,field in [("strict_adjacency",None),("truck_distance_m","distance_m"),("truck_time_min","time_min"),("truck_energy_kwh","nominal_energy_kwh")]:
        matrix=[]
        for a in anchors:
            row={"truck_anchor_id":a}
            for b in anchors:
                v=accepted.get((a,b))
                row[b]=int(v is not None) if field is None else (v[field] if v else (0 if a==b else ""))
            matrix.append(row)
        write_csv(base/(name+".csv"),matrix)
    edge_ids={e for r in lineage for e in r["canonical_physical_edge_ids_json"]}
    registry=[]; network_reports=[]
    for depot in depots:
        network_id=scope.get("network_ids",{}).get(depot,combination+"_D_"+depot)
        net=area/"networks"/network_id; net.mkdir(parents=True,exist_ok=False)
        sites,coverage,selection=energy_overlay(logical,terms,depot,profile)
        backbone=[]
        for site in sites:
            for e in edges:
                if site["truck_anchor_id"] in [e["from_node"],e["to_node"]]:
                    backbone.append({"network_id":network_id,"energy_site_id":site["energy_site_id"],"edge_id":e["edge_id"],"rule":"incident_physical_edge_backbone"})
                    edge_ids.add(e["edge_id"])
        roles=[]; goods=[]
        site_ids={r["energy_site_id"] for r in sites}
        for r in terms:
            is_depot=r["terminal_id"]==depot
            roles.append({"network_id":network_id,"terminal_id":r["terminal_id"],"truck_anchor_id":r["truck_anchor_id"],
                          "is_depot":is_depot,"goods_customer":not is_depot,"is_energy_site":r["terminal_id"] in site_ids,
                          "goods_population":0 if is_depot else r["population"],"energy_service_population":0 if is_depot else r["population"],
                          "goods_demand_kg":"0.000" if is_depot else goods_kg(r["population"],profile["goods"]["kg_per_person_24h"]),
                          "local_critical_energy_kwh":0 if is_depot else demand_energy(r["population"],profile["energy"]),
                          "truck_service_status":r["truck_service_status"],"depot_service_score":0})
            if not is_depot:
                goods.append({"network_id":network_id,"terminal_id":r["terminal_id"],"truck_anchor_id":r["truck_anchor_id"],
                              "population":r["population"],"goods_population_parameter":r["population"],
                              "goods_demand_kg":goods_kg(r["population"],profile["goods"]["kg_per_person_24h"]),
                              "source_county_component_id":r["source_county_component_id"],"rounding_rule":"ROUND_HALF_UP_0.001kg",
                              "demand_evidence_status":"calibrated_population_research_goods_rule"})
        d=next(r for r in terms if r["terminal_id"]==depot)
        write_csv(net/"depot_definition.csv",[{"network_id":network_id,"depot_terminal_id":depot,
                                              "truck_anchor_id":d["truck_anchor_id"],"goods_supply":"unlimited_exogenous_nonbinding",
                                              "goods_demand_kg":0,"energy_demand_kwh":0,"service_score":0}])
        write_csv(net/"service_roles.csv",roles); write_csv(net/"goods_demand.csv",goods)
        write_csv(net/"energy_support_sites.csv",[{"network_id":network_id,**r} for r in sites],
                  ["network_id","energy_site_id","site_terminal_id","truck_anchor_id","service_aggregated_population","service_aggregated_critical_energy_demand_kwh","member_terminal_ids_json","coverage_not_physical_electrical_grid"])
        write_csv(net/"microgrid_coverage.csv",coverage,["site_terminal_id","demand_terminal_id","eligible","time_min","distance_m","logical_path_ids_json","same_canonical_path_for_both_limits","assigned"])
        write_csv(net/"energy_backbone.csv",backbone,["network_id","energy_site_id","edge_id","rule"])
        write_json(net/"microgrid_selection_audit.json",selection)
        sr=area/"scenarios/portable_g2_v2"/network_id/"scenario_registry.csv"
        write_csv(sr,[],SCENARIO_FIELDS)
        files={f.stem:str((final/area_rel/"networks"/network_id/f.name).relative_to(root)).replace("\\","/") for f in net.iterdir() if f.is_file()}
        files.update({name:str((final/area_rel/"base"/(name+".csv")).relative_to(root)).replace("\\","/") for name in
                      ["physical_edges","directed_arc_nominal_costs","physical_edge_elevation_profiles","terminal_registry","truck_anchor_registry","customer_coordinates","truck_logical_arcs_nominal","fixed_path_lineage","physical_lineage_closure","drone_cost_arcs"]})
        files["scenario_registry"]=str((final/sr.relative_to(stage)).relative_to(root)).replace("\\","/")
        definition={"schema_version":"portable-wuding-style.v2","network_id":network_id,"combination_id":combination,
                    "depot_terminal_id":depot,"files":files,"known_paths":files,"parameter_hash":parameter_hash,
                    "publication_status":"deterministic_network_published_scenarios_pending","model_adapter_validation":"not_run_requires_model_agent_review",
                    "counts":{"terminal_registry_count":len(terms),"goods_demand_count":len(goods),"energy_site_count":len(sites),"truck_logical_arc_count":len(logical),"drone_raw_leg_count":2*len(terms)**2,"scenario_count":0}}
        write_json(net/"network_definition.json",definition)
        registry.append({"network_id":network_id,"combination_id":combination,"area_slug":scope["directory"].split("/")[1],
                         "network_definition":str((final/net.relative_to(stage)/"network_definition.json").relative_to(root)).replace("\\","/"),
                         "depot_terminal_id":depot,"publication_status":definition["publication_status"],"scenario_count":0})
        network_reports.append({"network_id":network_id,"goods_customers":len(goods),"energy_sites":len(sites),
                                "depot_population_excluded":d["population"],"local_goods_population":math.fsum(r["population"] for r in goods)})
    subset=[e for e in edges if e["edge_id"] in edge_ids]
    edge_fields=sorted({k for row in subset for k in row if k != "geometry"})
    write_csv(base/"physical_edges.csv",[{k:v for k,v in r.items() if k!="geometry"} for r in subset],edge_fields)
    write_csv(base/"directed_arc_nominal_costs.csv",[a for a in arcs if a["edge_id"] in edge_ids])
    write_csv(base/"physical_edge_elevation_profiles.csv",[r for r in read_csv(mother/"physical_edge_elevation_profiles.csv") if r["edge_id"] in edge_ids])
    write_csv(base/"physical_lineage_closure.csv",[{"edge_id":e,"source":"registry/mother_network/physical_edges.csv","in_scope_closure":True} for e in sorted(edge_ids)])
    drone_stats=build_drone(base,terms,sampler,profile["drone"],combination,parameter_hash)
    gis=gpd.GeoDataFrame([{k:v for k,v in e.items() if k!="geometry"} for e in subset],geometry=[e["geometry"] for e in subset],crs=profile["region"]["processing_crs"])
    gis.to_file(base/"network.gpkg",layer="physical_edges",driver="GPKG")
    tg=gpd.GeoDataFrame(terms,geometry=[Point(r["delivery_x_m"],r["delivery_y_m"]) for r in terms],crs=gis.crs)
    tg.to_file(base/"network.gpkg",layer="customer_centroids",driver="GPKG")
    tg.to_crs(4326).to_file(base/"customer_centroids.geojson",driver="GeoJSON")
    edge_lookup={e["edge_id"]:e for e in subset}
    arc_lookup={a["arc_id"]:a for a in arcs}
    line_lookup={l["truck_route_arc_id"]:l for l in lineage}
    real_paths=[]; straight=[]
    for l in logical:
        pieces=[]
        for aid in line_lookup[l["arc_id"]]["canonical_directed_physical_arc_ids_json"]:
            a=arc_lookup[aid]; geom=edge_lookup[a["edge_id"]]["geometry"]
            pieces.append(geom if int(a["traversal_direction"])==1 else LineString(list(geom.coords)[::-1]))
        real_paths.append(MultiLineString(pieces))
        a,b=unique[l["from_node"]],unique[l["to_node"]]
        straight.append(LineString([(a["anchor_x_m"],a["anchor_y_m"]),(b["anchor_x_m"],b["anchor_y_m"])]))
    routes=gpd.GeoDataFrame(logical,geometry=real_paths,crs=gis.crs)
    routes.to_file(base/"network.gpkg",layer="truck_strict_direct_paths",driver="GPKG")
    routes.to_crs(4326).to_file(base/"truck_strict_paths.geojson",driver="GeoJSON")
    chords=gpd.GeoDataFrame([{**r,"display_chord_not_a_real_road":True} for r in logical],geometry=straight,crs=gis.crs)
    chords.to_crs(4326).to_file(base/"truck_direct_links_straight.geojson",driver="GeoJSON")
    corridor_rows=read_csv(base/"drone_corridors.csv")
    flight_gis=gpd.GeoDataFrame(corridor_rows,geometry=[from_wkt(r["geometry_wkt_m"]) for r in corridor_rows],crs=gis.crs)
    flight_gis["candidate_only_not_approved_route"]=True
    flight_gis.to_file(base/"network.gpkg",layer="drone_candidate_corridors",driver="GPKG")
    flight_gis.to_crs(4326).to_file(base/"drone_candidate_corridors.geojson",driver="GeoJSON")
    draw_map(base/"network_overview.png",gis,tg)
    write_json(base/"source_gate.json",assessments)
    return registry,{"combination_id":combination,"terminal_count":len(terms),"physical_edge_count":len(subset),
                     "logical_arc_count":len(logical),"all_ordered_anchor_pairs":len(audit),"networks":network_reports,**drone_stats}


def run(args):
    start=time.monotonic(); root=args.project_root.resolve(); path=args.profile.resolve()
    profile=read_json(path); validate_profile(profile,root)
    inventory=input_inventory(root,profile)
    final=safe_path(root,args.output_root or profile["execution"]["output_root"])
    protected=[root/p for p in ["data/raw","model","manuscript"]]
    if final.exists() or any(final.is_relative_to(p) for p in protected) or final==root:
        raise GateError("Output must be a new, unprotected processed release path")
    final.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix="stg-",dir=final.parent))
    try:
        write_json(stage/"registry/parameter_snapshot.json",profile)
        write_json(stage/"registry/input_inventory.json",inventory)
        semantic=copy.deepcopy(profile); semantic["execution"].pop("output_root",None)
        parameter_hash=stable_id("",semantic)
        if args.mother_root:
            prior=safe_path(root,args.mother_root)
            manifest=read_json(prior/"registry/run_manifest.json")
            old_inventory={r["path"]:r["sha256"] for r in manifest["input_inventory"]}
            if old_inventory!={r["path"]:r["sha256"] for r in inventory}:
                raise GateError("Mother reuse requires identical input bytes, not just identical filenames")
            if manifest["parameter_hash"] != parameter_hash:
                # Scope definitions may change; mother science may not.
                old=read_json(prior/"registry/parameter_snapshot.json")
                for name in ["region","inputs","demand","roads","truck","dem"]:
                    if old[name] != profile[name]:
                        raise GateError(f"Mother reuse scientific configuration mismatch: {name}")
            for name,sha in manifest["mother_core_hashes"].items():
                if digest(prior/"registry/mother_network"/name) != sha:
                    raise GateError("Mother core hash mismatch")
            mother=stage/"registry/mother_network"
            shutil.copytree(prior/"registry/mother_network",mother)
            shutil.copy2(prior/"registry/road_source_assessment.json",stage/"registry/road_source_assessment.json")
            mother_stats={"reused":True,"source_release":str(prior.relative_to(root))}
        else:
            mother,mother_stats=make_mother(root,profile,path,stage)
        registry=[]; reports=[]
        if args.stage == "full":
            sampler=DEMSampler(safe_path(root,profile["dem"]["path"]),profile["region"]["processing_crs"],profile["dem"]["vertical_unit"],profile["dem"]["vertical_datum"])
            try:
                for scope in profile["scopes"]:
                    print("Building scope "+scope["combination_id"],flush=True)
                    rows,report=build_scope(stage,final,root,profile,mother,scope,sampler,parameter_hash)
                    registry.extend(rows); reports.append(report)
            finally:
                sampler.close()
        write_csv(stage/"registry/network_registry.csv",registry,["network_id","combination_id","area_slug","network_definition","depot_terminal_id","publication_status","scenario_count"])
        write_csv(stage/"registry/area_registry.csv",[{"combination_id":s["combination_id"],"scope_path":s["directory"],"unit_ids_json":s["unit_ids"]} for s in profile["scopes"]] if args.stage=="full" else [],["combination_id","scope_path","unit_ids_json"])
        write_csv(stage/"registry/scenario_registry.csv",[],SCENARIO_FIELDS)
        from generate_scenarios import generate
        scenario_report=generate(stage,final,root,profile,registry) if args.stage=="full" else {"enabled":False,"scenario_count":0}
        if scenario_report["enabled"]:
            write_csv(stage/"registry/network_registry.csv",registry)
        from validate_pipeline import validate_release
        validation=validate_release(stage,root,final)
        if not validation["passed"]:
            raise GateError("Independent output validator failed")
        write_json(stage/"registry/validation.json",validation)
        write_json(stage/"registry/environment_snapshot.json",{"python":sys.version,"executable":sys.executable,"platform":platform.platform(),
                   "packages":{p:importlib.metadata.version(p) for p in ["numpy","pandas","geopandas","pyogrio","shapely","rasterio","pyproj","scipy","networkx"]}})
        skill_root=Path(__file__).parent.parent
        source_hash=stable_id("",[(str(f.relative_to(skill_root)).replace("\\","/"),digest(f))
                                 for f in sorted(skill_root.rglob("*")) if f.is_file() and "__pycache__" not in f.parts
                                 and f.suffix in {".py",".md",".json",".yaml",".csv"}])
        core={str(f.relative_to(stage)).replace("\\","/"):digest(f) for f in sorted(stage.rglob("*.csv"))}
        manifest={"pipeline_version":VERSION,"generated_at":now(),"stage":args.stage,"parameter_hash":parameter_hash,
                  "skill_source_hash":source_hash,"profile_path":str(path),"input_inventory":inventory,
                  "mother_core_hashes":{name:digest(mother/name) for name in ["terminal_registry.csv","physical_edges.csv","directed_arc_nominal_costs.csv","physical_edge_elevation_profiles.csv"]},
                  "core_csv_hashes":core,"mother":mother_stats,"scopes":reports,"scenarios":scenario_report,"runtime_seconds":time.monotonic()-start,
                  "no_model_or_gurobi":True,"new_amap_requests":0,"evidence_status":"synthetic_fixture" if profile.get("synthetic_fixture") else "real_source_research_preprocessing"}
        write_json(stage/"registry/run_manifest.json",manifest)
        (stage/"README.md").write_text("# Regional network release\n\nResolve networks through `registry/network_registry.csv`.\n\n"
            "Mother graph: `registry/mother_network/`; all real physical geometry is retained there for new scopes.\n\n"
            f"Development/shared environment: `{DEVELOPMENT_VENV}`. Actual run: `{sys.executable}`. No new environment was created.\n\n"
            "Road, microgrid and drone outputs are research candidates, not access certification, physical electrical supply networks, or approved flights.\n",encoding="utf-8")
        stage.rename(final)
        return {"status":"published","path":str(final),"network_count":len(registry),"runtime_seconds":manifest["runtime_seconds"],"validation":validation}
    except Exception as error:
        # Retain the exact failed stage for diagnosis, never alias it as an active release.
        write_json(stage/"failure.json",{"status":"audit_only_not_published","error_type":type(error).__name__,"message":str(error),"generated_at":now()})
        raise GateError(f"Not published. Audit retained at {stage}. {error}") from error


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root",type=Path,required=True)
    parser.add_argument("--profile",type=Path,required=True)
    parser.add_argument("--stage",choices=["mother","full"],default="full")
    parser.add_argument("--output-root")
    parser.add_argument("--mother-root",help="Reuse a previous validated mother graph, never clip its logical arcs")
    args=parser.parse_args()
    try:
        print(json.dumps(run(args),ensure_ascii=False,indent=2))
    except (GateError,KeyError,ValueError) as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(2)


if __name__=="__main__":
    main()
