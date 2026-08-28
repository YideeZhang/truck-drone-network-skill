# Model boundary and interpretation

Development/shared environment: `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`; original-host interpreter `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv/Scripts/python.exe`. Other hosts record their approved existing interpreter.

This repository constructs parameters; it does not import, modify or execute the E-Truck-Drone-System_TRE MILP, Gurobi, Methods training or manuscript. SciPy/HiGHS exact set cover is the isolated G1B-style **preprocessing** site-assignment problem, not the truck/drone routing Model. The count → population-weighted time → stable site-ID tie order is frozen; timeout/nonoptimal termination is a hard gate.

The interface follows the current Wuding directory/role philosophy but has its own `portable-wuding-style.v2` schema. It is not asserted to be a drop-in byte-identical production instance. A downstream Model Agent must check its loader, vehicle restrictions, role graph and feasibility semantics. Every `network_definition.json` records `model_adapter_validation=not_run_requires_model_agent_review`.

The local goods population at a customer and the aggregated energy catchment population at a site are distinct. Depot has zero goods/energy demand and zero service score. Its goods supply uses the approved unlimited exogenous/nonbinding research meaning. Co-located truck anchors do not erase independent customer IDs or add a zero-time logical arc. Deferred road customers retain demand and original delivery coordinates.

Microgrid coverage is a service catchment defined by a road path, not an electrical feeder. It does not prove generation/storage capacity, voltage, reliability or cross-village physical power distribution. No unrequested synthetic secondary-road corridor is promoted merely to make a site reachable. In this portable version, the existing secondary mapping and incident-site edge backbone are used; a requirement for a Depot-to-site all-secondary corridor is an additional approved scenario, not silently inherited from the historical Huanzhou prototype.

Truck roads are source evidence, not proof that a heavy truck may legally or physically use each road/bridge/turn. Generic OSM one-way rules are retained, but weight/width limits and OSM turn-restriction relations are not used by the routing implementation. NZ road controlling authority and vehicle rules require independent review before operational conclusions.

The simple drone model is a planning proxy: fixed sampled terrain-clearance corridor, two payload endpoints and no weather/air-density optimisation. Terrain sampling does not prove continuous obstacle clearance, aviation approval or a safe takeoff/landing site. Every flight record is marked a candidate. Energy necessary conditions are not full task feasibility and do not create scheduling slots or sorties.

Experimental disruption probabilities and multipliers are research assumptions, not empirical landslide frequencies or observed closures. No node-passability variables are generated. Actual disaster records, if later used, must be independently sourced and never confused with random draws.

Before mathematical-model use, approve: geographic scope, Depot location(s), population threshold/aggregation, class mapping, travel speeds, vehicle/load mass and energy coefficients, microgrid coverage/capacity interpretation, drone payload/proxy/reserve settings and optional disruption assumptions. No retrospective adjustment to obtain more attractive results.
