# Energy and optional Generator definitions

Development/shared environment: `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`; interpreter `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv/Scripts/python.exe`. Record the approved actual interpreter on other hosts.

## Truck

For a directed physical edge of length d metres, speed v km/h and cumulative positive rise H+ metres:

`time_min = d / (v * 1000 / 60)`

`energy_kwh = flat_kwh_per_km * d/1000 + mass_kg * g * H+ / (3.6e6 * uphill_efficiency)`

Sample the real polyline every at-most configured interval (including endpoints), bilinearly interpolate DEM, apply a centred three-sample mean to interior samples and preserve endpoint heights. `H+ = sum(max(diff(z),0))`; descent is `sum(max(-diff(z),0))`. Reverse uses the same physical profile in reverse order. Downhill has no regeneration credit. Logical path distance/time/energy are sums of ordered directed physical arcs, not a straight-line-distance proxy or endpoint-only elevation difference.

The Wuding technology scenario was 28,000 kg, eActros 600, 1.03 kWh/km, uphill efficiency 0.90, 35/15 km/h for secondary/residential, and 40 m DEM interval. The distance coefficient came from a **40 t combination-vehicle official test**, not a measurement of the 28 t rigid truck. Adding a potential-energy term to a test-derived average can double-count some terrain consumption; treat the result as a conservative planning proxy and report the limitation. A teammate may retain these for controlled comparison only with a stated research-transfer approval.

Primary reference pages to recheck: [Mercedes-Benz product sheet](https://www.mercedes-benz-trucks.com/content/dam/brandhub/markets/gb/files/product-sheets/eActros-600-product-sheet.pdf.coredownload.pdf), [Daimler Truck European Testing Tour](https://www.daimlertruck.com/en/newsroom/events/2024/eactros-600-european-testing-tour-2024). These links are evidence pointers, not bundled datasets.

## Goods and local emergency electricity

`goods_kg = ROUND_HALF_UP(population * kg_per_person_24h, 0.001 kg)`; the baseline research rule is 2 kg/person/24 h. Sum calibrated populations first if an explicit aggregation contract applies, then round; do not silently sum pre-rounded goods.

`local_energy_kwh = population / people_per_household * annual_household_kwh / annual_hours * outage_hours * critical_fraction`.

12 h and 35% are the Wuding emergency scenario, not NZ observations. NZ household size and household annual electricity must come from NZ evidence with matching units/year or an explicitly approved local assumption. A kWh/connection statistic is not automatically kWh/household. Depot is zero. Deferred customers retain their local energy in service_roles but are not claimed covered by current truck service.

## Microgrid road-service catchments

Candidates are active non-Depot demand terminals. On the strict logical graph choose canonical fastest paths, testing **time <=45 min and length <=10 km on that same path** when those profile limits are approved. There is no separate shortest-distance route substituted to pass the second gate.

Site selection: exact minimum site count, then minimum sum(population × assigned service time), then lexicographically smallest sorted site-ID set; final assignment is nearest eligible selected site by time then ID. No capacity feasibility is claimed. Recompute this for each scope and Depot variant. An energy incident-edge backbone is materialized as separate evidence; its edges are protected in the optional G2 scenario process, not represented as newly built roads.

## Drone: fixed-altitude three-phase proxy

For planar endpoint distance d, ground endpoint elevations zi/zj, maximum sampled corridor DEM zmax and clearance h:

`z_cruise = max(zi, zj, zmax) + h`

`t_up = (z_cruise-zi)/v_up`, `t_cruise = d/v_horizontal`, `t_down = (z_cruise-zj)/v_down` (seconds).

For each payload state p, nominal battery energy B kWh, depletion-range endpoint Rp km and depletion-hover endpoint Tp minutes:

`horizontal_rate_p = B/Rp` (kWh/km), `hover_power_p = B/(Tp/60)` (kW).

`E_p = horizontal_rate_p*d/1000 + hover_power_p*(t_up+t_down)/3600`.

`time_s = t_up+t_cruise+t_down`.

The **same positive hover-power proxy** is used for climb and descent; descent is not negative energy or regeneration. No temperature, wind, humidity, density, drag/rotor fitting, terrain-following envelope, hovering service time, battery ageing or intermediate-payload interpolation is introduced. These exclusions limit interpretation and must remain in reports.

The retained historical FlyCart 200 four-DB2400/DL200 example has B=9.60848 kWh, payloads 0/200 kg, ranges 36/10 km, hover endpoints 25/7 min, horizontal speed 20 m/s, up/down speeds 3/3 m/s, clearance 60 m, sampling 30 m and 20% reserve. Product endpoints are battery-depletion test points, not a guarantee at NZ mountain sites. Verify the actual configuration/conditions at [DJI product](https://www.dji.com/cn/flycart-200) and [specifications](https://www.dji.com/cn/flycart-200/specs). The software does not assert payload certification at every altitude.

Two separate outputs avoid a common confusion:

- Raw role-aware catalog: for N service terminals, **2 N²** legs, full(road anchor i → original customer centroid j), empty(customer centroid j → road anchor i). Diagonal IDs can be nonzero real flights because roles have different coordinates. No reserve is subtracted from raw costs.
- Optional customer-centroid graph: 2 N(N−1) payload-state arcs, both directions and both empty/full states. These are not the road-anchor role catalog.

Single-flight necessary energy test: `E_p <= (1-reserve)*B`.

Full-out/empty-return to the same anchor test: `E_full(i→j) + E_empty(j→i) <= (1-reserve)*B`.

Passing one flight is **not** evidence that the return is feasible. Altitude flag is separate; no flag constitutes flight permission. Raw costs, flags and full/empty/round-trip matrices are all retained.

## Optional corrected G2-style research scenarios

Default NZ template disables scenarios. To enable, record explicit approval of transferred assumptions. The independent portable version is `portable_g2_v2`; do not reuse Wuding production scenario IDs or claim observed disasters.

1. Derive each master logical arc's Smax and Smean from positive within-physical-edge increments only. Smax=max(100*abs(dz/dd)); Smean=100*sum(abs(dz))/sum(dd). Never splice elevations across different physical edges. Freeze published values to 12 significant figures.
2. Freeze `registry/scenario_profiles/portable_g2_v2/county_master_rank_reference.csv` on the entire administration's logical graph. Compute `Fref(x)=(count(ref<x)+0.5*count(ref=x))/N`. All slices use this same reference; do not rank each slice independently.
3. `v = .7*Fref(Smax)+.3*Fref(Smean)`; a reciprocal logical connection uses the larger directional v. `damage=lambda*v`, with example lambdas .2/.5/.8.
4. Cumulative degradation `fD=sigmoid(10*(damage-.25))`; cumulative failure `fF=sigmoid(10*(damage-.55))`. `p_normal=1-fD`, `p_failed=fF` only when eligible, `p_degraded=fD-p_failed`. Protected failure mass remains degraded.
5. Failure is suppressed at an energy-site endpoint or where every physical edge is secondary/protected incident backbone. Unprotected residential exposure permits failure. One deterministic hash draw per scope/replicate/connection is shared by both directions and across severity/Depot variants.
6. Clip `q=(fD-sigmoid(-2.5))/(sigmoid(5.5)-sigmoid(-2.5))` to [0,1]. Normal multipliers=1; degraded time=1.10+1.90q, energy=1.05+.95q; failure has availability=false and blank costs, never a zero-cost usable edge. Geometric length does not change. Drone raw costs remain unchanged.

Every coefficient, bound and seed namespace is in the profile. Three severities × two replicates gives six scenarios per network. Outputs contain connection states, directed truck costs and definitions, with no node-passability or drone assignment/slot invention.
