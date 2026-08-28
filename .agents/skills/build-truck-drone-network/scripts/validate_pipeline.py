"""Independent, read-only validation of the portable release tables."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from shapely import from_wkt

from portable_core import (GateError, demand_energy, goods_kg, read_csv, read_json,
                           safe_path, slope_metrics, truck_energy)


def yes(value):
    return str(value).lower() in {"true","1"}


def check(condition, message):
    if not condition:
        raise GateError(message)


def close(a,b):
    return math.isclose(float(a),float(b),rel_tol=1e-9,abs_tol=1e-7)


def keyed(rows, key, label):
    result={r[key]:r for r in rows}
    check(len(result)==len(rows),f"Duplicate {label} key: {key}")
    check(all(result),f"Empty {label} key")
    return result


def validate_physical(base,profile):
    edges=keyed(read_csv(base/"physical_edges.csv"),"edge_id","physical edge")
    arcs=keyed(read_csv(base/"directed_arc_nominal_costs.csv"),"arc_id","physical arc")
    profiles=keyed(read_csv(base/"physical_edge_elevation_profiles.csv"),"edge_id","profile")
    check(set(edges)==set(profiles),"DEM profile/physical edge keys differ")
    check(bool(arcs),"Empty physical graph")
    for eid,e in edges.items():
        check(e["final_road_class"] in {"secondary","residential"},"Unmapped road class")
        check(e["from_node"]!=e["to_node"],"Physical self loop")
        geom=from_wkt(e["geometry_wkt_m"])
        check(geom.is_valid and not geom.is_empty and geom.length>0,"Bad physical geometry")
        check(close(geom.length,e["length_m"]),"Geometry/length mismatch")
        check(e["source_feature_ids"] and e["source_url"] and e["source_license"],"Missing real road provenance")
        check(bool(json.loads(e["lineage_json"])),"Empty road lineage")
        p=profiles[eid]
        dd=np.asarray(json.loads(p["sample_distances_m_json"]),dtype=float)
        zz=np.asarray(json.loads(p["smoothed_elevations_m_json"]),dtype=float)
        check(len(dd)==len(zz)>=2,"DEM array length mismatch")
        check(np.isfinite(dd).all() and np.isfinite(zz).all(),"Nonfinite DEM samples")
        check(dd[0]==0 and close(dd[-1],e["length_m"]),"Profile endpoint chainage mismatch")
        smax,smean=slope_metrics(dd,zz)
        check(close(smax,p["smax_abs_gradient"]) and close(smean,p["smean_abs_gradient"]),"Within-edge slope mismatch")
        for forward in [True,False]:
            aid=eid+("_F" if forward else "_R")
            check((aid in arcs)==yes(e["forward_allowed" if forward else "reverse_allowed"]),"Direction evidence mismatch")
    for aid,a in arcs.items():
        check(a["edge_id"] in edges,"Unknown physical edge in arc")
        e=edges[a["edge_id"]]; forward=int(a["traversal_direction"])==1
        u,v=(e["from_node"],e["to_node"]) if forward else (e["to_node"],e["from_node"])
        check((u,v)==(a["from_node"],a["to_node"]),"Physical direction endpoint mismatch")
        for field in ["distance_m","time_min","truck_energy_kwh"]:
            check(math.isfinite(float(a[field])) and float(a[field])>0,"Invalid positive physical cost")
        z=np.asarray(json.loads(profiles[e["edge_id"]]["smoothed_elevations_m_json"]),dtype=float)
        dz=np.diff(z if forward else z[::-1])
        up,down=np.maximum(dz,0).sum(),np.maximum(-dz,0).sum()
        check(close(up,a["cumulative_ascent_m"]) and close(down,a["cumulative_descent_m"]),"Directional ascent/descent mismatch")
        check(close(a["distance_m"],e["length_m"]),"Arc/edge length mismatch")
        speed=profile["truck"]["speeds_kmh"][e["final_road_class"]]
        check(close(float(a["distance_m"])/(speed*1000/60),a["time_min"]),"Truck time formula mismatch")
        check(close(truck_energy(float(a["distance_m"]),float(up),profile["truck"]),a["truck_energy_kwh"]),"Truck energy formula mismatch")
        rid=a["reverse_directed_physical_arc_id"]
        if rid:
            check(rid in arcs and arcs[rid]["reverse_directed_physical_arc_id"]==aid,"Bad reverse physical evidence")
            check(close(a["cumulative_ascent_m"],arcs[rid]["cumulative_descent_m"]),"Reverse ascent/descent mismatch")
    return edges,arcs,profiles


def validate_release(stage,project_root,final_root=None):
    stage=Path(stage).resolve(); root=Path(project_root).resolve()
    final=Path(final_root or stage).resolve()
    profile=read_json(stage/"registry/parameter_snapshot.json")
    mother=stage/"registry/mother_network"
    medges,marcs,mprofiles=validate_physical(mother,profile)
    mother_terms=keyed(read_csv(mother/"terminal_registry.csv"),"terminal_id","mother terminal")
    official=profile["demand"].get("official_population_total")
    if official is not None:
        check(close(sum(float(r["population"]) for r in mother_terms.values()),official),"Calibrated official population not conserved")
    gates=read_json(stage/"registry/road_source_assessment.json")
    check(gates and yes(gates[-1]["qualified"]),"Mother source gate not passed")
    check(gates[-1]["inaccessible_ratio"]<.2 and gates[-1]["depot_attached"],"Source gate denominator/Depot condition failed")
    registry=read_csv(stage/"registry/network_registry.csv")
    keyed(registry,"network_id","network registry")
    areas=read_csv(stage/"registry/area_registry.csv")
    keyed(areas,"combination_id","area registry")
    summaries=[]
    path_columns=["network_definition","scope_path"]

    def runtime_path(relative):
        path=safe_path(root,relative)
        check(path.is_relative_to(final),"Final runtime path escapes this release")
        check(not {"archive","staging"}&set(p.lower() for p in path.relative_to(final).parts),"Runtime depends on archive/staging")
        actual=stage/path.relative_to(final)
        check(actual.is_file(),f"Runtime interface missing: {relative}")
        return actual

    total_scenarios=0
    for scope in areas:
        area=stage/scope["scope_path"]; base=area/"base"
        edges,arcs,profiles=validate_physical(base,profile)
        check(set(edges)<=set(medges),"Scope invented a physical edge")
        terms=keyed(read_csv(base/"terminal_registry.csv"),"terminal_id","scope terminal")
        check(set(terms)<=set(mother_terms),"Scope invented a service identity")
        coords=keyed(read_csv(base/"customer_coordinates.csv"),"terminal_id","customer coordinate")
        check(set(coords)==set(terms),"Missing customer coordinates")
        for tid,t in terms.items():
            for field in ["population","delivery_x_m","delivery_y_m","anchor_x_m","anchor_y_m","snap_distance_m"]:
                check(math.isfinite(float(t[field])),"Nonfinite terminal field")
                check(close(t[field],mother_terms[tid][field]),"Slice changed mother terminal position/population")
            c=coords[tid]
            check(c["source_county_component_id"]==t["source_county_component_id"],"Customer component ID mismatch")
            check(c["crs"]==profile["region"]["processing_crs"],"Customer CRS mismatch")
            check(close(c["delivery_x_m"],t["delivery_x_m"]) and close(c["delivery_y_m"],t["delivery_y_m"]),"Customer centroid replaced with road anchor")
        anchors=keyed(read_csv(base/"truck_anchor_registry.csv"),"truck_anchor_id","truck anchor")
        expected_anchors={r["truck_anchor_id"] for r in terms.values() if r["truck_service_status"]=="active"}
        check(set(anchors)==expected_anchors,"Service-to-anchor mapping incomplete")
        logical=keyed(read_csv(base/"truck_logical_arcs_nominal.csv"),"arc_id","logical arc")
        lineage=keyed(read_csv(base/"fixed_path_lineage.csv"),"truck_route_arc_id","logical lineage")
        check(set(logical)==set(lineage),"Logical arc/lineage keys differ")
        audit=read_csv(base/"strict_direct_pair_audit.csv")
        check(len(audit)==len(anchors)*(len(anchors)-1),"Strict pair audit incomplete")
        check(sum(yes(a["accepted"]) for a in audit)==len(logical),"Strict accept count mismatch")
        required=set()
        for lid,l in logical.items():
            f=lineage[lid]; ids=json.loads(f["canonical_directed_physical_arc_ids_json"])
            nodes=json.loads(f["canonical_physical_node_ids_json"])
            eids=json.loads(f["canonical_physical_edge_ids_json"])
            check(ids and len(ids)+1==len(nodes),"Invalid ordered lineage dimensions")
            check(l["from_node"]!=l["to_node"] and {l["from_node"],l["to_node"]}<=set(anchors),"Bad strict endpoints/self loop")
            check(nodes[0]==l["from_node"] and nodes[-1]==l["to_node"],"Logical/physical endpoints differ")
            check(not (set(nodes[1:-1])&set(anchors)),"Strict link traverses another service anchor")
            check(set(ids)<=set(arcs),"Logical path references absent physical arc")
            check(eids==[arcs[a]["edge_id"] for a in ids],"Ordered edge lineage mismatch")
            check(all((arcs[a]["from_node"],arcs[a]["to_node"])==(nodes[i],nodes[i+1]) for i,a in enumerate(ids)),"Lineage chain discontinuity")
            for lf,pf in [("distance_m","distance_m"),("time_min","time_min"),("nominal_energy_kwh","truck_energy_kwh"),
                          ("cumulative_ascent_m","cumulative_ascent_m"),("cumulative_descent_m","cumulative_descent_m")]:
                check(close(l[lf],math.fsum(float(arcs[a][pf]) for a in ids)),"Logical cost is not physical sum")
            check(float(l["distance_m"])>0 and float(l["time_min"])>0,"Zero logical cost")
            rid=l["reverse_contracted_logical_arc_id"]
            if rid:
                check(rid in logical and (logical[rid]["from_node"],logical[rid]["to_node"])==(l["to_node"],l["from_node"]),"False reverse evidence")
            required.update(eids)
        check(required<=set(profiles),"Lineage profile coverage incomplete")
        closure={r["edge_id"] for r in read_csv(base/"physical_lineage_closure.csv")}
        check(closure==set(edges),"Physical closure differs from materialized subset")
        flights=read_csv(base/"drone_cost_arcs.csv")
        keyed(flights,"leg_id","drone leg")
        n=len(terms)
        check(len(flights)==2*n*n,"Raw drone leg count must equal 2*N*N")
        keys={(r["anchor_terminal_id"],r["customer_terminal_id"],r["payload_state"]) for r in flights}
        check(len(keys)==2*n*n and all(a in terms and c in terms and p in {"empty","full"} for a,c,p in keys),"Drone endpoint/payload universe mismatch")
        corridors=read_csv(base/"drone_corridors.csv")
        check(len(corridors)==n*n,"Drone corridor table incomplete")
        bypair={(r["anchor_terminal_id"],r["customer_terminal_id"]):r for r in corridors}
        for f in flights:
            a,c=terms[f["anchor_terminal_id"]],terms[f["customer_terminal_id"]]
            p=bypair[(a["terminal_id"],c["terminal_id"])]
            for pf,t,tf in [("anchor_x_m",a,"anchor_x_m"),("anchor_y_m",a,"anchor_y_m"),("delivery_x_m",c,"delivery_x_m"),("delivery_y_m",c,"delivery_y_m")]:
                check(close(p[pf],t[tf]),"Drone role-aware endpoint coordinate mismatch")
            for field in ["raw_total_time_s","raw_total_energy_kwh"]:
                check(math.isfinite(float(f[field])) and float(f[field])>0,"Invalid raw drone cost")
            check(not yes(f["reserve_applied_to_raw_leg"]),"Reserve incorrectly subtracted from raw flight")
            threshold=profile["drone"]["nominal_battery_kwh"]*(1-profile["drone"]["reserve_fraction"])
            check(yes(f["arc_energy_necessary_feasible"])==(float(f["raw_total_energy_kwh"])<=threshold),"Single-flight energy flag mismatch")
        network_rows=[r for r in registry if r["combination_id"]==scope["combination_id"]]
        check(bool(network_rows),"Scope has no network")
        for reg in network_rows:
            definition_path=runtime_path(reg["network_definition"]); net=definition_path.parent
            definition=read_json(definition_path)
            check(definition["network_id"]==reg["network_id"],"Network identity mismatch")
            for path in definition["files"].values():
                runtime_path(path)
            depots=read_csv(net/"depot_definition.csv")
            check(len(depots)==1,"Each network must have one Depot")
            d=depots[0]; depot=d["depot_terminal_id"]
            check(depot in terms and d["truck_anchor_id"]==terms[depot]["truck_anchor_id"],"Depot coordinate/identity mismatch")
            check(float(d["goods_demand_kg"])==float(d["energy_demand_kwh"])==float(d["service_score"])==0,"Depot cannot score as demand")
            goods=keyed(read_csv(net/"goods_demand.csv"),"terminal_id","goods demand")
            roles=keyed(read_csv(net/"service_roles.csv"),"terminal_id","service role")
            check(set(goods)==set(terms)-{depot} and set(roles)==set(terms),"Goods/depot role split mismatch")
            for tid,g in goods.items():
                check(close(g["population"],terms[tid]["population"]),"Goods local/catchment population mixed")
                check(g["goods_demand_kg"]==goods_kg(g["population"],profile["goods"]["kg_per_person_24h"]),"Goods rounding mismatch")
            active={t for t in goods if terms[t]["truck_service_status"]=="active"}
            sites=keyed(read_csv(net/"energy_support_sites.csv"),"energy_site_id","energy site")
            check(set(sites)<=active,"Energy site is not active non-Depot demand")
            assigned=[r for r in read_csv(net/"microgrid_coverage.csv") if yes(r["assigned"])]
            check(Counter(r["demand_terminal_id"] for r in assigned)==Counter({t:1 for t in active}),"Demand energy assignment is not unique/complete")
            for cov in assigned:
                check(cov["site_terminal_id"] in sites and yes(cov["eligible"]),"Invalid energy assignment")
                lids=json.loads(cov["logical_path_ids_json"])
                check(set(lids)<=set(logical),"Coverage path contains unknown logical arc")
                check(close(cov["time_min"],sum(float(logical[l]["time_min"]) for l in lids)),"Coverage time not canonical path sum")
                check(close(cov["distance_m"],sum(float(logical[l]["distance_m"]) for l in lids)),"Coverage distance not same path sum")
                check(float(cov["time_min"])<=profile["microgrid"]["coverage_time_min"]+1e-9 and float(cov["distance_m"])<=profile["microgrid"]["coverage_distance_m"]+1e-9,"Coverage limits exceeded")
            for sid,s in sites.items():
                members=sorted(r["demand_terminal_id"] for r in assigned if r["site_terminal_id"]==sid)
                check(members==json.loads(s["member_terminal_ids_json"]),"Energy membership mismatch")
                pop=sum(float(terms[t]["population"]) for t in members)
                check(close(pop,s["service_aggregated_population"]),"Energy catchment population not conserved")
                check(close(demand_energy(pop,profile["energy"]),s["service_aggregated_critical_energy_demand_kwh"]),"Energy demand formula mismatch")
            for b in read_csv(net/"energy_backbone.csv"):
                check(b["energy_site_id"] in sites and b["edge_id"] in edges,"Energy backbone FK failure")
            sr=read_csv(runtime_path(definition["files"]["scenario_registry"]))
            check(len(sr)==int(reg["scenario_count"])==definition["counts"]["scenario_count"],"Scenario registry count mismatch")
            for scenario in sr:
                out=safe_path(root,scenario["scenario_path"])
                check(out.is_relative_to(final),"Scenario path outside active release")
                out=stage/out.relative_to(final)
                states=keyed(read_csv(out/"connection_states.csv"),"contracted_logical_connection_id","scenario connection")
                costs=keyed(read_csv(out/"truck_costs.csv"),"truck_route_arc_id","scenario truck arc")
                check(set(costs)==set(logical),"Scenario missing nominal logical arcs")
                check(set(states)=={a["contracted_logical_connection_id"] for a in logical.values()},"Scenario missing connections")
                for s in states.values():
                    check("node_passability" not in s,"Forbidden node-passability field")
                    probabilities=[float(s[k]) for k in ["p_normal","p_degraded","p_failed"]]
                    check(all(0<=p<=1 for p in probabilities) and close(sum(probabilities),1),"Invalid state probabilities")
                    code=int(s["state_code"])
                    check(code in {0,1,2},"Unknown scenario state")
                    if not yes(s["failure_eligible"]):
                        check(code!=2 and float(s["p_failed"])==0,"Protected connection failed")
                    for aid,a in logical.items():
                        if a["contracted_logical_connection_id"]!=s["contracted_logical_connection_id"]:
                            continue
                        c=costs[aid]
                        check(int(c["state_code"])==code,"Reverse directions do not share connection state")
                        if code==2:
                            check(not yes(c["available"]) and not any(c[k] for k in ["distance_m","time_min","truck_energy_kwh"]),"Failed arc was assigned usable zero cost")
                        else:
                            check(yes(c["available"]) and close(c["distance_m"],a["distance_m"]),"Scenario changed geometric distance")
                            check(close(c["time_min"],float(a["time_min"])*float(s["time_multiplier"])),"Scenario time multiplier mismatch")
                            check(close(c["truck_energy_kwh"],float(a["nominal_energy_kwh"])*float(s["energy_multiplier"])),"Scenario energy multiplier mismatch")
            total_scenarios+=len(sr)
        summaries.append({"combination_id":scope["combination_id"],"terminals":n,"goods_per_depot":n-1,"anchors":len(anchors),"physical_edges":len(edges),"logical_arcs":len(logical),"raw_drone_legs":len(flights),"networks":len(network_rows)})
    scenarios=read_csv(stage/"registry/scenario_registry.csv")
    check(len(scenarios)==total_scenarios,"Global scenario registry mismatch")
    return {"passed":True,"validation_version":"portable-independent-v2","network_count":len(registry),"scenario_count":total_scenarios,
            "mother_terminals":len(mother_terms),"mother_physical_edges":len(medges),"scopes":summaries,
            "checks":{"population_conservation":True,"real_source_lineage":True,"no_synthetic_road_connectors":True,
                      "source_gate":True,"depot_roles":True,"strict_lineage_and_cost_sums":True,"directional_energy":True,
                      "microgrid_assignment_conservation":True,"raw_drone_2NN_roles":True,"customer_coordinates":True,
                      "runtime_paths_active_only":True},"model_adapter_run":False,"truck_legal_access_or_drone_route_approval_proven":False}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root",type=Path,required=True)
    parser.add_argument("--release",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(validate_release(args.release,args.project_root),indent=2))
