"""Raw role-aware flights and optional customer-to-customer candidate matrices."""
from __future__ import annotations

import itertools
import json

import numpy as np
from shapely.geometry import LineString

from portable_core import GateError, flight_cost, stable_id, write_csv


def build_drone(base, terminals, sampler, config, combination_id, parameter_hash):
    terminals = sorted(terminals,key=lambda r:r["terminal_id"])
    n = len(terminals)
    expected = 2*n*n
    if expected > config["max_raw_legs"]:
        raise GateError(f"Drone raw-leg size {expected} exceeds configured limit; no silent demand thinning")
    rows, corridors, roundtrips, candidates = [], [], [], []
    cache = {}

    def geometry_profile(start,end):
        key = tuple(sorted((tuple(start),tuple(end))))
        if key not in cache:
            line = LineString(key)
            if line.length < 1e-9:
                dd, zz = np.array([0.]), sampler.sample([key[0]])
            else:
                dd, zz = sampler.line_profile(line,config["dem_interval_m"])
            cache[key] = (line,dd,zz)
        line,dd,zz = cache[key]
        reverse = tuple(start) != key[0]
        return line.length,float(zz[-1] if reverse else zz[0]),float(zz[0] if reverse else zz[-1]),float(zz.max()),dd,zz

    for anchor in terminals:
        for customer in terminals:
            start = (anchor["anchor_x_m"],anchor["anchor_y_m"])
            end = (customer["delivery_x_m"],customer["delivery_y_m"])
            distance,z0,z1,zmax,dd,zz = geometry_profile(start,end)
            aid,cid = anchor["terminal_id"],customer["terminal_id"]
            corridor_id = stable_id("FLIGHT_",combination_id,start,end,parameter_hash)
            corridors.append({"corridor_id":corridor_id,"anchor_terminal_id":aid,"customer_terminal_id":cid,
                              "anchor_x_m":start[0],"anchor_y_m":start[1],"delivery_x_m":end[0],"delivery_y_m":end[1],
                              "sample_distances_m_json":dd.tolist(),"sample_elevations_m_json":zz.tolist(),
                              "sample_order":"lexicographically_sorted_endpoint_coordinates",
                              "geometry_wkt_m":LineString([start,end]).wkt})
            costs = {}
            for payload,reverse in [("full",False),("empty",True)]:
                value = flight_cost(distance,z1 if reverse else z0,z0 if reverse else z1,zmax,payload,config)
                costs[payload] = value
                rows.append({"leg_id":stable_id("LEG_",combination_id,payload,aid,cid,start,end,parameter_hash),
                             "combination_id":combination_id,"payload_state":payload,
                             "anchor_terminal_id":aid,"customer_terminal_id":cid,
                             "road_anchor_node_id":anchor["truck_anchor_id"],
                             "customer_component_id":customer["source_county_component_id"],
                             "origin_terminal_id":cid if reverse else aid,"destination_terminal_id":aid if reverse else cid,
                             "origin_coordinate_role":"customer_centroid" if reverse else "truck_anchor",
                             "destination_coordinate_role":"truck_anchor" if reverse else "customer_centroid",
                             "corridor_id":corridor_id,"raw_total_time_s":value["time_s"],
                             "raw_total_energy_kwh":value["energy_kwh"],"reserve_applied_to_raw_leg":False,
                             "parameter_hash":parameter_hash,"candidate_only_not_approved_route":True,**value})
            energy = costs["full"]["energy_kwh"]+costs["empty"]["energy_kwh"]
            roundtrips.append({"anchor_terminal_id":aid,"customer_terminal_id":cid,"energy_kwh":energy,
                               "time_s":costs["full"]["time_s"]+costs["empty"]["time_s"],
                               "roundtrip_energy_necessary_feasible":energy <= config["nominal_battery_kwh"]*(1-config["reserve_fraction"]),
                               "reserve_fraction":config["reserve_fraction"],"definition":"full_out_empty_return_same_anchor"})
    if len(rows) != expected:
        raise GateError("Role-aware raw flight cardinality mismatch")
    write_csv(base/"drone_cost_arcs.csv",rows)
    write_csv(base/"drone_corridors.csv",corridors)
    write_csv(base/"drone_full_out_empty_return.csv",roundtrips)
    if config["publish_customer_pair_candidates"]:
        for a,b in itertools.combinations(terminals,2):
            start=(a["delivery_x_m"],a["delivery_y_m"])
            end=(b["delivery_x_m"],b["delivery_y_m"])
            distance,z0,z1,zmax,_,_ = geometry_profile(start,end)
            for origin,destination,up,down in [(a,b,z0,z1),(b,a,z1,z0)]:
                for payload in ["empty","full"]:
                    value=flight_cost(distance,up,down,zmax,payload,config)
                    candidates.append({"origin_terminal_id":origin["terminal_id"],"destination_terminal_id":destination["terminal_id"],
                                       "payload_state":payload,"coordinate_role":"customer_centroid_both_ends",
                                       "candidate_only_not_approved_route":True,**value})
        write_csv(base/"drone_customer_pair_candidates.csv",candidates)
    ids=[t["terminal_id"] for t in terminals]
    for payload in ["empty","full"]:
        lookup={(r["anchor_terminal_id"],r["customer_terminal_id"]):r for r in rows if r["payload_state"]==payload}
        for metric in ["raw_total_energy_kwh","raw_total_time_s"]:
            write_csv(base/f"drone_{payload}_{metric}_matrix.csv",
                      [{"anchor_terminal_id":a,**{c:lookup[(a,c)][metric] for c in ids}} for a in ids])
    return {"drone_raw_leg_count":len(rows),"drone_expected_2NN":expected,
            "drone_customer_candidate_count":len(candidates),"sampled_corridor_count":len(cache)}
