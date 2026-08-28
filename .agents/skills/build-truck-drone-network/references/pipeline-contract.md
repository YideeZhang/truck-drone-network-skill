# Portable pipeline contract v2

Development/shared Python environment: `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`; interpreter `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv/Scripts/python.exe`. Teammate executions record their own approved existing interpreter, without creating an environment implicitly.

## Input and publication boundary

All configured inputs must resolve inside one project root. They must exist and have provenance before execution. Source inventory entries contain role, path, source name/URL, licence, acquisition time, units, optional expected SHA-256 and redistribution permission. Each input is hashed once for the run. Raw data are read-only. Prefer single-file GeoPackage/GeoTIFF sources; list all Shapefile components if used.

The profile supplies regional and scientific values. `approval.status=approved_for_preprocessing` is an explicit gate. A template is not approval. An unresolved electricity statistic, unknown DEM datum or unresolved Depot cannot be replaced by a Chinese value or zero.

`--stage mother` creates the administrative population universe and physical graph, without logical scope networks. `--stage full` creates the mother graph and all configured scopes, or reuses a previously validated mother graph with `--mother-root`. A new release is staged as a short sibling directory and renamed only after validation. Failure leaves staging as audit evidence, never as an active runtime path. Output paths cannot overwrite raw/model/manuscript or an existing release.

## Population

1. Verify population units are **people per cell**, not people/km², land cover, lights or an image. Select the actual administrative boundary by its attribute ID, not the folder name.
2. Preserve approximately 100 m native grids within the configured tolerance. For other resolutions use Rasterio `Resampling.sum` onto a 100 m projected grid; record the within-boundary mass correction used by the inherited demand builder. No bilinear interpolation of population counts.
3. Include pixels by cell centre (`all_touched=false`), then threshold and label **8-neighbour** components. A positive threshold excludes population; record the discarded mass and obtain approval rather than tuning it to node counts.
4. Population-weighted cell-centre centroids define customer delivery positions. Stable component order is raster row/column. IDs are stable only for the same inputs and configuration; never assume IDs survive a new raster/year/threshold.
5. The explicit Depot must identify exactly one above-threshold component; no nearest-component guessing. It is retained as one terminal identity but excluded from demand for the network in which it is Depot. In another approved Depot rotation it becomes ordinary demand. This avoids silently deleting an administrative centre's population from every network.
6. Calibrate proportionally to an official same-scope population only when supplied. Otherwise factor=1 and status=raster estimate. The CSV contains both raw and calibrated population. Mixing raster year and census year is a documented modelling choice, not household observation.
7. Optional real subunit polygons assign each component centroid to exactly one subunit. Boundary-spanning components are not split secretly; report the centroid-assignment convention. Ambiguous/outside-centroid assignment stops. Do not inherit historical Huanzhou-only rasterization.

## Mother graph and slices

Keep the selected physical source throughout the administrative boundary plus a configured context buffer. Retain all components in the mother, even those not serving a demand. The buffer must be justified and the source/DEM must cover it. County-clipped roads can falsely disconnect routes that leave and re-enter the county; inspect this before approval.

The graph carries observed direction evidence; no automatic conversion to undirected truck legality. A service is usable only when its anchor is reachable **from and to** Depot. Exact source edges are split at real access points. Tiny coordinate rounding is deterministic numeric precision, not a user-sized spatial merge.

Slices select service IDs by subunit membership (or an explicit reviewed ID list), **not** by cutting the previous logical-arc table. For every origin, canonical Dijkstra uses time → distance → ordered directed physical arc-ID sequence. Other current service anchors may be reached as destinations but not expanded as intermediate vertices. Ordinary physical nodes can be intermediate. This is equivalent to removing all other terminals for each ordered endpoint pair. Unordered service pairs need not share an identical physical route in a directed graph.

Service identities sharing one road anchor retain independent population/goods/flight coordinates. The truck anchor registry deduplicates routing coordinates, so logical self/zero-time arcs are not created. Merging humanitarian identities requires a separate explicit contract; no Wuding-specific WD_E118/WD_E120 rule is applied in NZ.

Physical edge profiles/costs are calculated once in the mother. A scope materializes the union of its ordered path lineages and energy incident-edge backbone. Its runtime files never point into private archives or staging. A new slice can reuse mother geometry/profile bytes; strict links and microgrid assignment are recomputed.

## Reproducibility and size gates

The manifest records input hashes, parameter hash, source-script hash, environment and scientific CSV hashes. Repeated output registry paths and timestamps differ intentionally; compare scientific tables, not entire GPKG/ZIP binaries or release-specific registry URLs. GPKG drivers can store creation metadata.

Candidate count, raw flight count and exact microgrid runtime have explicit profile ceilings. Exceeding a ceiling stops; it never silently keeps the first 20 customers. Raise a ceiling only after estimating memory/runtime and obtaining approval. Python 3.12/Windows with the tested requirements is the exercised platform; other OS/version combinations must run their own forward tests.
