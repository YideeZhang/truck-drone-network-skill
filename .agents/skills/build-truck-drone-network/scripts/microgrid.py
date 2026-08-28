"""Portable version of the existing G1B lexicographic site-assignment routine.

Uses SciPy/HiGHS for preprocessing set cover, not the project routing MILP.
No approximate replacement is made on a timeout or nonoptimal termination.
"""
from __future__ import annotations

from decimal import Decimal

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix, vstack

from portable_core import GateError


def select_sites(ids, eligible, times, populations, config):
    ids = sorted(ids)
    if not ids:
        return [], {}, {"minimum_site_count": 0}
    if len(ids) > config["max_candidates"]:
        raise GateError("Microgrid candidate limit reached; obtain approval, do not thin demands")
    pairs = [(s, d) for s in range(len(ids)) for d in range(len(ids))
             if eligible[(ids[s], ids[d])]]
    n, m = len(ids), len(pairs)
    costs = np.array([float(Decimal(str(populations[ids[d]])) *
                            Decimal(str(times[(ids[s], ids[d])]))) for s, d in pairs])
    rows, cols, data, lb, ub = [], [], [], [], []
    for index, (s, d) in enumerate(pairs):
        rows.extend([index, index]); cols.extend([n+index, s]); data.extend([1., -1.])
        lb.append(-np.inf); ub.append(0.)
    for d in range(n):
        indices = [index for index, (_, customer) in enumerate(pairs) if customer == d]
        if not indices:
            raise GateError(f"No eligible energy service for {ids[d]}")
        rows.extend([m+d]*len(indices)); cols.extend([n+i for i in indices]); data.extend([1.]*len(indices))
        lb.append(1.); ub.append(1.)
    matrix = coo_matrix((data, (rows, cols)), shape=(m+n, n+m)).tocsr()

    def solve(objective, count=None, cost_upper=None, fixed=None):
        matrices, lower, upper = [matrix], list(lb), list(ub)
        if count is not None:
            matrices.append(coo_matrix((np.ones(n), (np.zeros(n), np.arange(n))), shape=(1,n+m)).tocsr())
            lower.append(count); upper.append(count)
        if cost_upper is not None:
            matrices.append(coo_matrix((costs, (np.zeros(m), n+np.arange(m))), shape=(1,n+m)).tocsr())
            lower.append(-np.inf); upper.append(cost_upper)
        bounds_lo, bounds_hi = np.zeros(n+m), np.ones(n+m)
        for index, value in (fixed or {}).items():
            bounds_lo[index] = bounds_hi[index] = value
        c = np.zeros(n+m)
        if objective == "count":
            c[:n] = 1
        elif objective == "cost":
            c[n:] = costs
        result = milp(c, integrality=np.ones(n+m), bounds=Bounds(bounds_lo,bounds_hi),
                      constraints=LinearConstraint(vstack(matrices), lower, upper),
                      options={"time_limit":config["solver_time_limit_s"], "mip_rel_gap":0., "disp":False})
        if result.status not in (0, 2):
            raise GateError(f"Microgrid exact solver did not finish: status={result.status}")
        return result

    primary = solve("count")
    if not primary.success:
        raise GateError("Microgrid set cover infeasible")
    count = int(round(primary.fun))
    secondary = solve("cost", count=count)
    if not secondary.success:
        raise GateError("Microgrid secondary objective infeasible")
    tolerance = config["objective_tolerance"]
    upper = float(secondary.fun) + tolerance
    fixed, chosen = {}, []
    for index in range(n):
        if len(chosen) == count:
            fixed[index] = 0
            continue
        trial = dict(fixed, **{})
        trial[index] = 1
        result = solve("feasibility", count=count, cost_upper=upper, fixed=trial)
        fixed[index] = int(result.success)
        if result.success:
            chosen.append(ids[index])
    if len(chosen) != count:
        raise GateError("Lexicographic microgrid tie-break failed")
    assignment = {d:min((s for s in chosen if eligible[(s,d)]), key=lambda s:(times[(s,d)],s)) for d in ids}
    value = sum(Decimal(str(populations[d])) * Decimal(str(times[(assignment[d],d)])) for d in ids)
    if float(value) > upper+tolerance:
        raise GateError("Microgrid chosen set violates weighted-time optimum")
    return chosen, assignment, {"minimum_site_count":count, "weighted_time":str(value),
                                "algorithm":"existing_G1B_exact_count_weighted_time_site_ID_lex",
                                "solver":"scipy.optimize.milp/HiGHS", "capacity_proven":False}
