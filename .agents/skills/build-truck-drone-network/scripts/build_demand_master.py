#!/usr/bin/env python3
"""Build deterministic population-demand nodes from first-principles inputs.

The formal input is a region profile, an administrative boundary, a population
raster, and one explicit Depot coordinate.  Pre-built demand points are not
accepted.  The output is deliberately source-neutral so that the same demand
table can be evaluated against OSM, AMap-derived, or licensed local roads.

Canonical profile keys (relative paths are resolved against ``--project-root``)::

    {
      "demand": {
        "boundary_path": "path/to/boundary.geojson",
        "boundary_layer": null,
        "population_raster_path": "path/to/population.tif",
        "target_resolution_m": 100,
        "native_resolution_tolerance_fraction": 0.20,
        "population_threshold": 0,
        "connectivity": 8,
        "official_population_total": 8693,
        "demand_id_prefix": "HZ_E",
        "depot": {
          "id": "HZ_DEPOT",
          "longitude": 101.986,
          "latitude": 25.8700883,
          "crs": "EPSG:4326"
        }
      }
    }

For interoperability, ``inputs.administrative_boundary`` / ``inputs.boundary``
and ``inputs.population_raster`` are accepted aliases.  An input entry may be
either a path string or ``{"path": ..., "layer": ...}``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import CRS, Geod, Transformer
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import Affine, rowcol, xy
from rasterio.warp import reproject
from rasterio.windows import Window, from_bounds
from scipy import ndimage
from shapely.geometry import Point, mapping
from shapely.ops import transform as shapely_transform


OUTPUT_NAMES = (
    "demand_master.csv",
    "demand_nodes.csv",
    "demand_components.csv",
    "demand_master.json",
    "demand_master.geojson",
    "demand_generation_audit.json",
    "demand_data_dictionary.csv",
)


class DemandBuildError(RuntimeError):
    """Raised when a demand master cannot be built without ambiguity."""


@dataclass(frozen=True)
class DemandSettings:
    boundary_path: Path
    boundary_layer: str | None
    boundary_assumed_crs: CRS | None
    boundary_selector_field: str | None
    boundary_selector_value: str | None
    boundary_rasterization_mode: str
    stable_order: str
    population_raster_path: Path
    target_resolution_m: float
    resolution_tolerance_fraction: float
    population_threshold: float
    official_population_total: float | None
    demand_id_prefix: str
    depot_id: str
    depot_x: float
    depot_y: float
    depot_crs: CRS
    target_crs: CRS | None
    conservation_relative_tolerance: float


@dataclass(frozen=True)
class PopulationGrid:
    values: np.ndarray
    transform: Affine
    crs: CRS
    mode: str
    native_resolution_x_m: float
    native_resolution_y_m: float
    source_total_in_boundary: float
    target_total_before_threshold: float
    conservation_factor: float
    invalid_cell_count: int
    negative_cell_count: int


def _first(mapping_obj: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping_obj and mapping_obj[key] is not None:
            return mapping_obj[key]
    return None


def _nested_profile_section(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    section = profile.get("demand")
    if isinstance(section, Mapping):
        return section
    section = profile.get("demand_generation")
    if isinstance(section, Mapping):
        return section
    return profile


def _resolve_path(project_root: Path, raw_path: str | os.PathLike[str]) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _parse_path_spec(
    project_root: Path,
    value: Any,
    *,
    default_layer: str | None = None,
) -> tuple[Path, str | None]:
    if isinstance(value, (str, os.PathLike)):
        return _resolve_path(project_root, value), default_layer
    if isinstance(value, Mapping) and value.get("path"):
        return (
            _resolve_path(project_root, str(value["path"])),
            value.get("layer", default_layer),
        )
    raise DemandBuildError("An input path must be a string or an object containing 'path'.")


def load_settings(profile: Mapping[str, Any], project_root: Path) -> DemandSettings:
    demand = _nested_profile_section(profile)
    inputs = profile.get("inputs", {})
    if not isinstance(inputs, Mapping):
        inputs = {}

    boundary_value = _first(
        demand,
        ("boundary_path", "administrative_boundary_path", "boundary"),
    )
    if boundary_value is None:
        boundary_value = _first(inputs, ("administrative_boundary", "boundary"))
    population_value = _first(
        demand,
        ("population_raster_path", "population_raster", "population_path"),
    )
    if population_value is None:
        population_value = inputs.get("population_raster")
    if boundary_value is None or population_value is None:
        raise DemandBuildError(
            "Profile must provide an administrative boundary and population raster."
        )

    boundary_path, boundary_layer = _parse_path_spec(
        project_root,
        boundary_value,
        default_layer=demand.get("boundary_layer"),
    )
    population_path, ignored_layer = _parse_path_spec(project_root, population_value)
    if ignored_layer:
        raise DemandBuildError("A raster input cannot specify a vector layer.")
    for path in (boundary_path, population_path):
        if not path.exists():
            raise DemandBuildError(f"Required input does not exist: {path}")

    depot = demand.get("depot", profile.get("depot"))
    if not isinstance(depot, Mapping):
        raise DemandBuildError("Profile must contain an explicit 'depot' coordinate object.")
    depot_id = str(_first(depot, ("id", "node_id", "depot_id")) or "DEPOT")
    depot_crs = CRS.from_user_input(depot.get("crs", "EPSG:4326"))
    longitude = _first(depot, ("longitude", "lon", "x"))
    latitude = _first(depot, ("latitude", "lat", "y"))
    coordinates = depot.get("coordinates")
    if (longitude is None or latitude is None) and isinstance(coordinates, Sequence):
        if len(coordinates) >= 2:
            longitude, latitude = coordinates[0], coordinates[1]
    if longitude is None or latitude is None:
        raise DemandBuildError("Depot must contain longitude/latitude (or x/y).")

    target_resolution = float(demand.get("target_resolution_m", 100.0))
    if not math.isfinite(target_resolution) or target_resolution <= 0:
        raise DemandBuildError("target_resolution_m must be a positive finite number.")
    tolerance = float(demand.get("native_resolution_tolerance_fraction", 0.20))
    if not 0 <= tolerance <= 1:
        raise DemandBuildError(
            "native_resolution_tolerance_fraction must lie between 0 and 1."
        )
    threshold = float(_first(demand, ("population_threshold", "threshold")) or 0.0)
    if not math.isfinite(threshold) or threshold < 0:
        raise DemandBuildError("population_threshold must be finite and non-negative.")
    connectivity = int(demand.get("connectivity", 8))
    if connectivity != 8:
        raise DemandBuildError("This contract requires 8-neighbour connectivity.")

    official_raw = _first(
        demand,
        (
            "official_population_total",
            "official_total",
            "official_total_population",
            "official_population",
        ),
    )
    official_total = None if official_raw is None else float(official_raw)
    if official_total is not None and (
        not math.isfinite(official_total) or official_total <= 0
    ):
        raise DemandBuildError("official_population_total must be positive when supplied.")

    target_crs_value = demand.get("target_projected_crs") or demand.get("target_crs")
    target_crs = CRS.from_user_input(target_crs_value) if target_crs_value else None
    if target_crs is not None and not target_crs.is_projected:
        raise DemandBuildError("Configured target_crs must be a projected CRS.")
    if (
        target_crs is not None
        and target_crs.axis_info
        and not math.isclose(
            float(target_crs.axis_info[0].unit_conversion_factor or 1.0),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    ):
        raise DemandBuildError("Configured target_crs must use metre units in v1.")

    id_prefix = str(_first(demand, ("demand_id_prefix", "node_id_prefix")) or "D")
    if not id_prefix or any(ch in id_prefix for ch in ("\n", "\r", ",")):
        raise DemandBuildError("demand_id_prefix must be a non-empty, CSV-safe string.")

    assumed_crs_raw = demand.get("boundary_assumed_crs")
    assumed_crs = CRS.from_user_input(assumed_crs_raw) if assumed_crs_raw else None
    selector = demand.get("boundary_selector")
    selector_field: str | None = None
    selector_value: str | None = None
    if selector is not None:
        if not isinstance(selector, Mapping):
            raise DemandBuildError("demand.boundary_selector must be an object.")
        selector_field = str(selector.get("field") or "").strip() or None
        raw_selector_value = _first(selector, ("equals", "value"))
        selector_value = None if raw_selector_value is None else str(raw_selector_value)
        if selector_field is None or selector_value is None:
            raise DemandBuildError(
                "demand.boundary_selector requires 'field' and 'equals' (or 'value')."
            )

    rasterization_mode = str(
        demand.get("boundary_cell_inclusion", "cell_center_all_touched_false")
    )
    allowed_rasterization_modes = {
        "cell_center_all_touched_false",
        "legacy_pillow_source_order_zone_labels_v1",
    }
    if rasterization_mode not in allowed_rasterization_modes:
        raise DemandBuildError(
            "Unsupported boundary_cell_inclusion: " + rasterization_mode
        )
    if (
        rasterization_mode == "legacy_pillow_source_order_zone_labels_v1"
        and (selector_field is None or selector_value is None)
    ):
        raise DemandBuildError(
            "The legacy source-order zone-label mode requires a unique boundary_selector."
        )

    stable_order = str(
        demand.get(
            "stable_order",
            "minimum_component_raster_row_then_minimum_column",
        )
    )
    allowed_stable_orders = {
        "minimum_component_raster_row_then_minimum_column",
        "population_weighted_centroid_north_to_south_then_west_to_east",
    }
    if stable_order not in allowed_stable_orders:
        raise DemandBuildError("Unsupported demand.stable_order: " + stable_order)

    return DemandSettings(
        boundary_path=boundary_path,
        boundary_layer=boundary_layer,
        boundary_assumed_crs=assumed_crs,
        boundary_selector_field=selector_field,
        boundary_selector_value=selector_value,
        boundary_rasterization_mode=rasterization_mode,
        stable_order=stable_order,
        population_raster_path=population_path,
        target_resolution_m=target_resolution,
        resolution_tolerance_fraction=tolerance,
        population_threshold=threshold,
        official_population_total=official_total,
        demand_id_prefix=id_prefix,
        depot_id=depot_id,
        depot_x=float(longitude),
        depot_y=float(latitude),
        depot_crs=depot_crs,
        target_crs=target_crs,
        conservation_relative_tolerance=float(
            demand.get("population_conservation_relative_tolerance", 1e-6)
        ),
    )


def _read_boundary_collection(settings: DemandSettings) -> gpd.GeoDataFrame:
    kwargs: dict[str, Any] = {}
    if settings.boundary_layer:
        kwargs["layer"] = settings.boundary_layer
    boundary = gpd.read_file(settings.boundary_path, **kwargs)
    if boundary.empty:
        raise DemandBuildError("Administrative boundary has no features.")
    if boundary.crs is None:
        if settings.boundary_assumed_crs is None:
            raise DemandBuildError(
                "Administrative boundary has no declared CRS; configure "
                "demand.boundary_assumed_crs only when the source CRS is known."
            )
        boundary = boundary.set_crs(settings.boundary_assumed_crs, allow_override=True)
    return boundary


def _read_boundary(settings: DemandSettings) -> gpd.GeoDataFrame:
    boundary = _read_boundary_collection(settings)
    if settings.boundary_selector_field is not None:
        field = settings.boundary_selector_field
        if field not in boundary.columns:
            raise DemandBuildError(f"Boundary selector field is missing: {field}")
        selected = boundary.loc[
            boundary[field].astype(str) == settings.boundary_selector_value
        ].copy()
        if len(selected) != 1:
            raise DemandBuildError(
                "Boundary selector must match exactly one feature; "
                f"field={field!r}, value={settings.boundary_selector_value!r}, "
                f"matches={len(selected)}."
            )
        boundary = selected
    boundary = boundary.loc[~boundary.geometry.isna()].copy()
    boundary["geometry"] = boundary.geometry.make_valid()
    geometry = boundary.geometry.union_all()
    if geometry.is_empty:
        raise DemandBuildError("Administrative boundary is empty after geometry repair.")
    return gpd.GeoDataFrame({"boundary_id": ["study_area"]}, geometry=[geometry], crs=boundary.crs)


def _legacy_pillow_zone_mask(
    settings: DemandSettings,
    dataset: rasterio.io.DatasetReader,
    window: Window,
) -> np.ndarray:
    """Reproduce the traceable 2026-07-26 Huanzhou zone-label rasterization.

    The historical workflow drew every township polygon from the source
    collection, in source feature order, onto an integer Pillow label image.
    Later features overwrote earlier labels in tiny shared-edge overlaps.  This
    mode is profile-gated and exists only for golden-regression compatibility;
    the portable default remains Rasterio cell-centre inclusion.
    """

    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - environment capability guard
        raise DemandBuildError(
            "legacy_pillow_source_order_zone_labels_v1 requires Pillow."
        ) from exc

    collection = _read_boundary_collection(settings)
    field = settings.boundary_selector_field
    value = settings.boundary_selector_value
    assert field is not None and value is not None
    if field not in collection.columns:
        raise DemandBuildError(f"Boundary selector field is missing: {field}")
    selected_positions = np.flatnonzero(collection[field].astype(str).to_numpy() == value)
    if len(selected_positions) != 1:
        raise DemandBuildError(
            "Legacy zone-label selector must match exactly one source feature; "
            f"matches={len(selected_positions)}."
        )

    raster_crs = CRS.from_user_input(dataset.crs)
    collection_crs = CRS.from_user_input(collection.crs)
    if collection_crs != raster_crs:
        collection = collection.to_crs(raster_crs)
    window_transform = dataset.window_transform(window)
    inverse_transform = ~window_transform
    width = int(window.width)
    height = int(window.height)
    label_image = Image.new("I", (width, height), 0)
    drawer = ImageDraw.Draw(label_image)

    for source_position, geometry in enumerate(collection.geometry, start=1):
        if geometry is None or geometry.is_empty:
            continue
        polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
        for polygon in polygons:
            # The historical shapefile reader treated every ring as a drawable
            # polygon part.  Preserve that exact behavior for this named mode.
            for ring in (polygon.exterior, *polygon.interiors):
                pixel_vertices = [
                    inverse_transform * (float(x), float(y)) for x, y in ring.coords
                ]
                drawer.polygon(pixel_vertices, fill=source_position)

    selected_label = int(selected_positions[0]) + 1
    return np.asarray(label_image, dtype=np.int64) == selected_label


def _metric_pixel_size(
    transform: Affine,
    raster_crs: CRS,
    centre_x: float,
    centre_y: float,
) -> tuple[float, float]:
    if abs(transform.b) > 1e-12 or abs(transform.d) > 1e-12:
        raise DemandBuildError("Rotated population rasters are not supported in v1.")
    if raster_crs.is_projected:
        unit_factor = 1.0
        if raster_crs.axis_info:
            unit_factor = float(raster_crs.axis_info[0].unit_conversion_factor or 1.0)
        return abs(transform.a) * unit_factor, abs(transform.e) * unit_factor

    to_wgs84 = Transformer.from_crs(raster_crs, "EPSG:4326", always_xy=True)
    lon0, lat0 = to_wgs84.transform(centre_x, centre_y)
    lon1, lat1 = to_wgs84.transform(centre_x + transform.a, centre_y)
    lon2, lat2 = to_wgs84.transform(centre_x, centre_y + transform.e)
    geod = Geod(ellps="WGS84")
    _, _, width = geod.inv(lon0, lat0, lon1, lat1)
    _, _, height = geod.inv(lon0, lat0, lon2, lat2)
    return abs(float(width)), abs(float(height))


def _bounded_window(dataset: rasterio.io.DatasetReader, bounds: Sequence[float]) -> Window:
    raw = from_bounds(*bounds, transform=dataset.transform)
    col_off = max(0, math.floor(raw.col_off) - 1)
    row_off = max(0, math.floor(raw.row_off) - 1)
    col_end = min(dataset.width, math.ceil(raw.col_off + raw.width) + 1)
    row_end = min(dataset.height, math.ceil(raw.row_off + raw.height) + 1)
    if col_end <= col_off or row_end <= row_off:
        raise DemandBuildError("Population raster does not overlap the administrative boundary.")
    return Window(col_off, row_off, col_end - col_off, row_end - row_off)


def _clean_population(
    masked_values: np.ma.MaskedArray | np.ndarray,
) -> tuple[np.ndarray, int, int]:
    if np.ma.isMaskedArray(masked_values):
        mask_count = int(np.ma.getmaskarray(masked_values).sum())
        values = np.asarray(masked_values.filled(0.0), dtype=np.float64)
    else:
        mask_count = 0
        values = np.asarray(masked_values, dtype=np.float64)
    invalid = ~np.isfinite(values)
    invalid_count = mask_count + int(invalid.sum())
    values[invalid] = 0.0
    negative = values < 0
    negative_count = int(negative.sum())
    values[negative] = 0.0
    return values, invalid_count, negative_count


def _project_geometry(geometry: Any, src_crs: CRS, dst_crs: CRS) -> Any:
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    return shapely_transform(transformer.transform, geometry)


def _read_native_grid(
    dataset: rasterio.io.DatasetReader,
    boundary_geometry: Any,
    boundary_crs: CRS,
    native_x_m: float,
    native_y_m: float,
    settings: DemandSettings,
) -> PopulationGrid:
    raster_crs = CRS.from_user_input(dataset.crs)
    boundary_raster = _project_geometry(boundary_geometry, boundary_crs, raster_crs)
    window = _bounded_window(dataset, boundary_raster.bounds)
    values, invalid_count, negative_count = _clean_population(
        dataset.read(1, window=window, masked=True)
    )
    transform = dataset.window_transform(window)
    if settings.boundary_rasterization_mode == "cell_center_all_touched_false":
        inside = geometry_mask(
            [mapping(boundary_raster)],
            out_shape=values.shape,
            transform=transform,
            invert=True,
            all_touched=False,
        )
    else:
        inside = _legacy_pillow_zone_mask(settings, dataset, window)
    values[~inside] = 0.0
    total = float(values.sum(dtype=np.float64))
    if total <= 0:
        raise DemandBuildError("No positive population remains inside the boundary.")
    return PopulationGrid(
        values=values,
        transform=transform,
        crs=raster_crs,
        mode="native_grid_preserved",
        native_resolution_x_m=native_x_m,
        native_resolution_y_m=native_y_m,
        source_total_in_boundary=total,
        target_total_before_threshold=total,
        conservation_factor=1.0,
        invalid_cell_count=invalid_count,
        negative_cell_count=negative_count,
    )


def _target_grid_definition(
    boundary_geometry: Any,
    boundary_crs: CRS,
    target_crs: CRS,
    resolution: float,
) -> tuple[Affine, int, int, Any]:
    boundary_target = _project_geometry(boundary_geometry, boundary_crs, target_crs)
    minx, miny, maxx, maxy = boundary_target.bounds
    left = math.floor(minx / resolution) * resolution
    bottom = math.floor(miny / resolution) * resolution
    right = math.ceil(maxx / resolution) * resolution
    top = math.ceil(maxy / resolution) * resolution
    width = int(round((right - left) / resolution))
    height = int(round((top - bottom) / resolution))
    if width <= 0 or height <= 0:
        raise DemandBuildError("Administrative boundary produced an empty target grid.")
    return Affine(resolution, 0.0, left, 0.0, -resolution, top), width, height, boundary_target


def _read_resampled_grid(
    dataset: rasterio.io.DatasetReader,
    boundary_geometry: Any,
    boundary_crs: CRS,
    target_crs: CRS,
    settings: DemandSettings,
    native_x_m: float,
    native_y_m: float,
) -> PopulationGrid:
    if settings.boundary_rasterization_mode != "cell_center_all_touched_false":
        raise DemandBuildError(
            "The legacy Pillow zone-label compatibility mode is defined only "
            "for a native approximately-100 m raster; it cannot be combined "
            "with sum resampling."
        )
    raster_crs = CRS.from_user_input(dataset.crs)
    boundary_raster = _project_geometry(boundary_geometry, boundary_crs, raster_crs)
    window = _bounded_window(dataset, boundary_raster.bounds)
    source, invalid_count, negative_count = _clean_population(
        dataset.read(1, window=window, masked=True)
    )
    source_transform = dataset.window_transform(window)
    source_inside = geometry_mask(
        [mapping(boundary_raster)],
        out_shape=source.shape,
        transform=source_transform,
        invert=True,
        all_touched=False,
    )
    source[~source_inside] = 0.0
    source_total = float(source.sum(dtype=np.float64))
    if source_total <= 0:
        raise DemandBuildError("No positive population remains inside the boundary.")

    transform, width, height, boundary_target = _target_grid_definition(
        boundary_geometry,
        boundary_crs,
        target_crs,
        settings.target_resolution_m,
    )
    target = np.zeros((height, width), dtype=np.float64)
    reproject(
        source=source,
        destination=target,
        src_transform=source_transform,
        src_crs=raster_crs,
        src_nodata=0.0,
        dst_transform=transform,
        dst_crs=target_crs,
        dst_nodata=0.0,
        resampling=Resampling.sum,
        init_dest_nodata=True,
    )
    target_inside = geometry_mask(
        [mapping(boundary_target)],
        out_shape=target.shape,
        transform=transform,
        invert=True,
        all_touched=False,
    )
    target[~target_inside] = 0.0
    target[~np.isfinite(target)] = 0.0
    target[target < 0] = 0.0
    target_total = float(target.sum(dtype=np.float64))
    if target_total <= 0:
        raise DemandBuildError("100 m sum resampling produced no positive population.")

    # Masking a projected grid at the county edge can remove fractional mass
    # deposited just outside the polygon.  A documented uniform conservation
    # correction restores the in-boundary source total without altering spatial
    # proportions.  This is distinct from optional official-statistics calibration.
    conservation_factor = source_total / target_total
    target *= conservation_factor
    conserved_total = float(target.sum(dtype=np.float64))
    relative_error = abs(conserved_total - source_total) / source_total
    if relative_error > settings.conservation_relative_tolerance:
        raise DemandBuildError(
            "Population was not conserved during 100 m sum resampling: "
            f"relative error={relative_error:.3g}."
        )
    return PopulationGrid(
        values=target,
        transform=transform,
        crs=target_crs,
        mode="sum_resampled_to_projected_100m_grid",
        native_resolution_x_m=native_x_m,
        native_resolution_y_m=native_y_m,
        source_total_in_boundary=source_total,
        target_total_before_threshold=conserved_total,
        conservation_factor=conservation_factor,
        invalid_cell_count=invalid_count,
        negative_cell_count=negative_count,
    )


def build_population_grid(
    settings: DemandSettings,
    boundary: gpd.GeoDataFrame,
) -> PopulationGrid:
    boundary_crs = CRS.from_user_input(boundary.crs)
    geometry = boundary.geometry.iloc[0]
    with rasterio.open(settings.population_raster_path) as dataset:
        if dataset.crs is None:
            raise DemandBuildError("Population raster has no declared CRS.")
        raster_crs = CRS.from_user_input(dataset.crs)
        boundary_raster = _project_geometry(geometry, boundary_crs, raster_crs)
        centre = boundary_raster.centroid
        native_x_m, native_y_m = _metric_pixel_size(
            dataset.transform,
            raster_crs,
            centre.x,
            centre.y,
        )
        lower = settings.target_resolution_m * (1 - settings.resolution_tolerance_fraction)
        upper = settings.target_resolution_m * (1 + settings.resolution_tolerance_fraction)
        native_is_target = lower <= native_x_m <= upper and lower <= native_y_m <= upper
        if native_is_target:
            return _read_native_grid(
                dataset,
                geometry,
                boundary_crs,
                native_x_m,
                native_y_m,
                settings,
            )
        target_crs = settings.target_crs
        if target_crs is None:
            estimated = boundary.estimate_utm_crs()
            if estimated is None:
                raise DemandBuildError(
                    "Could not infer a projected target CRS; set demand.target_crs."
                )
            target_crs = CRS.from_user_input(estimated)
        return _read_resampled_grid(
            dataset,
            geometry,
            boundary_crs,
            target_crs,
            settings,
            native_x_m,
            native_y_m,
        )


def _component_records(
    grid: PopulationGrid,
    threshold: float,
) -> tuple[np.ndarray, list[dict[str, Any]], float, int]:
    eligible = grid.values > threshold
    labels, count = ndimage.label(eligible, structure=np.ones((3, 3), dtype=np.uint8))
    if count == 0:
        raise DemandBuildError("No population cells exceed population_threshold.")
    records: list[dict[str, Any]] = []
    width = grid.values.shape[1]
    selected_total = float(grid.values[eligible].sum(dtype=np.float64))
    for component_label in range(1, count + 1):
        rows, cols = np.nonzero(labels == component_label)
        weights = grid.values[rows, cols]
        raw_population = float(weights.sum(dtype=np.float64))
        xs, ys = xy(grid.transform, rows, cols, offset="center")
        x_array = np.asarray(xs, dtype=np.float64)
        y_array = np.asarray(ys, dtype=np.float64)
        centroid_x = float(np.average(x_array, weights=weights))
        centroid_y = float(np.average(y_array, weights=weights))
        flat_indices = rows.astype(np.int64) * width + cols.astype(np.int64)
        records.append(
            {
                "component_label": int(component_label),
                "min_cell_index": int(flat_indices.min()),
                "cell_count": int(rows.size),
                "raw_population": raw_population,
                "centroid_x": centroid_x,
                "centroid_y": centroid_y,
            }
        )
    records.sort(key=lambda item: item["min_cell_index"])
    for component_order, record in enumerate(records, start=1):
        record["component_order"] = component_order
    return labels, records, selected_total, int(eligible.sum())


def _depot_component_label(
    settings: DemandSettings,
    boundary: gpd.GeoDataFrame,
    grid: PopulationGrid,
    labels: np.ndarray,
) -> tuple[int, float, float, float, float]:
    boundary_crs = CRS.from_user_input(boundary.crs)
    to_boundary = Transformer.from_crs(settings.depot_crs, boundary_crs, always_xy=True)
    depot_boundary_x, depot_boundary_y = to_boundary.transform(settings.depot_x, settings.depot_y)
    if not boundary.geometry.iloc[0].covers(Point(depot_boundary_x, depot_boundary_y)):
        raise DemandBuildError("Explicit Depot coordinate is outside the administrative boundary.")

    to_grid = Transformer.from_crs(settings.depot_crs, grid.crs, always_xy=True)
    depot_grid_x, depot_grid_y = to_grid.transform(settings.depot_x, settings.depot_y)
    row, col = rowcol(grid.transform, depot_grid_x, depot_grid_y)
    if row < 0 or col < 0 or row >= labels.shape[0] or col >= labels.shape[1]:
        raise DemandBuildError("Explicit Depot coordinate falls outside the population grid.")
    component_label = int(labels[row, col])
    if component_label <= 0:
        raise DemandBuildError(
            "Explicit Depot coordinate does not fall in exactly one positive-population "
            "component above the configured threshold."
        )
    to_wgs84 = Transformer.from_crs(settings.depot_crs, "EPSG:4326", always_xy=True)
    depot_lon, depot_lat = to_wgs84.transform(settings.depot_x, settings.depot_y)
    return component_label, depot_grid_x, depot_grid_y, depot_lon, depot_lat


def _to_wgs84_coordinates(
    records: list[dict[str, Any]],
    grid_crs: CRS,
) -> None:
    transformer = Transformer.from_crs(grid_crs, "EPSG:4326", always_xy=True)
    for record in records:
        lon, lat = transformer.transform(record["centroid_x"], record["centroid_y"])
        record["centroid_longitude"] = float(lon)
        record["centroid_latitude"] = float(lat)


def _apply_component_order(
    records: list[dict[str, Any]],
    stable_order: str,
) -> None:
    if stable_order == "minimum_component_raster_row_then_minimum_column":
        records.sort(key=lambda item: item["min_cell_index"])
    elif stable_order == "population_weighted_centroid_north_to_south_then_west_to_east":
        records.sort(
            key=lambda item: (
                -item["centroid_latitude"],
                item["centroid_longitude"],
                item["min_cell_index"],
            )
        )
    else:  # pragma: no cover - load_settings enforces the contract
        raise DemandBuildError("Unsupported stable component order: " + stable_order)
    for component_order, record in enumerate(records, start=1):
        record["component_order"] = component_order


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _input_hash(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".shp":
        return {"path": str(path), "sha256": _sha256_file(path)}
    candidates = sorted(
        candidate for candidate in path.parent.glob(f"{path.stem}.*") if candidate.is_file()
    )
    ignored_locks = [candidate.name for candidate in candidates if candidate.name.lower().endswith(".lock")]
    companions = [
        candidate for candidate in candidates if not candidate.name.lower().endswith(".lock")
    ]
    digest = hashlib.sha256()
    members: list[dict[str, str]] = []
    for member in companions:
        member_hash = _sha256_file(member)
        digest.update(member.name.encode("utf-8"))
        digest.update(bytes.fromhex(member_hash))
        members.append({"name": member.name, "sha256": member_hash})
    return {
        "path": str(path),
        "sha256": digest.hexdigest(),
        "members": members,
        "ignored_lock_files": ignored_locks,
        "lock_file_policy": "recorded_but_excluded_from_content_hash_and_not_deleted",
    }


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fields: Sequence[str]) -> None:
    rows_list = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8-sig",
        newline="",
        dir=path.parent,
        delete=False,
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows_list)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def _write_outputs(
    output_dir: Path,
    master_rows: list[dict[str, Any]],
    component_rows: list[dict[str, Any]],
    audit: dict[str, Any],
) -> None:
    master_fields = (
        "node_id",
        "node_role",
        "demand_status",
        "affected_status",
        "component_label",
        "component_order",
        "min_cell_index",
        "cell_count",
        "raw_population",
        "population",
        "population_value_status",
        "longitude",
        "latitude",
        "grid_x",
        "grid_y",
        "grid_crs",
        "population_threshold",
        "calibration_factor",
    )
    component_fields = (
        "component_label",
        "component_order",
        "min_cell_index",
        "cell_count",
        "raw_population",
        "population",
        "is_depot_component",
        "centroid_x",
        "centroid_y",
        "centroid_longitude",
        "centroid_latitude",
    )
    _write_csv(output_dir / "demand_master.csv", master_rows, master_fields)
    _write_csv(
        output_dir / "demand_nodes.csv",
        [row for row in master_rows if row["node_role"] == "demand"],
        master_fields,
    )
    _write_csv(output_dir / "demand_components.csv", component_rows, component_fields)
    dictionary_fields = (
        "table_name",
        "field_name",
        "definition",
        "unit",
        "data_type",
        "source_or_derivation",
        "evidence_status",
    )
    master_dictionary = {
        "node_id": ("Stable service-node identifier", "none", "string", "deterministic component ordering", "generated_identifier"),
        "service_node_id": ("Compatibility alias of node_id", "none", "string", "node_id", "generated_identifier"),
        "node_role": ("Depot or demand role", "none", "string", "explicit Depot component exclusion", "generated_classification"),
        "role": ("Compatibility alias of node_role", "none", "string", "node_role", "generated_classification"),
        "demand_status": ("Road-service status before provider assessment", "none", "string", "demand-generation stage", "pending_assessment"),
        "affected_status": ("Scenario affected/unaffected status", "none", "string", "Depot unaffected assumption", "research_scenario"),
        "component_label": ("Connected-component raster label", "none", "integer", "8-neighbour component analysis", "generated_topology"),
        "component_order": ("Stable component ordering rank", "none", "integer", "profile stable-order rule", "generated_identifier"),
        "min_cell_index": ("Minimum row-major raster-cell index in component", "none", "integer", "population grid", "derived_raster_index"),
        "cell_count": ("Number of positive population cells in component", "100 m cells", "integer", "8-neighbour component analysis", "derived_raster_count"),
        "raw_population": ("Uncalibrated component population sum", "people", "number", "population raster sum", "raster_estimate"),
        "population": ("Official-total-calibrated component population", "people", "number", "raw population multiplied by calibration factor", "statistically_calibrated_estimate"),
        "population_value_status": ("Evidence label for population value", "none", "string", "profile calibration rule", "evidence_label"),
        "longitude": ("Population-weighted centroid longitude", "decimal degrees", "number", "component cell centres", "derived_location"),
        "latitude": ("Population-weighted centroid latitude", "decimal degrees", "number", "component cell centres", "derived_location"),
        "grid_x": ("Population-weighted centroid projected x", "metres", "number", "component cell centres", "derived_location"),
        "grid_y": ("Population-weighted centroid projected y", "metres", "number", "component cell centres", "derived_location"),
        "grid_crs": ("CRS of grid_x and grid_y", "none", "string", "processing grid", "metadata"),
        "population_threshold": ("Strict lower threshold for positive cells", "people per cell", "number", "regional profile", "research_configuration"),
        "calibration_factor": ("Official total divided by selected raster total", "dimensionless", "number", "profile total and raster", "statistical_calibration"),
    }
    dictionary_rows = []
    for field_name in master_fields:
        definition, unit, data_type, derivation, status = master_dictionary[field_name]
        dictionary_rows.append({
            "table_name": "demand_master.csv",
            "field_name": field_name,
            "definition": definition,
            "unit": unit,
            "data_type": data_type,
            "source_or_derivation": derivation,
            "evidence_status": status,
        })
    component_overrides = {
        "is_depot_component": ("Whether this is the uniquely matched Depot component", "none", "boolean", "explicit Depot point-in-component test", "generated_classification"),
        "centroid_x": master_dictionary["grid_x"],
        "centroid_y": master_dictionary["grid_y"],
        "centroid_longitude": master_dictionary["longitude"],
        "centroid_latitude": master_dictionary["latitude"],
    }
    for field_name in component_fields:
        dictionary_value = (
            component_overrides[field_name]
            if field_name in component_overrides
            else master_dictionary[field_name]
        )
        definition, unit, data_type, derivation, status = dictionary_value
        dictionary_rows.append({
            "table_name": "demand_components.csv",
            "field_name": field_name,
            "definition": definition,
            "unit": unit,
            "data_type": data_type,
            "source_or_derivation": derivation,
            "evidence_status": status,
        })
    _write_csv(output_dir / "demand_data_dictionary.csv", dictionary_rows, dictionary_fields)
    _atomic_text(
        output_dir / "demand_master.json",
        json.dumps(_json_ready(master_rows), ensure_ascii=False, indent=2) + "\n",
    )
    features = []
    for row in master_rows:
        properties = {key: value for key, value in row.items() if key not in {"longitude", "latitude"}}
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [row["longitude"], row["latitude"]],
                },
                "properties": properties,
            }
        )
    geojson = {
        "type": "FeatureCollection",
        "name": "demand_master",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    _atomic_text(
        output_dir / "demand_master.geojson",
        json.dumps(_json_ready(geojson), ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_text(
        output_dir / "demand_generation_audit.json",
        json.dumps(_json_ready(audit), ensure_ascii=False, indent=2) + "\n",
    )


def build_demand_master(
    *,
    project_root: Path,
    profile_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    profile_path = profile_path.resolve()
    output_dir = output_dir.resolve()
    if not profile_path.exists():
        raise DemandBuildError(f"Profile does not exist: {profile_path}")
    profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
    if not isinstance(profile, Mapping):
        raise DemandBuildError("Profile root must be a JSON object.")
    settings = load_settings(profile, project_root)

    existing = [output_dir / name for name in OUTPUT_NAMES if (output_dir / name).exists()]
    if existing and not overwrite:
        raise DemandBuildError(
            "Refusing to overwrite existing demand outputs: "
            + ", ".join(path.name for path in existing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    boundary = _read_boundary(settings)
    grid = build_population_grid(settings, boundary)
    labels, components, selected_total, selected_cells = _component_records(
        grid,
        settings.population_threshold,
    )
    _to_wgs84_coordinates(components, grid.crs)
    _apply_component_order(components, settings.stable_order)
    depot_label, depot_grid_x, depot_grid_y, depot_lon, depot_lat = _depot_component_label(
        settings,
        boundary,
        grid,
        labels,
    )
    matching = [row for row in components if row["component_label"] == depot_label]
    if len(matching) != 1:
        raise DemandBuildError("Depot did not uniquely identify one population component.")

    if settings.official_population_total is None:
        calibration_factor = 1.0
        population_status = "population_raster_estimate_uncalibrated"
    else:
        calibration_factor = settings.official_population_total / selected_total
        population_status = "population_raster_scaled_to_official_total"

    for component in components:
        component["population"] = component["raw_population"] * calibration_factor
        component["is_depot_component"] = component["component_label"] == depot_label

    depot_component = matching[0]
    master_rows: list[dict[str, Any]] = [
        {
            "node_id": settings.depot_id,
            "node_role": "depot",
            "demand_status": "excluded_unaffected_depot_component",
            "affected_status": "unaffected",
            "component_label": depot_component["component_label"],
            "component_order": depot_component["component_order"],
            "min_cell_index": depot_component["min_cell_index"],
            "cell_count": depot_component["cell_count"],
            "raw_population": depot_component["raw_population"],
            "population": depot_component["population"],
            "population_value_status": population_status,
            "longitude": depot_lon,
            "latitude": depot_lat,
            "grid_x": depot_grid_x,
            "grid_y": depot_grid_y,
            "grid_crs": grid.crs.to_string(),
            "population_threshold": settings.population_threshold,
            "calibration_factor": calibration_factor,
        }
    ]
    demand_components = [row for row in components if not row["is_depot_component"]]
    id_width = max(3, len(str(len(demand_components))))
    for demand_number, component in enumerate(demand_components, start=1):
        master_rows.append(
            {
                "node_id": f"{settings.demand_id_prefix}{demand_number:0{id_width}d}",
                "node_role": "demand",
                "demand_status": "candidate_demand",
                "affected_status": "affected_or_service_required",
                "component_label": component["component_label"],
                "component_order": component["component_order"],
                "min_cell_index": component["min_cell_index"],
                "cell_count": component["cell_count"],
                "raw_population": component["raw_population"],
                "population": component["population"],
                "population_value_status": population_status,
                "longitude": component["centroid_longitude"],
                "latitude": component["centroid_latitude"],
                "grid_x": component["centroid_x"],
                "grid_y": component["centroid_y"],
                "grid_crs": grid.crs.to_string(),
                "population_threshold": settings.population_threshold,
                "calibration_factor": calibration_factor,
            }
        )

    component_population_total = float(sum(row["population"] for row in components))
    expected_total = (
        selected_total
        if settings.official_population_total is None
        else settings.official_population_total
    )
    population_error = abs(component_population_total - expected_total)
    allowed_error = max(1e-9, abs(expected_total) * 1e-10)
    if population_error > allowed_error:
        raise DemandBuildError(
            "Population calibration failed conservation check: "
            f"expected={expected_total}, observed={component_population_total}."
        )

    with rasterio.open(settings.population_raster_path) as population_dataset:
        population_input_crs = population_dataset.crs.to_string()

    audit = {
        "schema_version": "build-truck-drone-network.demand-audit.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": "scripts/build_demand_master.py",
        "project_root": str(project_root),
        "profile_path": str(profile_path),
        "profile_sha256": _sha256_file(profile_path),
        "inputs": {
            "administrative_boundary": _input_hash(settings.boundary_path),
            "population_raster": _input_hash(settings.population_raster_path),
        },
        "input_crs": {
            "administrative_boundary": str(boundary.crs),
            "population_raster": population_input_crs,
        },
        "grid": {
            "mode": grid.mode,
            "boundary_cell_inclusion": settings.boundary_rasterization_mode,
            "boundary_assumed_crs": (
                settings.boundary_assumed_crs.to_string()
                if settings.boundary_assumed_crs is not None
                else None
            ),
            "boundary_selector": (
                {
                    "field": settings.boundary_selector_field,
                    "equals": settings.boundary_selector_value,
                }
                if settings.boundary_selector_field is not None
                else None
            ),
            "grid_crs": grid.crs.to_string(),
            "transform": list(grid.transform)[:6],
            "rows": int(grid.values.shape[0]),
            "columns": int(grid.values.shape[1]),
            "target_resolution_m": settings.target_resolution_m,
            "native_resolution_x_m": grid.native_resolution_x_m,
            "native_resolution_y_m": grid.native_resolution_y_m,
            "native_resolution_tolerance_fraction": settings.resolution_tolerance_fraction,
            "resampling": "none" if grid.mode == "native_grid_preserved" else "rasterio.enums.Resampling.sum",
        },
        "population_conservation": {
            "source_total_in_boundary": grid.source_total_in_boundary,
            "target_total_before_threshold": grid.target_total_before_threshold,
            "resampling_conservation_factor": grid.conservation_factor,
            "relative_error_after_resampling": abs(
                grid.target_total_before_threshold - grid.source_total_in_boundary
            )
            / grid.source_total_in_boundary,
            "population_threshold_strictly_greater_than": settings.population_threshold,
            "selected_cell_count": selected_cells,
            "selected_raw_population_total": selected_total,
            "official_population_total": settings.official_population_total,
            "official_calibration_factor": calibration_factor,
            "final_component_population_total": component_population_total,
            "population_value_status": population_status,
            "invalid_or_nodata_cell_count": grid.invalid_cell_count,
            "negative_cell_count_set_to_zero": grid.negative_cell_count,
        },
        "component_method": {
            "connectivity": 8,
            "neighbourhood_structure": "3x3 all ones",
            "centroid": "population-weighted cell-centre centroid",
            "stable_order": settings.stable_order,
        },
        "node_counts": {
            "population_components_including_depot": len(components),
            "depot_components": 1,
            "demand_nodes_excluding_depot": len(demand_components),
            "master_nodes": len(master_rows),
        },
        "depot": {
            "node_id": settings.depot_id,
            "explicit_coordinate_crs": settings.depot_crs.to_string(),
            "explicit_x": settings.depot_x,
            "explicit_y": settings.depot_y,
            "component_label": depot_label,
            "component_population": depot_component["population"],
            "treatment": "marked unaffected and excluded from demand nodes",
        },
        "limitations": [
            "Generated node populations are raster-derived aggregates, not household observations.",
            (
                "Administrative-edge raster inclusion uses cell centres; projected resampling documents a uniform boundary-mass conservation correction."
                if settings.boundary_rasterization_mode
                == "cell_center_all_touched_false"
                else "This profile explicitly replays a historical Pillow source-order zone-label rasterization; it is a golden-regression compatibility rule and must not be inherited by other regions."
            ),
            "The Depot must fall directly in one above-threshold component; v1 does not search for or infer a nearby component.",
        ],
    }
    _write_outputs(output_dir, master_rows, components, audit)
    return audit


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a deterministic source-neutral demand master from a boundary, "
            "population raster, and explicit Depot coordinate."
        )
    )
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace only this script's six named output files; never modifies inputs.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        audit = build_demand_master(
            project_root=args.project_root,
            profile_path=args.profile,
            output_dir=args.output_dir,
            overwrite=args.overwrite,
        )
    except Exception as exc:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        failure = {
            "schema_version": "build-truck-drone-network.demand-failure.v1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _atomic_text(
            args.output_dir / "demand_generation_failure.json",
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n",
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "demand_ready",
                "output_dir": str(args.output_dir.resolve()),
                "demand_nodes": audit["node_counts"]["demand_nodes_excluding_depot"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
