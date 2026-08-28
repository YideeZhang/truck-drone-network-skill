---
name: build-truck-drone-network
description: Build and audit reproducible regional road, population-demand, truck-energy, microgrid-service and two-payload drone networks. Use for whole administrative mother graphs, new-country source selection, Wuding-style scope slicing, deterministic costs and optional research road-disruption generation. Never treat a generated road or drone candidate as certified vehicle access or an approved flight.
---

# Truck–Drone Network Builder — portable v2

This is the self-contained distribution of the project's network method. Run its own scripts; do not search for the author's private Wuding outputs or call the original project's Model/Generator packages.

Development/shared environment: `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`; interpreter `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv/Scripts/python.exe`. On the original host use it exclusively. A teammate on another machine must choose and record an existing Python 3.12 interpreter; never assume this Windows path exists there or silently create/modify environments. Missing dependencies require an explicit install decision.

## Read before execution

Read completely: `references/pipeline-contract.md`, `references/source-selection-policy.md`, `references/model-boundaries.md`, `references/energy-and-scenarios.md`, `references/output-contract.md`. For NZ also read the repository's `docs/NEW_ZEALAND_DATA_GUIDE.md` and `docs/TEAMMATE_CODEX_PROMPT_ZH.md`. Repository root is three levels above this Skill directory.

## Workflow and stop conditions

1. Audit the selected administrative boundary, actual road source, population **counts** raster, DEM, official population and explicit Depot coordinate. Record year, units, horizontal/vertical CRS, licence, acquisition URL/time and input hash. Use `assets/profiles/nz_template.json`; all null/REQUIRED fields must be resolved. New-country household electricity or population data must not inherit Wuding values.
2. Select a real administrative unit by evidenced area/population/terrain comparison, not a preferred graph size. The Wuding benchmark is a project research boundary of 2,943.303294 km² and 2020 official population aggregate 239,059; the boundary is not asserted to be an official surveyed county polygon. Do not falsely claim NZ equivalence.
3. Download only public/licensed sources. `scripts/download_inputs.py` is opt-in for reviewed direct HTTPS URLs. Portal/manual entries remain explicit requests. `scripts/prepare_osm.py` reads archived PBF/GPKG/vector sources and retains real OSM IDs/tags. No AMap call is implemented or permitted for NZ. An authorised China **offline** AMap-derived vector may be configured as the licensed provider; live China API acquisition remains outside this portable version.
4. Populate profile assumptions and evidence. Run `scripts/run_network_pipeline.py --stage mother` first. It creates population components and the full source-selected physical graph with one-time DEM profiles/costs. Inspect source-gate results, coverage and maps. Stop if one giant population component leaves no external demand, the Depot is ambiguous, the source gate fails, or licences/CRS are unresolved. Do not change thresholds to force a desired count.
5. Set the whole-administration scope plus desired subunit scopes and explicit Depot variants. Run `--stage full --mother-root <previous-release>` to reuse unchanged raw inputs/physical geometry and recompute strict adjacency for each service-anchor set. Every network has exactly one Depot; a scope can contain several network variants, each with a different existing Depot. No fictitious link, cost interpolation, or reuse of cropped logical arcs.
6. Validate and repeat into a separate output path. Compare scientific CSV hashes excluding release-specific registries. Optional `scenarios.enabled=true` requires separate research approval; default NZ template is off. Reference ranks are frozen on the full regional graph, not independently within each slice.
7. Use `scripts/package_dataset.py` to bundle the release and permitted inputs, retaining original relative paths and a missing-download manifest. Do not push bulk GIS data into this code repository. Final report must distinguish real data, calibrated population, proxies and generated scenarios, including deferred customers.

## Commands

From repository root, with the chosen interpreter represented below as `python`:

```text
python network_pipeline.py --project-root <short-workspace> --profile <regional-profile.json> --stage mother
python network_pipeline.py --project-root <short-workspace> --profile <regional-profile.json> --stage full --mother-root <relative-mother-release> --output-root network/processed/<region>/release_v2
python .agents/skills/build-truck-drone-network/scripts/validate_pipeline.py --project-root <short-workspace> --release <absolute-release>
python .agents/skills/build-truck-drone-network/scripts/package_dataset.py --project-root <short-workspace> --release <absolute-release> --output <new.zip> --include-permitted-raw
```

The NZ profile is intentionally not executable before input/parameter completion. For the offline synthetic smoke run, use `scripts/create_demo_inputs.py`; its data must never enter a real case study. Read the root README for a complete test command.

## Non-negotiable boundaries

- No Model/Gurobi, training, manuscript, old release or raw overwrite. SciPy/HiGHS is used only for exact service-site preprocessing; timeout is a blocker, not a reason to substitute a heuristic.
- One physical graph, but **two coordinate roles**: actual population customer centroid versus real road access anchor. Preserve both. Co-located anchors do not silently aggregate service populations.
- Road classes are only `secondary` / `residential`, defined by the regional profile. Unknown truck restrictions remain unknown. OSM/AMap similarity is not a legal truck-access test.
- Raw drone legs are not round-trip feasibility. Full out/empty return must share a battery budget; 20% reserve is applied to the mission test, not subtracted from stored raw leg costs.
- No new safety, electrical-capacity or disaster-frequency claims. Local flight permission and truck access require separate competent review.
- Failure preserves audit staging but publishes no active interface. No fake PASS, placeholder costs, forced connectivity or parameter tuning to beautify results.
