# Wuding-style portable output contract

Development/shared environment: `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`; interpreter `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv/Scripts/python.exe`. The run manifest records the actual selected environment.

The administrative graph is built once. Its scope folders follow Wuding's separation of common base evidence, one-Depot network variants and scenarios. Use `<region>`, not `wuding`, for NZ. The folder `<k>_town` is a structural compatibility label for k selected **actual subunits**, not a claim that NZ wards/SA2s are Chinese townships. `unit_ids=[]` means the complete administration; record that explicitly. A full administration with k wards may use `<k>_town/all_<region>`.

```text
<workspace>/
  data/raw/<region>/                # actual source files and original metadata
  data/prepared/<region>/           # documented PBF extraction/mosaic, raw untouched
  parameters/<region>_v1.json
  network/processed/<region>/release_v1/
    registry/
      parameter_snapshot.json
      input_inventory.json
      environment_snapshot.json
      run_manifest.json
      validation.json
      network_registry.csv
      area_registry.csv
      scenario_registry.csv
      road_source_assessment.json
      mother_network/
        population/                # demand master, calibration audit, components
        terminal_registry.csv
        road_nodes.csv
        physical_edges.csv
        directed_arc_nominal_costs.csv
        physical_edge_elevation_profiles.csv
        mother_network.gpkg
        physical_edges_wgs84.geojson
        overview.png
      scenario_profiles/portable_g2_v2/  # only if scenarios approved/enabled
    <k>_town/<scope>/
      base/
        physical_edges.csv
        directed_arc_nominal_costs.csv
        physical_edge_elevation_profiles.csv
        terminal_registry.csv
        truck_anchor_registry.csv
        customer_coordinates.csv
        truck_logical_arcs_nominal.csv
        fixed_path_lineage.csv
        physical_lineage_closure.csv
        strict_direct_pair_audit.csv
        strict_adjacency.csv
        truck_distance_m.csv
        truck_time_min.csv
        truck_energy_kwh.csv
        drone_cost_arcs.csv
        drone_corridors.csv
        drone_full_out_empty_return.csv
        drone_{empty,full}_{raw_total_energy_kwh,raw_total_time_s}_matrix.csv
        drone_customer_pair_candidates.csv
        network.gpkg
        truck_strict_paths.geojson
        truck_direct_links_straight.geojson
        drone_candidate_corridors.geojson
        customer_centroids.geojson
        network_overview.png
      networks/<network_id>/
        network_definition.json
        depot_definition.csv
        service_roles.csv
        goods_demand.csv
        energy_support_sites.csv
        microgrid_coverage.csv
        microgrid_selection_audit.json
        energy_backbone.csv
      scenarios/portable_g2_v2/<network_id>/
        scenario_registry.csv       # header-only when disabled
        <scenario_id>/              # only generated when approved
          scenario_definition.json
          connection_states.csv
          truck_costs.csv
```

## Keys, types and meanings

CSV booleans are `True`/`False`; IDs are strings, JSON arrays are UTF-8 JSON strings, scientific quantities are decimal numerics in the units below. Blank scenario costs mean unavailable, not zero. WKT uses the profile's metric CRS; GeoJSON exports use WGS84 longitude/latitude. Do not use GeoJSON degree lengths as kilometres.

| Interface | Key / important fields | Definition, units and source |
|---|---|---|
| terminal_registry | terminal_id; source_county_component_id; population/raw_population; home_township_id | One stable population component. Persons, calibrated versus raster estimate retained. Includes the potential Depot identity. |
| customer_coordinates | terminal_id; customer_coordinate_id; delivery_x_m/delivery_y_m; crs; source_lineage | Original component centroid in projected metres. **Not** a road anchor. Same coordinate role used by raw drone customer endpoints. |
| truck_anchor_registry | truck_anchor_id; x_m/y_m; member_service_ids_json | Unique operational real-road routing location; multiple service IDs may share it. |
| physical_edges | edge_id; from_node/to_node; length_m; geometry_wkt_m; source_feature_ids; lineage_json; final_road_class | True source-derived line geometry and ordered endpoint orientation. Units metres; final class research mapping separate from observed class. |
| directed_arc_nominal_costs | arc_id; edge_id; traversal_direction; reverse_directed_physical_arc_id; distance_m; time_min; truck_energy_kwh; cumulative_ascent_m/descent_m | Allowed directed traversal; cost derived from geometry/profile/vehicle config. Reverse can be absent. |
| elevation profiles | edge_id; sample_distances_m_json; smoothed_elevations_m_json | Increasing along-edge metres and DEM metres. Enables independent within-edge slope and ascent checks. |
| truck logical arcs | arc_id / truck_route_arc_id / contracted_logical_arc_id; contracted_logical_connection_id; from/to_truck_anchor_id; distance_m/time_min/nominal_energy_kwh | Strict paths not passing another current service anchor internally. Reciprocal directions share a connection ID, not necessarily the same physical path. |
| fixed_path_lineage | truck_route_arc_id; canonical_directed_physical_arc_ids_json; canonical_physical_edge_ids_json; canonical_physical_node_ids_json | Ordered evidence allowing full cost summation and geometry reconstruction. |
| goods_demand | network_id + terminal_id; goods_population_parameter; goods_demand_kg | Local non-Depot persons and ROUND_HALF_UP goods rule, not energy catchment population. |
| service_roles | network_id + terminal_id; is_depot; is_energy_site; goods_population; local_critical_energy_kwh; truck_service_status | One-Depot role overlay; deferred demand retained. |
| energy_support_sites | energy_site_id; truck_anchor_id; service_aggregated_population; service_aggregated_critical_energy_demand_kwh; member_terminal_ids_json | Aggregate covered persons and scenario electricity kWh. Not generation capacity. |
| microgrid_coverage | site_terminal_id + demand_terminal_id; eligible; assigned; time_min; distance_m; logical_path_ids_json | Same fastest path used for both limits. One assigned site per active demand. |
| energy_backbone | network_id + energy_site_id + edge_id | Incident real physical edges, protected exposure evidence for optional G2. |
| drone_cost_arcs | leg_id; anchor_terminal_id; customer_terminal_id; payload_state; origin/destination_coordinate_role; raw_total_time_s; raw_total_energy_kwh; parameter_hash | 2 N² role-aware full-out / empty-return raw legs. Seconds and kWh; no battery reserve deducted from raw energy. |
| drone customer candidates | origin_terminal_id + destination_terminal_id + payload_state; coordinate_role | 2 N(N−1) separate centroid-to-centroid arcs. Do not substitute for anchor-role raw catalog. |
| connection_states | scenario_id + contracted_logical_connection_id; state_code; p_normal/degraded/failed; time/energy_multiplier | Generated research states; codes 0/1/2 = normal/degraded/failed. |
| scenario truck_costs | scenario_id + truck_route_arc_id; available; distance_m; time_min; truck_energy_kwh | Preserves physical distance when available; cost multipliers; unavailable rows blank. |

`truck_direct_links_straight.geojson` is an abstract display chord and is explicitly flagged, not road geometry. Use `truck_strict_paths.geojson` for actual routed polylines. `drone_candidate_corridors.geojson` is a planning display layer, not an approved route.

The output dictionary is a portable contract, not the original project's complete production schema. Additional Model-side aliases/constraints require a reviewed adapter. A manifest proves bytes and processing lineage, not input completeness or operational validity.
