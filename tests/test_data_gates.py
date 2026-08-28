from argparse import Namespace
import copy
import os

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString,Point

from build_demand_master import build_demand_master
from create_demo_inputs import create
from download_inputs import download
from portable_core import DEMSampler,GateError,read_json,write_json
from portable_roads import insert_services
from prepare_osm import hstore,prepare
from road_core import build_graph_tables,noded_segments
from pyproj import CRS
from run_network_pipeline import run


def test_200m_exact_boundary():
    records=[{"geometry":LineString([(0,0),(1000,0)]),"grade_key":"0","source_id":"real","road_class":"residential","oneway":"no"}]
    nodes,edges=build_graph_tables(noded_segments(records,.001),CRS(2193),set(),6)
    services=[{"terminal_id":"a","access_reference_x_m":250.,"access_reference_y_m":200.},
              {"terminal_id":"b","access_reference_x_m":750.,"access_reference_y_m":200.0001}]
    cfg={"junction_priority":False,"coordinate_decimals":6,"access_threshold_m":200.}
    _,new_edges,snaps=insert_services(nodes,edges,services,cfg)
    assert snaps[0]["distance_covered"] and not snaps[1]["distance_covered"]
    assert all(e["geometry"].distance(LineString([(0,0),(1000,0)]))==0 for e in new_edges)


def test_diagonal_8_neighbour_component_and_depot_exclusion(tmp_path):
    root=tmp_path/"d"; path=create(root); profile=read_json(path)
    raw=root/"data/raw/synthetic_demo/population.tif"
    with rasterio.open(raw) as src:
        data=src.read(1); meta=src.profile
    data[3,3]=5.  # diagonal to the Depot cell [2,2], not a new component
    alternate=raw.with_name("diagonal.tif")
    with rasterio.open(alternate,"w",**meta) as dst:
        dst.write(data,1)
    profile["demand"]["population_raster_path"]=str(alternate.relative_to(root))
    p=root/"parameters/diagonal.json"; write_json(p,profile)
    audit=build_demand_master(project_root=root,profile_path=p,output_dir=root/"audit_diagonal")
    assert audit["node_counts"]["population_components_including_depot"]==9
    assert audit["node_counts"]["demand_nodes_excluding_depot"]==8
    assert audit["population_conservation"]["final_component_population_total"]==pytest.approx(1080)


def test_50_to_100m_population_mass_conservation(tmp_path):
    root=tmp_path/"d"; path=create(root); profile=read_json(path)
    original=root/"data/raw/synthetic_demo/population.tif"
    with rasterio.open(original) as src:
        data=src.read(1); meta=src.profile
    data=np.repeat(np.repeat(data,2,axis=0),2,axis=1)/4
    alternate=original.with_name("population_50m.tif")
    meta.update(width=40,height=40,transform=from_origin(1300000,5002000,50,50))
    with rasterio.open(alternate,"w",**meta) as dst:
        dst.write(data,1)
    profile["demand"]["population_raster_path"]=str(alternate.relative_to(root))
    p=root/"parameters/resample.json"; write_json(p,profile)
    audit=build_demand_master(project_root=root,profile_path=p,output_dir=root/"audit_resample")
    mass=audit["population_conservation"]
    assert audit["grid"]["resampling"]=="rasterio.enums.Resampling.sum"
    assert mass["source_total_in_boundary"]==pytest.approx(540)
    assert mass["target_total_before_threshold"]==pytest.approx(540)
    assert mass["final_component_population_total"]==pytest.approx(1080)


def test_dem_zero_valid_and_nodata_blocker(tmp_path):
    path=tmp_path/"zero.tif"
    meta={"driver":"GTiff","width":10,"height":10,"count":1,"dtype":"float32","crs":"EPSG:2193", "transform":from_origin(0,100,10,10),"nodata":-9999}
    data=np.zeros((10,10),dtype="float32")
    with rasterio.open(path,"w",**meta) as dst:
        dst.write(data,1)
    sampler=DEMSampler(path,"EPSG:2193","m","synthetic")
    try:
        assert sampler.sample([(50,50)]).tolist()==[0.]
        with pytest.raises(GateError,match="interpolation margin"):
            sampler.sample([(-10,50)])
    finally:
        sampler.close()
    masked=tmp_path/"nodata.tif"; data[4,4]=-9999
    with rasterio.open(masked,"w",**meta) as dst:
        dst.write(data,1)
    sampler=DEMSampler(masked,"EPSG:2193","m","synthetic")
    try:
        with pytest.raises(GateError,match="nodata"):
            sampler.sample([(50,50)])
    finally:
        sampler.close()


def test_nz_osm_failure_and_licensed_fallback_without_api(tmp_path,monkeypatch):
    root=tmp_path/"d"; path=create(root); profile=read_json(path)
    original=root/profile["roads"]["osm"]["path"]
    roads=gpd.read_file(original)
    roads["access"]="private"
    failed=original.with_name("private_roads.gpkg"); roads.to_file(failed,layer="roads",driver="GPKG")
    for item in profile["inputs"]:
        if item["role"]=="roads":
            item["path"]=str(failed.relative_to(root))
    profile["roads"]["osm"]["path"]=str(failed.relative_to(root))
    profile["scenarios"]["enabled"]=False
    def guarded_getenv(key,*args):
        assert "AMAP" not in key.upper()
        return original_getenv(key,*args)
    original_getenv=os.getenv; monkeypatch.setattr(os,"getenv",guarded_getenv)
    p=root/"parameters/no_roads.json"; write_json(p,profile)
    with pytest.raises(GateError,match="licensed_local_source_required"):
        run(Namespace(project_root=root,profile=p,stage="mother",output_root="network/processed/fail",mother_root=None))
    assert not (root/"network/processed/fail").exists()
    local=copy.deepcopy(profile["roads"]["osm"])
    local.update(path=str(original.relative_to(root)),provider_name="SYNTHETIC_LICENSED_LOCAL",license_verified=True)
    profile["roads"]["licensed"]=local
    profile["inputs"].append({**profile["inputs"][3],"role":"licensed_roads","path":local["path"]})
    write_json(p,profile)
    result=run(Namespace(project_root=root,profile=p,stage="mother",output_root="network/processed/local",mother_root=None))
    assert result["status"]=="published"
    gates=read_json(root/"network/processed/local/registry/road_source_assessment.json")
    assert len(gates)==2 and gates[-1]["qualified"]
    assert gates[-1]["provider"]=="SYNTHETIC_LICENSED_LOCAL"


def test_pbf_tag_parser_and_vector_preparation(tmp_path):
    assert hstore('"access"=>"private","oneway"=>"-1","name"=>"A \\"B\\""')["oneway"]=="-1"
    root=tmp_path/"d"; path=create(root)
    out=root/"data/prepared/roads.gpkg"
    args=Namespace(source=root/"data/raw/synthetic_demo/roads.gpkg",boundary=root/"data/raw/synthetic_demo/boundary.gpkg",
                   output=out,crs="EPSG:2193",source_url="synthetic://fixture",buffer_m=0.,layer="roads",boundary_layer="boundary",selector_field=None,selector_value=None)
    audit=prepare(args)
    assert audit["feature_count"]==6 and out.exists()


def test_manual_download_and_credentials_not_catalogued(tmp_path):
    catalog={"datasets":[{"role":"dem","website":"https://data.linz.govt.nz/","data_type":"DEM GeoTIFF","download_url":None}]}
    assert download(tmp_path,catalog)[0]["status"]=="manual_download_required"
    bad={"datasets":[{"role":"x","download_url":"https://example.com/x?token=placeholder","allowed_hosts":["example.com"]}]}
    with pytest.raises(GateError,match="credential-bearing"):
        download(tmp_path,bad)
