"""Create explicitly SYNTHETIC raw GIS inputs for an offline end-to-end test.

Never use this fixture as evidence of an actual NZ settlement, road or DEM.
The destination must be a new directory. No existing input is replaced.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString,box

from portable_core import GateError,write_json


def create(root):
    root=Path(root).resolve()
    if root.exists():
        raise GateError("Demo destination already exists; choose a new one")
    raw=root/"data/raw/synthetic_demo"; raw.mkdir(parents=True)
    x,y=1300000.,5000000.
    crs="EPSG:2193"
    boundary=gpd.GeoDataFrame({"region_id":["SYNTHETIC"]},geometry=[box(x,y,x+2000,y+2000)],crs=crs)
    boundary.to_file(raw/"boundary.gpkg",layer="boundary",driver="GPKG")
    units=gpd.GeoDataFrame({"unit_id":["WEST","EAST"]},geometry=[box(x,y,x+1200,y+2000),box(x+1200,y,x+2000,y+2000)],crs=crs)
    units.to_file(raw/"units.gpkg",layer="units",driver="GPKG")
    pop=np.zeros((20,20),dtype="float32")
    for n,(r,c) in enumerate((r,c) for r in [2,9,16] for c in [2,9,16]):
        pop[r,c]=20+10*n
    with rasterio.open(raw/"population.tif","w",driver="GTiff",width=20,height=20,count=1,dtype=pop.dtype,crs=crs,
                       transform=from_origin(x,y+2000,100,100),nodata=-9999) as dst:
        dst.write(pop,1)
    # 25 m DEM includes the full rectangular flight envelope and sampling margin.
    yy,xx=np.meshgrid(np.arange(120),np.arange(120),indexing="ij")
    dem=(120+.9*xx+.6*yy+15*np.sin(xx/8)).astype("float32")
    with rasterio.open(raw/"dem.tif","w",driver="GTiff",width=120,height=120,count=1,dtype=dem.dtype,crs=crs,
                       transform=from_origin(x-500,y+2500,25,25),nodata=-9999) as dst:
        dst.write(dem,1)
    road_rows=[]
    for i,dy in enumerate([1720,1020,320]):
        road_rows.append({"osm_id":f"fixture_h{i}","highway":"secondary" if i==1 else "residential",
                          "oneway":"no","access":"yes","name":f"Synthetic row {i}",
                          "geometry":LineString([(x+100,y+dy),(x+1900,y+dy)])})
    for i,dx in enumerate([220,920,1620]):
        road_rows.append({"osm_id":f"fixture_v{i}","highway":"residential","oneway":"no","access":"yes",
                          "name":f"Synthetic column {i}","geometry":LineString([(x+dx,y+100),(x+dx,y+1900)])})
    gpd.GeoDataFrame(road_rows,crs=crs).to_file(raw/"roads.gpkg",layer="roads",driver="GPKG")
    profile={
      "schema_version":"portable-profile-v2","profile_version":"synthetic-demo-v2","synthetic_fixture":True,
      "approval":{"status":"approved_for_preprocessing","authority":"synthetic_regression_fixture_only"},
      "region":{"region_id":"SYNTHETIC","name":"Synthetic test, not a real NZ area","country_code":"NZ","processing_crs":crs},
      "execution":{"python_interpreter":"current","create_environment":False,"output_root":"network/processed/synthetic_demo/release_v1","max_scope_terminals":100},
      "demand":{"boundary_path":"data/raw/synthetic_demo/boundary.gpkg","boundary_layer":"boundary",
                "population_raster_path":"data/raw/synthetic_demo/population.tif","target_projected_crs":crs,
                "target_resolution_m":100,"native_resolution_tolerance_fraction":.2,"connectivity":8,
                "population_threshold":0.,"boundary_cell_inclusion":"cell_center_all_touched_false",
                "official_population_total":1080.,"node_id_prefix":"DEMO_E","stable_order":"minimum_component_raster_row_then_minimum_column",
                "depot":{"id":"DEMO_DEPOT","x":x+250,"y":y+1750,"crs":crs}},
      "units":{"path":"data/raw/synthetic_demo/units.gpkg","layer":"units","id_field":"unit_id"},
      "dem":{"path":"data/raw/synthetic_demo/dem.tif","vertical_unit":"m","vertical_datum":"synthetic_local_elevation_not_real_NZVD2016"},
      "roads":{"access_threshold_m":200,"fallback_ratio":.2,"context_buffer_m":0.,"minimum_segment_m":.01,
               "coordinate_decimals":6,"junction_priority":True,"junction_radius_m":100.,"junction_extra_m":30.,
               "secondary_gap_max_m":750.,"excluded_classes":["footway","path","cycleway","steps","pedestrian","bridleway"],
               "excluded_access":["no","private"],
               "osm":{"path":"data/raw/synthetic_demo/roads.gpkg","layer":"roads","provider_name":"SYNTHETIC_OSM_STYLE",
                        "source_url":"synthetic://create_demo_inputs","license":"synthetic fixture authored in this repository",
                        "secondary_classes":["secondary"],"fields":{"id":"osm_id","class":"highway","name":"name","oneway":"oneway","access":"access"}}},
      "truck":{"vehicle":"eActros 600 research proxy","mass_kg":28000.,"flat_kwh_per_km":1.03,"gravity_m_s2":9.81,"uphill_efficiency":.9,
               "speeds_kmh":{"secondary":35.,"residential":15.},"dem_interval_m":40.,"evidence_status":"synthetic_test_reuses_Wuding_scenario_not_NZ_vehicle_observation"},
      "energy":{"people_per_household":3.,"annual_household_kwh":9000.,"annual_hours":8760.,"outage_hours":12.,"critical_fraction":.35,
                "evidence_status":"invented_for_software_test_not_NZ_statistics"},
      "goods":{"kg_per_person_24h":2.,"evidence_status":"research_rule"},
      "microgrid":{"coverage_time_min":45.,"coverage_distance_m":10000.,"max_candidates":100,"solver_time_limit_s":30.,"objective_tolerance":1e-6,
                   "coverage_meaning":"road_service_cluster_not_physical_electricity_distribution"},
      "drone":{"platform":"DJI FlyCart 200 four-battery proxy","nominal_battery_kwh":9.60848,"cruise_m_s":20.,"climb_m_s":3.,"descent_m_s":3.,
               "clearance_m":60.,"maximum_altitude_m":6000.,"dem_interval_m":30.,"reserve_fraction":.2,"max_raw_legs":20000,
               "publish_customer_pair_candidates":True,"states":{"empty":{"payload_kg":0.,"depletion_range_km":36.,"depletion_hover_min":25.},
               "full":{"payload_kg":200.,"depletion_range_km":10.,"depletion_hover_min":7.}},"evidence_status":"official_endpoint_calibrated_planning_proxy_not_measured_flight"},
      "scopes":[{"combination_id":"DEMO_C2_WEST_EAST","directory":"2_town/west_east","unit_ids":["WEST","EAST"],"depot_terminal_ids":["DEMO_DEPOT"]},
                {"combination_id":"DEMO_C1_WEST","directory":"1_town/west","unit_ids":["WEST"],"depot_terminal_ids":["DEMO_DEPOT"]}],
      "scenarios":{"enabled":True,"approval_status":"approved_research_scenario","replicates":2,"maximum_replicates":10,
                   "severity_lambda":{"mild":.2,"moderate":.5,"severe":.8},"smax_weight":.7,"smean_weight":.3,
                   "sigmoid_steepness":10.,"degrade_midpoint":.25,"failure_midpoint":.55,"q_damage_min":0.,"q_damage_max":.8,
                   "degraded_time_min":1.1,"degraded_time_span":1.9,"degraded_energy_min":1.05,"degraded_energy_span":.95,
                   "seed_namespace":"synthetic_portable_test_v2","evidence_status":"research_generated_not_disaster_record"},
      "inputs":[]}
    for role,name,unit in [("boundary","boundary.gpkg","projected metres"),("population","population.tif","people_per_cell"),
                           ("dem","dem.tif","m"),("roads","roads.gpkg","projected metres"),("units","units.gpkg","projected metres")]:
        profile["inputs"].append({"role":role,"path":"data/raw/synthetic_demo/"+name,"source_url":"synthetic://create_demo_inputs",
                                  "source_name":"SYNTHETIC TEST ONLY","license":"synthetic fixture authored in this repository",
                                  "acquired_at":"2026-08-28T00:00:00Z","units":unit,"redistribution_permitted":True})
    path=root/"parameters/synthetic_demo.json"; write_json(path,profile)
    return path


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    print(create(parser.parse_args().output))
