#!/usr/bin/env python3
"""Build a source-neutral, traceable canonical road graph from OSM vectors.

The script is deliberately offline.  It reads an already archived OSM-derived
vector layer and an administrative boundary, filters non-motor/access-restricted
features, nodes same-grade linework, and writes deterministic node/edge tables.
It does not download OSM data and it never invents AMap evidence fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import geopandas as gpd
import networkx as nx
import pandas as pd
from pyproj import CRS
from shapely import make_valid
from shapely.geometry import GeometryCollection, LineString, MultiLineString, Point
from shapely.ops import unary_union, substring
from shapely.strtree import STRtree


EXCLUDED_HIGHWAYS = {
    "footway",
    "path",
    "cycleway",
    "steps",
    "pedestrian",
    "bridleway",
}
EXCLUDED_ACCESS = {"no", "private"}
DEFAULT_SECONDARY_CLASSES = {"secondary", "secondary_link"}
OUTPUT_FILES = (
    "canonical_road_nodes.csv",
    "canonical_road_nodes.geojson",
    "canonical_road_edges.csv",
    "canonical_road_edges.geojson",
    "canonical_manifest.json",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deterministic OSM canonical road nodes and edges."
    )
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--roads", type=Path, help="Override the OSM road vector path.")
    parser.add_argument(
        "--boundary", type=Path, help="Override the administrative boundary path."
    )
    parser.add_argument(
        "--processing-crs",
        help="Override the projected processing CRS (for example EPSG:32648).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace only this script's named outputs if they already exist.",
    )
    return parser.parse_args(argv)


def nested(mapping: dict[str, Any], *paths: str, default: Any = None) -> Any:
    """Return the first non-null value found at a dotted path."""

    for dotted in paths:
        value: Any = mapping
        found = True
        for key in dotted.split("."):
            if not isinstance(value, dict) or key not in value:
                found = False
                break
            value = value[key]
        if found and value is not None:
            return value
    return default


def resolve_path(project_root: Path, value: str | Path | None, label: str) -> Path:
    if value is None or str(value).strip() == "":
        raise ValueError(f"Missing required {label} path in CLI/profile.")
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iter_lines(geometry: Any) -> Iterator[LineString]:
    """Yield non-empty LineStrings from any repaired line-like geometry."""

    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, LineString):
        yield geometry
    elif isinstance(geometry, MultiLineString):
        yield from geometry.geoms
    elif isinstance(geometry, GeometryCollection):
        for part in geometry.geoms:
            yield from iter_lines(part)


def clean_tag(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def tag_tokens(value: Any) -> set[str]:
    text = clean_tag(value).lower().replace(",", ";")
    return {token.strip() for token in text.split(";") if token.strip()}


def first_existing(columns: Iterable[str], candidates: Iterable[str]) -> str | None:
    lookup = {str(column).lower(): str(column) for column in columns}
    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]
    return None


def boolish(value: Any) -> bool:
    return clean_tag(value).lower() in {"1", "true", "yes", "y", "t"}


def grade_key(row: pd.Series, layer_field: str | None, bridge_field: str | None,
              tunnel_field: str | None) -> str:
    layer = clean_tag(row.get(layer_field, "0")) if layer_field else "0"
    layer = layer or "0"
    bridge = "bridge" if bridge_field and boolish(row.get(bridge_field)) else "surface"
    tunnel = "tunnel" if tunnel_field and boolish(row.get(tunnel_field)) else "surface"
    return f"layer={layer}|{bridge}|{tunnel}"


def normalize_line_orientation(line: LineString, precision: int) -> LineString:
    coords = list(line.coords)
    if len(coords) < 2:
        return line
    start = tuple(round(float(v), precision) for v in coords[0][:2])
    end = tuple(round(float(v), precision) for v in coords[-1][:2])
    if end < start:
        coords.reverse()
    return LineString(coords)


def profile_inputs(profile: dict[str, Any]) -> tuple[Any, Any]:
    roads = nested(
        profile,
        "inputs.osm_roads",
        "inputs.osm_road_vector",
        "road_sources.osm.input",
        "road_sources.osm.vector",
        "osm.roads",
        "osm.roads_path",
    )
    boundary = nested(
        profile,
        "inputs.administrative_boundary",
        "inputs.boundary",
        "region.boundary",
        "osm.boundary_path",
        "demand.boundary_path",
    )
    return roads, boundary


def choose_processing_crs(
    override: str | None, profile: dict[str, Any], boundary: gpd.GeoDataFrame
) -> CRS:
    candidate = override or nested(
        profile,
        "crs.processing",
        "crs.processing_projected",
        "processing_crs",
        "region.processing_crs",
    )
    if candidate:
        crs = CRS.from_user_input(candidate)
    else:
        crs = CRS.from_user_input(boundary.estimate_utm_crs())
    if not crs.is_projected:
        raise ValueError(f"Processing CRS must be projected in metres, got {crs}.")
    axis_units = {axis.unit_name.lower() for axis in crs.axis_info if axis.unit_name}
    if axis_units and not any("metre" in unit or "meter" in unit for unit in axis_units):
        raise ValueError(f"Processing CRS axes are not metre based: {crs}.")
    return crs


def line_records(
    roads_m: gpd.GeoDataFrame,
    boundary_geometry: Any,
    fields: dict[str, str | None],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source_order, row in roads_m.iterrows():
        road_class = clean_tag(row.get(fields["class"]))
        if tag_tokens(road_class) & EXCLUDED_HIGHWAYS:
            continue
        access = clean_tag(row.get(fields["access"])).lower() if fields["access"] else ""
        if tag_tokens(access) & EXCLUDED_ACCESS:
            continue
        geometry = make_valid(row.geometry) if row.geometry is not None else None
        if geometry is None or geometry.is_empty:
            continue
        geometry = geometry.intersection(boundary_geometry)
        source_id = clean_tag(row.get(fields["id"])) if fields["id"] else ""
        if not source_id:
            source_id = f"feature_{source_order}"
        base = {
            "source_id": source_id,
            "source_order": int(source_order) if isinstance(source_order, int) else str(source_order),
            "road_class": road_class,
            "name": clean_tag(row.get(fields["name"])) if fields["name"] else "",
            "ref": clean_tag(row.get(fields["ref"])) if fields["ref"] else "",
            "oneway": clean_tag(row.get(fields["oneway"])) if fields["oneway"] else "",
            "access": access,
            "grade_key": grade_key(
                row, fields["layer"], fields["bridge"], fields["tunnel"]
            ),
        }
        if not base["oneway"] and (road_class == "motorway" or
                                     clean_tag(row.get("junction")) == "roundabout"):
            base["oneway"] = "yes"
        for part in iter_lines(geometry):
            if not part.is_empty and part.length > 0:
                records.append({**base, "geometry": part})
    return records


def noded_segments(records: list[dict[str, Any]], minimum_length_m: float) -> list[dict[str, Any]]:
    by_grade: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_grade[record["grade_key"]].append(record)

    output: list[dict[str, Any]] = []
    for grade in sorted(by_grade):
        source_records = by_grade[grade]
        tree = STRtree([record["geometry"] for record in source_records])
        unioned = unary_union([record["geometry"] for record in source_records])
        lines = []
        for line in iter_lines(unioned):
            if line.is_ring:
                lines.extend([substring(line, 0, line.length/2),
                              substring(line, line.length/2, line.length)])
            else:
                lines.append(line)
        for line in lines:
            if line.length + 1e-9 < minimum_length_m:
                continue
            # Exact noding keeps a segment on at least one source feature.  Use a
            # very small numerical buffer solely for lineage matching.
            tolerance = max(1e-6, min(0.01, float(line.length) * 1e-7))
            contributors: list[dict[str, Any]] = []
            for source_index in sorted(tree.query(line.buffer(tolerance))):
                source = source_records[int(source_index)]
                if not source["geometry"].buffer(tolerance).intersects(line):
                    continue
                overlap = line.intersection(source["geometry"].buffer(tolerance)).length
                if overlap >= max(0.0, line.length - 2.0 * tolerance):
                    contributors.append(source)
            if not contributors:
                # Numerical fall-back: select the nearest archived source but
                # retain that the evidence assignment used a fallback.
                raise ValueError("Noded segment has no exact source lineage; refusing nearest-source guess")
            else:
                lineage_rule = "exact_same_grade_noding_overlap"
            output.append(
                {
                    "grade_key": grade,
                    "geometry": line,
                    "contributors": contributors,
                    "lineage_rule": lineage_rule,
                }
            )
    return output


def contributor_value(contributors: list[dict[str, Any]], field: str) -> str:
    values = sorted({clean_tag(item.get(field)) for item in contributors if clean_tag(item.get(field))})
    return "|".join(values)


def build_graph_tables(
    segments: list[dict[str, Any]],
    processing_crs: CRS,
    secondary_classes: set[str],
    coordinate_precision: int,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    normalized: list[dict[str, Any]] = []
    for item in segments:
        line = normalize_line_orientation(item["geometry"], coordinate_precision)
        start = tuple(round(float(v), coordinate_precision) for v in line.coords[0][:2])
        end = tuple(round(float(v), coordinate_precision) for v in line.coords[-1][:2])
        if start == end:
            continue
        normalized.append({**item, "geometry": line, "start_key": start, "end_key": end})

    normalized.sort(
        key=lambda item: (
            item["grade_key"],
            item["start_key"],
            item["end_key"],
            round(item["geometry"].length, 6),
            item["geometry"].wkb_hex,
        )
    )

    # Grade groups are unioned separately, so an interior bridge/tunnel crossing
    # is not split. Endpoints are keyed by XY only: this reconnects a grade-
    # separated segment to its approach where archived endpoints coincide,
    # without connecting an interior crossing.
    node_keys = sorted(
        {item[endpoint] for item in normalized for endpoint in ("start_key", "end_key")}
    )
    node_ids = {key: f"OSM_N{number:06d}" for number, key in enumerate(node_keys, 1)}

    graph = nx.Graph()
    graph.add_nodes_from(node_ids.values())
    edge_rows: list[dict[str, Any]] = []
    seen_geometries: set[tuple[str, tuple[float, float], tuple[float, float], str]] = set()
    for item in normalized:
        geometry_key = (
            item["grade_key"], item["start_key"], item["end_key"], item["geometry"].wkb_hex
        )
        if geometry_key in seen_geometries:
            continue
        seen_geometries.add(geometry_key)
        from_node = node_ids[item["start_key"]]
        to_node = node_ids[item["end_key"]]
        graph.add_edge(from_node, to_node)
        contributors = sorted(item["contributors"], key=lambda row: str(row["source_id"]))
        source_ids = sorted({str(row["source_id"]) for row in contributors})
        directions = set()
        for source in contributors:
            tag = str(source.get("oneway", "")).lower()
            if tag in {"", "no", "0", "false", "b"}:
                directions.update(["forward", "reverse"])
            elif tag in {"yes", "1", "true", "f", "-1", "t"}:
                src = source["geometry"]
                # Interior samples avoid a closed ring's endpoint wrapping back
                # to chainage zero. The canonical geometry may be reversed.
                a = item["geometry"].interpolate(.25, normalized=True)
                b = item["geometry"].interpolate(.75, normalized=True)
                same = src.project(b) > src.project(a)
                if tag in {"-1", "t"}:
                    same = not same
                directions.add("forward" if same else "reverse")
            else:
                raise ValueError(f"Unsupported OSM oneway value: {tag}")
        observed_class = contributor_value(contributors, "road_class")
        class_tokens: set[str] = set()
        for contributor in contributors:
            class_tokens.update(tag_tokens(contributor["road_class"]))
        final_class = "secondary" if class_tokens & secondary_classes else "residential"
        edge_rows.append(
            {
                "edge_id": "",  # assigned after deterministic component ordering
                "road_edge_id": "",
                "from_node": from_node,
                "to_node": to_node,
                "length_m": float(item["geometry"].length),
                "component_id": "",
                "source_provider": "OSM",
                "geometry_provider": "OSM",
                "attribute_provider": "OSM_direct",
                "source_feature_ids": "|".join(source_ids),
                "original_source_id": "|".join(source_ids),
                "lineage_json": json.dumps(
                    {
                        "provider": "OSM",
                        "source_feature_ids": source_ids,
                        "rule": item["lineage_rule"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "observed_road_class": observed_class,
                "source_road_class": observed_class,
                "observed_name": contributor_value(contributors, "name"),
                "road_name": contributor_value(contributors, "name"),
                "observed_ref": contributor_value(contributors, "ref"),
                "road_ref": contributor_value(contributors, "ref"),
                "observed_oneway": contributor_value(contributors, "oneway"),
                "forward_allowed": "forward" in directions,
                "reverse_allowed": "reverse" in directions,
                "direction_evidence": "OSM_tag_preserved_not_legality_verified",
                "final_road_class": final_class,
                "class_evidence": (
                    "OSM_direct_secondary_seed"
                    if final_class == "secondary"
                    else "profile_mapping_to_residential"
                ),
                "classification_evidence": (
                    "OSM_direct_secondary_seed"
                    if final_class == "secondary"
                    else "profile_mapping_to_residential"
                ),
                "track_access_uncertain": bool("track" in class_tokens),
                "processing_rule": "same_grade_make_valid_clip_and_node",
                "grade_key": item["grade_key"],
                "geometry_wkt_m": item["geometry"].wkt,
                "geometry_wkt": item["geometry"].wkt,
                "geometry": item["geometry"],
            }
        )

    if not edge_rows:
        raise ValueError("No eligible OSM motor-road edge remains after filtering and clipping.")

    components = sorted(
        (sorted(component) for component in nx.connected_components(graph)),
        key=lambda component: (-len(component), component[0]),
    )
    node_component: dict[str, str] = {}
    for number, component in enumerate(components, 1):
        component_id = f"OSM_C{number:04d}"
        node_component.update({node_id: component_id for node_id in component})

    edge_rows.sort(
        key=lambda row: (
            node_component[row["from_node"]],
            row["from_node"],
            row["to_node"],
            round(row["length_m"], 6),
            row["geometry"].wkb_hex,
        )
    )
    for number, row in enumerate(edge_rows, 1):
        row["edge_id"] = f"OSM_E{number:06d}"
        row["road_edge_id"] = row["edge_id"]
        row["component_id"] = node_component[row["from_node"]]

    degree = dict(graph.degree())
    incident_grades: dict[str, set[str]] = defaultdict(set)
    for row in edge_rows:
        incident_grades[row["from_node"]].add(row["grade_key"])
        incident_grades[row["to_node"]].add(row["grade_key"])
    node_rows = []
    for (x_coord, y_coord), node_id in sorted(node_ids.items(), key=lambda item: item[1]):
        node_rows.append(
            {
                "node_id": node_id,
                "road_node_id": node_id,
                "x_m": float(x_coord),
                "y_m": float(y_coord),
                "component_id": node_component[node_id],
                "degree": int(degree[node_id]),
                "source_provider": "OSM",
                "geometry_provider": "OSM",
                "source_status": "OSM_direct_topology_node",
                "lineage_json": json.dumps(
                    {
                        "provider": "OSM",
                        "stable_coordinate_m": [float(x_coord), float(y_coord)],
                        "incident_grade_keys": sorted(incident_grades[node_id]),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "grade_key": "|".join(sorted(incident_grades[node_id])),
                "geometry": gpd.points_from_xy([x_coord], [y_coord])[0],
            }
        )

    return (
        gpd.GeoDataFrame(node_rows, geometry="geometry", crs=processing_crs),
        gpd.GeoDataFrame(edge_rows, geometry="geometry", crs=processing_crs),
    )


def write_outputs(
    output_dir: Path,
    nodes_m: gpd.GeoDataFrame,
    edges_m: gpd.GeoDataFrame,
    roads_path: Path,
    boundary_path: Path,
    profile_path: Path,
    processing_crs: CRS,
    overwrite: bool,
    input_feature_count: int,
    eligible_part_count: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = [output_dir / name for name in OUTPUT_FILES if (output_dir / name).exists()]
    if existing and not overwrite:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(f"Refusing to overwrite existing canonical outputs: {names}")
    for path in existing:
        path.unlink()

    nodes_wgs = nodes_m.to_crs("EPSG:4326")
    nodes_wgs["longitude"] = nodes_wgs.geometry.x
    nodes_wgs["latitude"] = nodes_wgs.geometry.y
    node_csv = nodes_wgs.drop(columns="geometry").copy()
    node_csv.to_csv(output_dir / "canonical_road_nodes.csv", index=False, lineterminator="\n")
    nodes_wgs.to_file(output_dir / "canonical_road_nodes.geojson", driver="GeoJSON")

    edges_wgs = edges_m.to_crs("EPSG:4326")
    edge_csv = edges_m.drop(columns="geometry").copy()
    edge_csv.to_csv(output_dir / "canonical_road_edges.csv", index=False, lineterminator="\n")
    edges_wgs.drop(columns=["geometry_wkt_m"]).to_file(
        output_dir / "canonical_road_edges.geojson", driver="GeoJSON"
    )

    manifest = {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": str(Path(__file__).resolve()),
        "provider": "OSM",
        "profile": str(profile_path),
        "inputs": {
            "roads": str(roads_path),
            "roads_sha256": sha256_file(roads_path),
            "boundary": str(boundary_path),
            "boundary_sha256": sha256_file(boundary_path),
        },
        "processing_crs": processing_crs.to_string(),
        "source_feature_count": input_feature_count,
        "eligible_clipped_line_part_count": eligible_part_count,
        "canonical_node_count": len(nodes_m),
        "canonical_edge_count": len(edges_m),
        "component_count": int(nodes_m["component_id"].nunique()),
        "final_road_classes": sorted(edges_m["final_road_class"].unique().tolist()),
        "exclusion_policy": {
            "highway": sorted(EXCLUDED_HIGHWAYS),
            "access": sorted(EXCLUDED_ACCESS),
            "track": "retained_with_track_access_uncertain=true",
        },
        "evidence_policy": "OSM direct geometry and attributes; no AMap evidence fields",
    }
    (output_dir / "canonical_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = args.project_root.resolve()
    profile_path = resolve_path(project_root, args.profile, "profile")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile_roads, profile_boundary = profile_inputs(profile)
    roads_path = resolve_path(project_root, args.roads or profile_roads, "OSM roads")
    boundary_path = resolve_path(
        project_root, args.boundary or profile_boundary, "administrative boundary"
    )
    output_dir = args.output_dir if args.output_dir.is_absolute() else project_root / args.output_dir
    output_dir = output_dir.resolve()

    roads = gpd.read_file(roads_path)
    boundary = gpd.read_file(boundary_path)
    if roads.crs is None or boundary.crs is None:
        raise ValueError("Road and boundary inputs must both declare a CRS.")
    if roads.empty or boundary.empty:
        raise ValueError("Road and boundary inputs must both contain features.")
    processing_crs = choose_processing_crs(args.processing_crs, profile, boundary)
    roads_m = roads.to_crs(processing_crs)
    boundary_m = boundary.to_crs(processing_crs)
    boundary_union = unary_union(
        [make_valid(geometry) for geometry in boundary_m.geometry if geometry is not None]
    )
    if boundary_union.is_empty:
        raise ValueError("Administrative boundary has no valid polygonal geometry.")

    configured = nested(
        profile, "road_sources.osm.fields", "osm.fields", "osm.attribute_fields", default={}
    ) or {}
    def configured_field(name: str, candidates_name: str | None = None) -> str | None:
        value = configured.get(name)
        if isinstance(value, str):
            return value
        candidates = configured.get(candidates_name or f"{name}_candidates", [])
        if isinstance(candidates, str):
            candidates = [candidates]
        return first_existing(roads_m.columns, candidates)

    fields: dict[str, str | None] = {
        "class": configured_field("road_class", "road_class_candidates")
        or configured_field("highway")
        or first_existing(roads_m.columns, ["highway", "road_class", "fclass", "type"]),
        "access": configured_field("access", "access_candidates")
        or first_existing(roads_m.columns, ["access"]),
        "id": configured_field("source_id")
        or first_existing(roads_m.columns, ["osm_id", "osm_way_id", "osmid", "id", "fid"]),
        "name": configured_field("name", "name_candidates")
        or first_existing(roads_m.columns, ["name"]),
        "ref": configured_field("ref", "reference_candidates")
        or first_existing(roads_m.columns, ["ref"]),
        "oneway": configured_field("oneway") or first_existing(roads_m.columns, ["oneway"]),
        "layer": configured_field("layer") or first_existing(roads_m.columns, ["layer"]),
        "bridge": configured_field("bridge") or first_existing(roads_m.columns, ["bridge"]),
        "tunnel": configured_field("tunnel") or first_existing(roads_m.columns, ["tunnel"]),
    }
    if fields["class"] is None:
        raise ValueError(
            "Cannot identify the OSM road-class field. Configure road_sources.osm.fields.road_class."
        )

    records = line_records(roads_m, boundary_union, fields)
    if not records:
        raise ValueError("No eligible OSM road geometry remains after access/type filtering.")
    minimum_length_m = float(
        nested(profile, "road_sources.osm.minimum_edge_length_m", "osm.minimum_edge_length_m", default=0.05)
    )
    coordinate_precision = int(
        nested(profile, "road_sources.osm.coordinate_precision_decimals", default=3)
    )
    segments = noded_segments(records, minimum_length_m)
    configured_secondary = nested(
        profile,
        "road_class_mapping.osm.secondary_source_classes",
        "road_sources.osm.secondary_source_classes",
        default=sorted(DEFAULT_SECONDARY_CLASSES),
    )
    secondary_classes = {str(value).strip().lower() for value in configured_secondary}
    nodes_m, edges_m = build_graph_tables(
        segments, processing_crs, secondary_classes, coordinate_precision
    )
    write_outputs(
        output_dir,
        nodes_m,
        edges_m,
        roads_path,
        boundary_path,
        profile_path,
        processing_crs,
        args.overwrite,
        len(roads),
        len(records),
    )
    print(
        json.dumps(
            {
                "provider": "OSM",
                "nodes": len(nodes_m),
                "edges": len(edges_m),
                "components": int(nodes_m["component_id"].nunique()),
                "output_dir": str(output_dir),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # concise CLI failure without suppressing nonzero status
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
