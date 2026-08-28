"""Optional research G2 scenarios on fixed logical connections, never on nodes.

Uses a frozen whole-region logical-arc slope reference. This is a portable
implementation of the corrected G2 construction, not an empirical hazard model.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

import numpy as np

from portable_core import (GateError, digest, midrank, read_csv, read_json,
                           stable_uniform, write_csv, write_json)

VERSION="portable_g2_v2"


def slope_features(lineage, profiles):
    rows=[]
    for row in lineage:
        maximum,total_d,total_abs_z=0.,0.,0.
        ids=row["canonical_physical_edge_ids_json"]
        if isinstance(ids,str):
            ids=json.loads(ids)
        for eid in ids:
            p=profiles[eid]
            d=np.asarray(json.loads(p["sample_distances_m_json"]),dtype=float)
            z=np.asarray(json.loads(p["smoothed_elevations_m_json"]),dtype=float)
            dd,dz=np.diff(d),np.abs(np.diff(z))
            if not len(dd) or (dd<=0).any() or not np.isfinite(dd).all() or not np.isfinite(dz).all():
                raise GateError("Scenario slope requires positive finite within-edge increments")
            maximum=max(maximum,float((dz/dd).max()))
            total_d+=float(dd.sum()); total_abs_z+=float(dz.sum())
        if total_d<=0:
            raise GateError("Scenario slope path has no physical length")
        rows.append({"contracted_logical_arc_id":row["contracted_logical_arc_id"],
                     "contracted_logical_connection_id":row["contracted_logical_connection_id"],
                     "smax_percent":float(format(maximum*100,".12g")),
                     "smean_percent":float(format(total_abs_z/total_d*100,".12g")),
                     "slope_rule":"within_each_edge_only_no_cross_edge_jumps"})
    return rows


def sigmoid(x):
    return 1/(1+math.exp(-x))


def state_parameters(damage,eligible,cfg):
    k=cfg["sigmoid_steepness"]
    fd=sigmoid(k*(damage-cfg["degrade_midpoint"]))
    ff=sigmoid(k*(damage-cfg["failure_midpoint"])) if eligible else 0.
    pn,pd,pf=1-fd,fd-ff,ff
    if min(pn,pd,pf)<-1e-12 or not math.isclose(pn+pd+pf,1.,abs_tol=1e-12):
        raise GateError("Invalid scenario probability configuration")
    lo=sigmoid(k*(cfg["q_damage_min"]-cfg["degrade_midpoint"]))
    hi=sigmoid(k*(cfg["q_damage_max"]-cfg["degrade_midpoint"]))
    if hi<=lo:
        raise GateError("Invalid scenario q normalization range")
    q=min(1.,max(0.,(fd-lo)/(hi-lo)))
    return pn,pd,pf,q


def generate(stage,final,root,profile,registry):
    cfg=profile.get("scenarios",{})
    if not cfg.get("enabled"):
        return {"enabled":False,"scenario_count":0}
    if cfg.get("approval_status")!="approved_research_scenario":
        raise GateError("Scenario parameter transfer requires explicit research approval")
    if cfg["replicates"]<1 or cfg["replicates"]>cfg["maximum_replicates"]:
        raise GateError("Scenario replicate count outside configured limit")
    if not math.isclose(cfg["smax_weight"]+cfg["smean_weight"],1):
        raise GateError("Scenario feature weights must sum to one")
    if not cfg["severity_lambda"] or any(not 0<=v<=1 for v in cfg["severity_lambda"].values()):
        raise GateError("Invalid severity lambda")
    from run_network_pipeline import load_mother,logical_network
    mother=stage/"registry/mother_network"
    _,arcs,terms=load_mother(mother)
    _,lineage,_=logical_network(arcs,terms,profile["region"]["region_id"]+"_MASTER_RANK")
    profiles={r["edge_id"]:r for r in read_csv(mother/"physical_edge_elevation_profiles.csv")}
    reference=slope_features(lineage,profiles)
    if not reference:
        raise GateError("Cannot freeze an empty regional slope reference")
    calibration=stage/"registry/scenario_profiles"/VERSION
    write_csv(calibration/"county_master_rank_reference.csv",reference)
    write_json(calibration/"parameter_snapshot.json",cfg)
    refs_max=[r["smax_percent"] for r in reference]; refs_mean=[r["smean_percent"] for r in reference]
    refhash=digest(calibration/"county_master_rank_reference.csv")
    all_registry=[]; by_scope={}
    for reg in registry:
        definition_path=stage/(root/reg["network_definition"]).relative_to(final)
        definition=read_json(definition_path); net=definition_path.parent
        base=net.parent.parent/"base"
        combination=reg["combination_id"]
        if combination not in by_scope:
            logical=read_csv(base/"truck_logical_arcs_nominal.csv")
            line=read_csv(base/"fixed_path_lineage.csv")
            feature=slope_features(line,profiles)
            ranks_max=midrank([r["smax_percent"] for r in feature],refs_max)
            ranks_mean=midrank([r["smean_percent"] for r in feature],refs_mean)
            by_connection=defaultdict(list)
            for i,row in enumerate(feature):
                row["f_ref_smax"]=float(ranks_max[i]); row["f_ref_smean"]=float(ranks_mean[i])
                row["vulnerability"]=float(cfg["smax_weight"]*ranks_max[i]+cfg["smean_weight"]*ranks_mean[i])
                row["frozen_reference_sha256"]=refhash
                by_connection[row["contracted_logical_connection_id"]].append(row["vulnerability"])
            write_csv(base/"g2_slope_features.csv",feature)
            by_scope[combination]=(logical,{r["truck_route_arc_id"]:r for r in line},
                                   {c:max(v) for c,v in by_connection.items()})
        logical,line,vulnerabilities=by_scope[combination]
        edge_table={r["edge_id"]:r for r in read_csv(base/"physical_edges.csv")}
        backbone={r["edge_id"] for r in read_csv(net/"energy_backbone.csv")}
        site_anchors={r["truck_anchor_id"] for r in read_csv(net/"energy_support_sites.csv")}
        groups=defaultdict(list)
        for a in logical:
            groups[a["contracted_logical_connection_id"]].append(a)
        evidence={}
        for cid,members in sorted(groups.items()):
            endpoints={n for r in members for n in [r["from_node"],r["to_node"]]}
            used={e for r in members for e in json.loads(line[r["arc_id"]]["canonical_physical_edge_ids_json"])}
            protected_site=bool(endpoints&site_anchors)
            exposed=any(edge_table[e]["final_road_class"]=="residential" and e not in backbone for e in used)
            evidence[cid]={"failure_eligible":not protected_site and exposed,
                           "energy_site_endpoint_protected":protected_site,"unprotected_residential_exposure":exposed,
                           "vulnerability":vulnerabilities[cid]}
        srpath=stage/(root/definition["files"]["scenario_registry"]).relative_to(final)
        scenario_rows=[]
        for severity,lam in sorted(cfg["severity_lambda"].items(),key=lambda x:(x[1],x[0])):
            for rep in range(1,cfg["replicates"]+1):
                scenario_id=f"{VERSION}_{severity}_r{rep:03d}"
                out=srpath.parent/scenario_id
                states=[]; cost=[]
                for cid,e in sorted(evidence.items()):
                    damage=lam*e["vulnerability"]
                    pn,pd,pf,q=state_parameters(damage,e["failure_eligible"],cfg)
                    # Common random number across severities and Depot variants.
                    u=stable_uniform(cfg["seed_namespace"],combination,rep,cid)
                    state=0 if u<pn else (1 if u<pn+pd else 2)
                    tm=1. if state==0 else (cfg["degraded_time_min"]+cfg["degraded_time_span"]*q if state==1 else None)
                    em=1. if state==0 else (cfg["degraded_energy_min"]+cfg["degraded_energy_span"]*q if state==1 else None)
                    states.append({"scenario_id":scenario_id,"contracted_logical_connection_id":cid,**e,
                                   "state_code":state,"state":["normal","degraded","failed"][state],"damage":damage,
                                   "p_normal":pn,"p_degraded":pd,"p_failed":pf,"uniform_draw":u,"q":q,
                                   "time_multiplier":tm,"energy_multiplier":em,"generated_not_observed_disruption":True})
                indexed={r["contracted_logical_connection_id"]:r for r in states}
                for a in logical:
                    s=indexed[a["contracted_logical_connection_id"]]; available=s["state_code"]!=2
                    cost.append({"scenario_id":scenario_id,"truck_route_arc_id":a["arc_id"],
                                 "contracted_logical_connection_id":a["contracted_logical_connection_id"],
                                 "available":available,"state_code":s["state_code"],
                                 "distance_m":a["distance_m"] if available else "",
                                 "time_min":float(a["time_min"])*s["time_multiplier"] if available else "",
                                 "truck_energy_kwh":float(a["nominal_energy_kwh"])*s["energy_multiplier"] if available else ""})
                write_csv(out/"connection_states.csv",states)
                write_csv(out/"truck_costs.csv",cost)
                write_json(out/"scenario_definition.json",{"scenario_id":scenario_id,"network_id":reg["network_id"],
                           "generator_version":VERSION,"frozen_reference_sha256":refhash,
                           "interpretation":"generated_experimental_road_disruption_not_observed_event",
                           "drone_raw_costs_changed":False,"random_node_state_generated":False})
                scenario_rows.append({"network_id":reg["network_id"],"scenario_id":scenario_id,"generator_version":VERSION,
                                      "scenario_path":str((final/out.relative_to(stage)).relative_to(root)).replace("\\","/")})
        write_csv(srpath,scenario_rows)
        all_registry.extend(scenario_rows)
        definition["counts"]["scenario_count"]=len(scenario_rows)
        definition["publication_status"]="network_and_generated_research_scenarios_published"
        definition["files"]["g2_frozen_rank_reference"]=str((final/calibration.relative_to(stage)/"county_master_rank_reference.csv").relative_to(root)).replace("\\","/")
        definition["files"]["g2_parameter_snapshot"]=str((final/calibration.relative_to(stage)/"parameter_snapshot.json").relative_to(root)).replace("\\","/")
        definition["known_paths"]=dict(definition["files"])
        write_json(definition_path,definition)
        reg["scenario_count"]=len(scenario_rows); reg["publication_status"]=definition["publication_status"]
    write_csv(stage/"registry/scenario_registry.csv",all_registry)
    write_json(calibration/"validation.json",{"passed":True,"reference_rows":len(reference),"reference_sha256":refhash,
               "cdf":"(#ref<x + 0.5*#ref=x)/N","rank_domain":"whole_region_not_each_scope","scenario_count":len(all_registry)})
    return {"enabled":True,"scenario_count":len(all_registry),"reference_rows":len(reference),"reference_sha256":refhash}
