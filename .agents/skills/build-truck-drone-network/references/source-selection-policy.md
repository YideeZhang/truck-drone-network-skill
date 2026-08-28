# Real road-source policy

Development/shared environment: `C:/Users/59167/Desktop/Home/PythonProject/E-Truck-Drone-System/.venv`; use its `Scripts/python.exe` on the original host and record the approved interpreter elsewhere.

OSM is attempted first. Motor-road exclusions are profile-driven; the baseline excludes footway/path/cycleway/steps/pedestrian/bridleway and explicit access=no/private. Preserve original identifiers, geometry, source class, name/ref, one-way, layer, bridge and tunnel tags. Missing tags mean unknown. Track remains possible residential research geometry with `track_access_uncertain=true`, not proof of truck access. If access tags are absent from a simplified export, retrieve richer OSM PBF or a licensed local source before calling the coverage result operationally adequate.

For external demand set D (Depot excluded), a point is inaccessible if its chosen road access is more than 200 m from the reference customer location **or** its anchor is not mutually reachable with Depot on the directed road graph. No road/no valid snap is also failure. Exactly 200 m is covered; ratio >=0.20 rejects a source. Depot must attach independently of the ratio. All rejected/deferred demand identities and populations remain in the output and in drone candidates.

Snapping uses the closest real line projection, with a nearby existing junction preferred only within the configured radius and additional-distance allowance. It then splits that real edge, never creates a customer-to-road driving segment. Export the actual centroid, road anchor, offset and decision. This v2 projection convention is explicit; it is not a claim to reproduce every historical nearest-vertex snap.

Noding uses Shapely/GEOS on the same-grade layer/bridge/tunnel group. Distinct-grade interior crossings are not joined. Coincident endpoints reconnect approaches. Retain line lineage; refuse an unmapped segment rather than nearest-source guessing. Route samples and OSM tags do not contain full turn restrictions: a production turn-aware/vehicle-restricted network requires additional data and review.

Final research classes are only secondary/residential. NZ secondary seeds must be explicitly configured, e.g. an approved mapping of trunk/primary/secondary and their links. Do not assume NZ uses China's road classes. The only portable automatic class repair is a <=profile-length residential chain with no interior junction between secondary seeds. It changes a **research class**, not an observed highway upgrade. There is no geometry averaging or automatic deletion of close parallel roads. Degree-two shape vertices are retained; strict logical abstraction reduces routing terminals without discarding geometry.

Outside mainland China, AMap is forbidden. If OSM fails, request verified licensed local geometry (e.g. LINZ/local road controlling authority). Configure it as `roads.licensed`, retaining separate provider labels and rerun the same quality gate. This is whole-source selection, not an undocumented mixed-geometry overlay. A manual mixed-source supplement needs a separately reviewed provenance-preserving input.

In China, this portable package can read a previously authorised, prepared AMap-derived vector as an offline licensed input, with `written_authorization_confirmed=true`. It has **no live AMap crawler or secret handling**. AMap route-derived samples must not be described as complete road databases. Unverified attributes cannot be relabelled as OSM direct evidence. The old private Wuding API response archive is neither required nor distributed.

If a licensed fallback also fails >=20%, stop for manual source review; do not fabricate connectors or change the denominator. The mother report records every attempted provider and the selected source.
