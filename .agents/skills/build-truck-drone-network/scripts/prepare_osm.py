"""Offline extraction of a regional OSM road vector from an archived file.

Accepts Geofabrik .osm.pbf (GDAL OSM driver), GeoPackage, GeoJSON or Shapefile.
Does not download, snap, infer roads, or infer legal heavy-truck access.
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime,timezone
from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely import make_valid

from portable_core import GateError,digest,write_json


def hstore(value):
    if not isinstance(value,str):
        return {}
    pattern=r'"((?:\\.|[^"\\])*)"\s*=>\s*"((?:\\.|[^"\\])*)"'
    return {k.replace('\\"','"'):v.replace('\\"','"').replace('\\\\','\\') for k,v in re.findall(pattern,value)}


def prepare(args):
    source=args.source.resolve(); out=args.output.resolve()
    if out.exists() or out==source or "raw" in [p.lower() for p in out.parts[-3:-1]]:
        raise GateError("Prepared output must be a new file outside the immutable raw directory")
    boundary=gpd.read_file(args.boundary,layer=args.boundary_layer)
    if boundary.crs is None:
        raise GateError("Boundary CRS not defined")
    if args.selector_field:
        boundary=boundary.loc[boundary[args.selector_field].astype(str)==args.selector_value]
    if boundary.empty:
        raise GateError("Empty administrative selection")
    clip=gpd.GeoSeries([boundary.to_crs(args.crs).union_all().buffer(args.buffer_m)],crs=args.crs)
    layer=args.layer or ("lines" if source.suffix.lower()==".pbf" else None)
    info=pyogrio.read_info(source,layer=layer)
    if not info["crs"]:
        raise GateError("Road source CRS not defined")
    bounds=tuple(clip.to_crs(info["crs"]).total_bounds)
    roads=gpd.read_file(source,layer=layer,bbox=bounds)
    tags=roads["other_tags"].map(hstore) if "other_tags" in roads else [dict() for _ in range(len(roads))]
    aliases={"osm_id":["osm_id","osm_way_id","id"],"highway":["highway","fclass","road_class"],
             "name":["name"],"ref":["ref"],"access":["access"],"oneway":["oneway"],"layer":["layer"],
             "bridge":["bridge"],"tunnel":["tunnel"],"junction":["junction"],"motor_vehicle":["motor_vehicle"],
             "maxweight":["maxweight"],"maxwidth":["maxwidth"],"surface":["surface"]}
    for target,names in aliases.items():
        field=next((n for n in names if n in roads),None)
        existing=roads[field].fillna("").astype(str).tolist() if field else [""]*len(roads)
        roads[target]=[value or tag.get(target,"") for value,tag in zip(existing,tags)]
    roads=roads.loc[roads["highway"]!=""].copy().to_crs(args.crs)
    roads["geometry"]=roads.geometry.map(make_valid).intersection(clip.iloc[0])
    roads=roads.loc[~roads.geometry.is_empty].copy()
    if roads.empty or (roads["osm_id"]=="").any():
        raise GateError("No road data or missing source OSM identifiers; do not invent IDs")
    roads=roads[[*aliases,"geometry"]].sort_values(["osm_id","highway","name"]).reset_index(drop=True)
    out.parent.mkdir(parents=True,exist_ok=True)
    roads.to_file(out,layer="roads",driver="GPKG")
    audit={"source_path":str(source),"source_sha256":digest(source),"source_url":args.source_url,
           "license":"Open Database License (ODbL); © OpenStreetMap contributors",
           "boundary_path":str(args.boundary.resolve()),"boundary_sha256":digest(args.boundary),
           "generated_at":datetime.now(timezone.utc).isoformat(),"output_path":str(out),"output_sha256":digest(out),
           "processing_crs":args.crs,"context_buffer_m":args.buffer_m,"feature_count":len(roads),
           "missing_access_tag_count":int((roads["access"]=="").sum()),
           "missing_oneway_tag_count":int((roads["oneway"]=="").sum()),
           "warning":"Missing tags are unknown, not evidence of 28 t legal/physical access. No coordinates fabricated."}
    write_json(out.with_suffix(".provenance.json"),audit)
    return audit


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",type=Path,required=True); p.add_argument("--boundary",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True); p.add_argument("--crs",required=True)
    p.add_argument("--source-url",required=True); p.add_argument("--buffer-m",type=float,required=True)
    p.add_argument("--layer"); p.add_argument("--boundary-layer"); p.add_argument("--selector-field"); p.add_argument("--selector-value")
    import json
    print(json.dumps(prepare(p.parse_args()),indent=2))
