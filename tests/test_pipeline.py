from argparse import Namespace
import copy
import json
import math

import geopandas as gpd
import numpy as np
import pytest
from pyproj import CRS
from shapely.geometry import LineString

from build_demand_master import build_demand_master
from create_demo_inputs import create
from generate_scenarios import slope_features,state_parameters
from portable_core import DEMSampler,GateError,flight_cost,goods_kg,midrank,read_csv,read_json,shortest_paths,slope_metrics,truck_energy,write_json
from portable_roads import assess,fill_secondary_gaps
from road_core import build_graph_tables,noded_segments
from run_network_pipeline import logical_network,run,validate_profile
from validate_pipeline import validate_release
from package_dataset import package


def arc(a,b,name,d=100.,t=1.,energy=1.):
    return {"arc_id":name,"edge_id":name,"from_node":a,"to_node":b,"distance_m":d,"time_min":t,
            "truck_energy_kwh":energy,"cumulative_ascent_m":0.,"cumulative_descent_m":0.}


def snaps(bad_count):
    return [{"terminal_id":"D" if i==0 else f"E{i}","truck_anchor_id":str(i),"distance_covered":i>bad_count or i==0} for i in range(11)]


def test_exact_twenty_percent_and_depot_excluded():
    arcs=[arc("0",str(i),f"a{i}") for i in range(1,11)]+[arc(str(i),"0",f"b{i}") for i in range(1,11)]
    g=assess(snaps(2),arcs,"D",.2)
    assert g["demand_denominator"]==10 and g["inaccessible_count"]==2 and not g["qualified"]
    assert assess(snaps(1),arcs,"D",.2)["qualified"]
    s=snaps(0); s[0]["distance_covered"]=False
    assert not assess(s,arcs,"D",.2)["qualified"]


def test_disconnected_near_road_is_bad():
    s=snaps(0)
    arcs=[arc("0",str(i),f"a{i}") for i in range(1,10)]+[arc(str(i),"0",f"b{i}") for i in range(1,10)]+[arc("10","isolated","i")]
    g=assess(s,arcs,"D",.2)
    assert g["inaccessible_ids"]==["E10"]


def test_strict_slicing_recomputes_blockers_and_tie_break():
    arcs=[arc("A","B","ab"),arc("B","C","bc"),arc("B","A","ba"),arc("C","B","cb")]
    terms=[{"terminal_id":n,"truck_anchor_id":n,"truck_service_status":"active"} for n in "ABC"]
    original,_,_=logical_network(arcs,terms,"ABC")
    assert len(original)==4
    sliced,lineage,_=logical_network(arcs,[terms[0],terms[2]],"AC")
    assert len(sliced)==2 and lineage[0]["canonical_directed_physical_arc_ids_json"]==["ab","bc"]
    ties=[arc("A","B","z",d=10),arc("A","B","a",d=10),arc("A","B","shorter",d=9)]
    assert shortest_paths(ties,"A")["B"][2]==("shorter",)
    assert shortest_paths(ties[:2],"A")["B"][2]==("a",)


def test_reverse_energy_and_rounding():
    cfg={"flat_kwh_per_km":1.03,"mass_kg":28000.,"gravity_m_s2":9.81,"uphill_efficiency":.9}
    assert truck_energy(1000,100,cfg)>truck_energy(1000,0,cfg)>0
    assert goods_kg("97.0383343707",2)=="194.077"
    assert goods_kg(".00025",2)=="0.001"


def test_slope_within_edge_only_and_midrank():
    p={"a":{"sample_distances_m_json":"[0,10]","smoothed_elevations_m_json":"[100,101]"},
       "b":{"sample_distances_m_json":"[0,10]","smoothed_elevations_m_json":"[1000,1002]"}}
    r=slope_features([{"contracted_logical_arc_id":"x","contracted_logical_connection_id":"c","canonical_physical_edge_ids_json":["a","b"]}],p)[0]
    assert r["smax_percent"]==20 and r["smean_percent"]==15
    assert np.allclose(midrank([1,2,3],[1,2,2,3]),[.125,.5,.875])
    with pytest.raises(GateError):
        slope_metrics([0,0],[1,2])


def test_roundabout_preserves_directed_cycle():
    geometry=LineString([(0,0),(100,0),(100,100),(0,100),(0,0)])
    records=[{"geometry":geometry,"grade_key":"0","source_id":"ring","road_class":"residential","oneway":"yes"}]
    segments=noded_segments(records,.001)
    nodes,edges=build_graph_tables(segments,CRS(2193),set(),6)
    assert len(edges)==2
    directed=[]
    for r in edges.to_dict("records"):
        assert r["forward_allowed"]!=r["reverse_allowed"]
        directed.append((r["from_node"],r["to_node"]) if r["forward_allowed"] else (r["to_node"],r["from_node"]))
    assert directed[0]==directed[1][::-1]


def test_gap_fill_preserves_branches():
    rows=[{"edge_id":"a","from_node":"A","to_node":"B","length_m":10.,"final_road_class":"secondary"},
          {"edge_id":"b","from_node":"B","to_node":"C","length_m":200.,"final_road_class":"residential"},
          {"edge_id":"c","from_node":"C","to_node":"D","length_m":10.,"final_road_class":"secondary"}]
    assert fill_secondary_gaps(rows,750)[1]["final_road_class"]=="secondary"


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    root=tmp_path_factory.mktemp("portable-parent")/"demo"
    path=create(root)
    args=Namespace(project_root=root,profile=path,stage="full",output_root=None,mother_root=None)
    first=run(args)
    return root,path,first


def test_end_to_end_and_determinism(completed):
    root,path,first=completed
    assert first["status"]=="published" and first["network_count"]==2
    release=root/"network/processed/synthetic_demo/release_v1"
    valid=validate_release(release,root)
    assert valid["passed"] and valid["mother_terminals"]==9 and valid["scenario_count"]==12
    second=run(Namespace(project_root=root,profile=path,stage="full",output_root="network/processed/synthetic_demo/release_repeat",mother_root=None))
    assert second["status"]=="published"
    one=read_json(release/"registry/run_manifest.json")["core_csv_hashes"]
    two=read_json(root/"network/processed/synthetic_demo/release_repeat/registry/run_manifest.json")["core_csv_hashes"]
    # Registry paths are deliberately release-specific; science tables are not.
    science=lambda d:{k:v for k,v in d.items() if not k.startswith("registry/") and not k.endswith("scenario_registry.csv")}
    assert science(one)==science(two)
    assert one["registry/mother_network/physical_edges.csv"]==two["registry/mother_network/physical_edges.csv"]


def test_mother_reuse_without_dem_road_recalculation(completed):
    root,path,_=completed
    result=run(Namespace(project_root=root,profile=path,stage="full",output_root="network/processed/synthetic_demo/reused",
                         mother_root="network/processed/synthetic_demo/release_v1"))
    assert result["status"]=="published"
    manifest=read_json(root/"network/processed/synthetic_demo/reused/registry/run_manifest.json")
    assert manifest["mother"]["reused"] and manifest["new_amap_requests"]==0


def test_reserve_is_single_leg_not_roundtrip(completed):
    root,path,_=completed; cfg=read_json(path)["drone"]
    leg=flight_cost(6000,100,100,100,"full",cfg)
    back=flight_cost(6000,100,100,100,"empty",cfg)
    assert leg["arc_energy_necessary_feasible"]
    assert leg["energy_kwh"]+back["energy_kwh"]>cfg["nominal_battery_kwh"]*.8
    assert close(leg["energy_kwh"],leg["horizontal_energy_kwh"]+leg["vertical_energy_kwh"])


def close(a,b):
    return math.isclose(a,b,rel_tol=1e-10)


def test_existing_output_never_overwritten(completed):
    root,path,_=completed
    with pytest.raises(GateError,match="new, unprotected"):
        run(Namespace(project_root=root,profile=path,stage="full",output_root=None,mother_root=None))


def test_missing_nz_parameters_fail(completed):
    root,path,_=completed; profile=read_json(path)
    profile["energy"]["annual_household_kwh"]=None
    with pytest.raises(GateError,match="annual_household_kwh"):
        validate_profile(profile,root)


def test_transfer_bundle_preserves_raw_and_mother(completed):
    import zipfile
    root,path,_=completed
    out=root/"deliverables/demo.zip"
    result=package(root,root/"network/processed/synthetic_demo/release_v1",out,True)
    assert result["omitted_input_count"]==0
    with zipfile.ZipFile(out) as z:
        names=set(z.namelist())
        assert "data/raw/synthetic_demo/population.tif" in names
        assert "network/processed/synthetic_demo/release_v1/registry/mother_network/physical_edges.csv" in names
        assert ".agents/skills/build-truck-drone-network/SKILL.md" in names
        assert "network_pipeline.py" in names
        assert "DATASET_BUNDLE_MANIFEST.json" in names
        assert "parameters/region_profile_snapshot.json" in names
