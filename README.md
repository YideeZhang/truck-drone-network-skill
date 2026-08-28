# Truck–Drone Network Skill

A self-contained Codex Skill and Python pipeline for real regional road/population/DEM inputs, a reusable whole-administration mother graph, Wuding-style one-Depot scope networks, truck and two-payload drone energy costs, microgrid road-service catchments, and optional experimental road disruptions.

Repository: [YideeZhang/truck-drone-network-skill](https://github.com/YideeZhang/truck-drone-network-skill). It is initially **private**. The owner must grant teammates repository access before clone; a 404 can mean missing permission, not a wrong URL. No new public software licence or data-redistribution permission is implied; see `NOTICE`.

Development/shared environment: `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`; original-host interpreter `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv/Scripts/python.exe`. All development verification used that existing environment. A teammate uses a separately confirmed existing Python 3.12 interpreter and records it. No command in this pipeline creates a venv or installs packages automatically.

## Start with Codex

```powershell
git clone https://github.com/YideeZhang/truck-drone-network-skill.git C:/tdn
```

Open **that cloned repository** as the Codex project. The Skill is in `.agents/skills/build-truck-drone-network/SKILL.md`, the project-local location documented by [OpenAI](https://learn.chatgpt.com/docs/build-skills); no global skill installation is needed. Explicitly invoke `$build-truck-drone-network`. If it is not listed after opening the clone, restart/open a new task in that repository and point Codex to that exact `SKILL.md` instead of using a stale globally installed version.

Give Codex the complete [Chinese teammate prompt](docs/TEAMMATE_CODEX_PROMPT_ZH.md). It covers actual NZ administrative selection, evidence-backed data downloads, parameter review, whole-region generation, scope slicing and final dataset packaging. Read the [NZ data guide](docs/NEW_ZEALAND_DATA_GUIDE.md) for exact websites and file types, including manual portal-download instructions.

Use a short working path, for example `C:/tdn/workspaces/nz`. Long nested Windows paths can fail below a scenario/network directory. Do not change OS settings as an implicit fix. `workspaces/`, raw files and generated networks are Git-ignored.

## What is implemented

- Population **count** raster → 100 m conservation-aware grid → 8-neighbour components → calibrated original customer centroids and explicit Depot component.
- OSM PBF/vector preparation, same-grade noding and source lineage; original directions retained. An inclusive 200 m / mutual-Depot-connectivity test rejects OSM at **>=20%** unavailable demand, with no invented connector. NZ can use a verified offline local-road fallback. No NZ AMap requests.
- Reusable mother physical geometry, per-edge DEM profiles, two road classes and directional truck costs. Strict paths recomputed after changing service-anchor blockers; not cropped from an old logical graph.
- One Depot per network, local goods demand, emergency household-electricity estimates, exact min-site / population-weighted-time microgrid service assignment with both time and distance limits, and materialized incident-edge backbone.
- Raw full(road anchor → customer centroid) / empty(customer centroid → road anchor) drone time/energy catalog, **2 N²** legs. Separate centroid-to-centroid empty/full candidates, raw cost matrices, single-flight reserve flags and full-out/empty-return battery tests.
- Real-road path GeoJSON/GPKG, abstract straight link display, flight candidate corridor layers and overview maps.
- Optional `portable_g2_v2` generated road-disruption scenarios using a frozen full-region rank reference, within-edge terrain slopes, common connection-level draws and corrected cost multipliers. Disabled in the NZ template until explicitly approved.
- Independent table/lineage/cost validation, deterministic scientific CSV checks, create-only atomic release publication, dataset ZIP builder with licensed-source inclusion and explicit missing-download manifest.

## Quick offline forward test

`python` below means the approved interpreter, not an invitation to use an arbitrary system Python. On the original host use the full shared path stated above. Audit installed packages against `requirements-lock-tested.txt`; ask before changing a shared environment. Only if installation is approved, use that interpreter's `-m pip install -r requirements-lock-tested.txt`. The broad compatible direct dependency bounds are in `requirements.txt`.

```powershell
python -m pytest tests -q -p no:cacheprovider --basetemp C:/tdn-test-new

python .agents/skills/build-truck-drone-network/scripts/create_demo_inputs.py --output C:/tdn/workspaces/demo-new
python network_pipeline.py --project-root C:/tdn/workspaces/demo-new --profile C:/tdn/workspaces/demo-new/parameters/synthetic_demo.json --stage full
```

Choose a **new** test/demo directory each time. Pytest may otherwise clean its specified test directory. The demo generates labelled **synthetic** population/roads/DEM/boundary, never real NZ observations. The forward tests also repeat the build independently, reuse a mother graph and check first-stage failure safety.

## Real-data generation

1. Copy `.agents/skills/build-truck-drone-network/assets/profiles/nz_template.json` into your workspace's `parameters/`. Fill exact input/provenance, region/Depot, official population and local electricity/household fields. The unfilled template **must fail**, not silently borrow Chinese data.
2. Obtain actual raw files from the data guide, retain licences/metadata and register all originals and derived preparation files in `inputs`. The public-download helper is dry-run unless `--download` is explicitly set; LINZ/Stats NZ manual items are reported as such.
3. Prepare OSM from the archived raw file, using an explicit area/CRS/buffer:

```powershell
python .agents/skills/build-truck-drone-network/scripts/prepare_osm.py --source <raw.osm.pbf> --boundary <boundary.gpkg> --boundary-layer boundary --output <data/prepared/nz_selected/osm_roads.gpkg> --crs EPSG:2193 --buffer-m 2000 --source-url https://download.geofabrik.de/australia-oceania/new-zealand.html
```

4. Review assumptions, then run a mother release. `--output-root` is relative to the workspace root, not the code repository:

```powershell
python network_pipeline.py --project-root <workspace> --profile <profile.json> --stage mother --output-root network/processed/<region>/mother_v1
```

5. Inspect the whole graph, component counts, customer/anchor locations, source gate and DEM extent. Configure the full administration as a scope (all terminals) and optional real-subunit scopes. Candidate Depot variants must be existing terminal IDs; each produces a separate network with one active Depot. Then:

```powershell
python network_pipeline.py --project-root <workspace> --profile <profile.json> --stage full --mother-root network/processed/<region>/mother_v1 --output-root network/processed/<region>/release_v1
python .agents/skills/build-truck-drone-network/scripts/validate_pipeline.py --project-root <workspace> --release <workspace>/network/processed/<region>/release_v1
```

6. Repeat with a new release path and compare scientific hashes, excluding release-specific registry paths. The [output contract](.agents/skills/build-truck-drone-network/references/output-contract.md) documents keys, types, units and directories.
7. Package the **whole graph** and scope evidence. With licence approval, include actual source files; missing/restricted sources remain an explicit download manifest:

```powershell
python .agents/skills/build-truck-drone-network/scripts/package_dataset.py --project-root <workspace> --release <workspace>/network/processed/<region>/release_v1 --output <workspace>/deliverables/<region>_network_v1.zip --include-permitted-raw
```

The ZIP preserves project-relative paths, includes code/configuration and produces `DATASET_BUNDLE_MANIFEST.json`. Do not push large GIS datasets through ordinary Git or upload licensed data without checking terms.

## Scope and evidence limits

This distribution **does not contain a completed real NZ network**, bulk Wuding data, AMap responses/keys or a private-project runtime dependency. It includes a tested synthetic end-to-end example and explicit real-data acquisition instructions. A real NZ selection and build remains the teammate's job in the prompt.

It ports the network science into `portable-wuding-style.v2`; it does **not** claim to reproduce all historical Wuding production hashes, every previous prototype's class-promotion rule, or the production Model loader unchanged. It retains real road geometry rather than applying destructive degree-two geometric simplification. The full-region mother is a source-selected graph, not proof of complete real-world road coverage.

Missing road vehicle/turn restrictions, electrical capacity, off-grid feeder design, continuous obstacle clearance, aviation authorisation, payload-at-altitude limits and observed disaster calibration remain separate tasks. Costs are planning proxies; G2 scenarios are generated experiments; neither is operational certification. No Model/Gurobi/training is run. See the [method](.agents/skills/build-truck-drone-network/references/energy-and-scenarios.md) and [boundaries](.agents/skills/build-truck-drone-network/references/model-boundaries.md).
