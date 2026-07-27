"""Step 3b — VRPP (Vehicle Routing Problem with Profits) in Gurobi.

Model (identical to the one in the original notebooks):

    max  R * SUM_i S_i g_i  -  C * SUM_ij D_ij x_ij  -  OMEGA * k
    s.t. k <= MAX_ROUTES

    x_ij  binary       arc (i,j) used
    g_i   binary       point i collected             (g_i = 1 forced for MustGo)
    y_ij  continuous   load carried (kg)             -> capacity Q
    f_ij  continuous   unit flow                     -> subtour elimination

Pre-processing:
  * arc filtering by bidirectional KNN + pairwise capacity;
  * automatic MustGo adjustment when their weight exceeds MAX_ROUTES x Q
    (the least full ones are downgraded to Optional — without this the model
    would be infeasible);
  * nearest-neighbour warm start over the MustGo points.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import gurobipy as gp
import numpy as np
import pandas as pd
from gurobipy import GRB, quicksum

from .config import Config
from .instance import Instance
from .lookahead import LookaheadResult


@dataclass
class Solution:
    day: str
    routes: list
    id_map: dict
    S: dict
    pct: dict
    kind: dict
    kind_orig: dict
    forced: dict
    g_val: dict
    bin_route: dict
    collected: list
    kpi: dict
    diag: dict
    df_not_visited: pd.DataFrame
    df_lookahead: pd.DataFrame
    D: np.ndarray
    TM: np.ndarray
    dep: int = 0


# ══════════════════════════════════════════════════════════════════
# Sub-steps
# ══════════════════════════════════════════════════════════════════
def _arcs(N, dep, D, S, cfg: Config, forced: set):
    """Bidirectional KNN + removal of pairs that exceed Q.

    Arcs to/from the depot are always kept (they guarantee feasibility).
    With `solver.keep_mustgo_arcs=True` every arc between MustGo points is
    kept as well (optional; off by default so the behaviour of the original
    notebooks is reproduced exactly).
    """
    NR = N[1:]
    knn = max(1, min(cfg.solver.knn, len(NR) - 1))
    neighbours = {i: {j for _, j in sorted((D[i][j], j) for j in NR if j != i)[:knn]} for i in NR}

    arcs, n_cap_removed = [], 0
    for i in N:
        for j in N:
            if i == j:
                continue
            if i == dep or j == dep:
                arcs.append((i, j))
                continue
            close = (j in neighbours[i]) or (i in neighbours[j])
            if cfg.solver.keep_mustgo_arcs and i in forced and j in forced:
                close = True
            if not close:
                continue
            if S[i] + S[j] > cfg.model.Q:      # pair impossible in a vehicle of capacity Q
                n_cap_removed += 1
                continue
            arcs.append((i, j))

    n_total = len(N) * (len(N) - 1)
    stats = {'knn': knn, 'n_total': n_total, 'n_arcs': len(arcs),
             'n_cap_removed': n_cap_removed, 'pct_kept': round(len(arcs) / n_total * 100, 1)}
    return arcs, stats


def _adjust_mustgo(NR, S, crit, route_ids, inst: Instance, cfg: Config):
    """If the MustGo weight exceeds MAX_ROUTES x Q, keep the fullest (%) and downgrade the rest."""
    mod = cfg.model
    mg = [i for i in NR if crit[i]]
    weight = sum(S[i] for i in mg)
    cap = mod.fleet_capacity_kg

    print('\n  MustGo capacity check:')
    print(f'    Original MustGo: {len(mg)} bins  |  {weight:.1f} kg')
    print(f'    Capacity {mod.MAX_ROUTES} x {mod.Q:g} kg = {cap:g} kg')

    n_downgraded = 0
    if weight > cap:
        ordered = sorted(mg, key=lambda i: S[i] / max(inst.cap.get(route_ids[i], 1.0), 1e-9),
                         reverse=True)
        kept, acc = [], 0.0
        for i in ordered:
            if acc + S[i] <= cap:
                kept.append(i)
                acc += S[i]
        n_downgraded = len(mg) - len(kept)
        kept_ids = {route_ids[i] for i in kept}
        for i in NR:
            crit[i] = route_ids[i] in kept_ids
        mg, weight = kept, acc
        print('    !!! Exceeds capacity — automatic adjustment by fill level:')
        print(f'    -> Kept as MustGo        : {len(mg):3d} ({weight:.1f} kg)')
        print(f'    -> Downgraded to Optional: {n_downgraded:3d} '
              f'(the solver may still collect them if profitable)')

    print(f'    OK — adjusted MG: {len(mg)} bins ({weight:.1f} kg)  slack: {cap - weight:.1f} kg')
    return mg, weight, n_downgraded


def _warm_start(NR, dep, S, D, cfg: Config, mandatory, arcs_s):
    """Nearest neighbour over the mandatory points, limited to MAX_ROUTES."""
    pending = set(mandatory)
    routes = []
    while pending and len(routes) < cfg.model.MAX_ROUTES:
        route, cur, load = [], dep, 0.0
        while True:
            best, best_d = None, float('inf')
            for j in pending:
                if load + S[j] <= cfg.model.Q and D[cur][j] < best_d and (cur, j) in arcs_s:
                    best, best_d = j, D[cur][j]
            if best is None:
                break
            route.append((cur, best))
            load += S[best]
            pending.discard(best)
            cur = best
        if not route:
            break
        route.append((cur, dep))
        routes.append(route)
    return routes


def _extract_routes(active, dep):
    """Rebuilds the routes from the active arcs; also returns orphan arcs."""
    out: dict = {}
    for (i, j) in active:
        out.setdefault(i, []).append(j)

    used, routes = set(), []
    for j0 in list(out.get(dep, [])):
        if (dep, j0) in used:
            continue
        route, cur, nxt = [], dep, j0
        while True:
            route.append((cur, nxt))
            used.add((cur, nxt))
            cur = nxt
            if cur == dep:
                break
            following = [t for t in out.get(cur, []) if (cur, t) not in used]
            if not following:
                break
            nxt = following[0]
        routes.append(route)

    return routes, [a for a in active if a not in used]


# ══════════════════════════════════════════════════════════════════
# Full model
# ══════════════════════════════════════════════════════════════════
def solve_vrpp(state: pd.DataFrame, la: LookaheadResult, inst: Instance,
               cfg: Config, day_label: str) -> Solution | None:
    mod, sv = cfg.model, cfg.solver
    mg_ini_s, mg_la_s = set(la.mustgo), set(la.mustgo_la)
    mg_all = mg_ini_s | mg_la_s

    route_ids = [0] + state['id_contentor'].tolist()
    levels = [0.0] + state['level_kg'].tolist()
    D, TM = inst.sub(route_ids)

    N = list(range(len(route_ids)))
    dep, NR = 0, N[1:]
    id_map = {i: route_ids[i] for i in N}
    S = {i: float(levels[i]) for i in N}
    pct = {i: (S[i] / inst.cap.get(route_ids[i], 1.0) * 100.0) if i > 0 else 0.0 for i in N}
    crit = {i: (route_ids[i] in mg_all) for i in N}
    kind_orig = {i: ('MustGo' if route_ids[i] in mg_ini_s
                     else 'MustGoLA' if route_ids[i] in mg_la_s else 'Optional') for i in N}

    # ── 1. adjust the MustGo to the available fleet ────────────────
    mg_ids, mg_weight, n_downgraded = _adjust_mustgo(NR, S, crit, route_ids, inst, cfg)
    kind = {i: ('MustGo' if crit[i]
                else 'MustGoLA' if route_ids[i] in mg_la_s else 'Optional') for i in N}

    # ── 2. arc filtering ───────────────────────────────────────────
    arcs, st = _arcs(N, dep, D, S, cfg, forced=set(mg_ids))
    arcs_s = set(arcs)
    print(f'\n  Total nodes  : {len(N)}  (depot + {len(NR)} bins)')
    print(f'  Total arcs   : {st["n_total"]}  ->  {st["n_arcs"]} after filtering  '
          f'({st["pct_kept"]}% kept)')
    print(f'  KNN={st["knn"]} (bidirectional) | Removed by capacity: {st["n_cap_removed"]}')
    print(f'  Forced MG    : {len(mg_ids)}  |  total S: {sum(S[i] for i in NR):.1f} kg '
          f'(mean {sum(S[i] for i in NR)/len(NR):.2f} kg/point)')
    print(f'  *** ACTIVE CONSTRAINT: k <= {mod.MAX_ROUTES} routes ***')

    # ── 3. model ───────────────────────────────────────────────────
    mdl = gp.Model(f'VRPP_{cfg.label}')
    mdl.setParam('OutputFlag', sv.output_flag)
    mdl.setParam('MIPGap', mod.MIP_GAP)
    mdl.setParam('TimeLimit', mod.TIME_LIMIT)
    if sv.seed is not None:
        mdl.setParam('Seed', int(sv.seed))
    mdl.Params.MIPFocus = sv.mip_focus
    mdl.Params.Heuristics = sv.heuristics
    mdl.Params.Threads = sv.threads
    mdl.Params.Cuts = sv.cuts
    mdl.Params.Presolve = sv.presolve
    mdl.Params.NodeMethod = sv.node_method

    x = mdl.addVars(arcs, vtype=GRB.BINARY, name='x')
    y = mdl.addVars(arcs, vtype=GRB.CONTINUOUS, lb=0, name='y')   # load (kg)
    f = mdl.addVars(arcs, vtype=GRB.CONTINUOUS, lb=0, name='f')   # unit flow
    g = mdl.addVars(NR, vtype=GRB.BINARY, name='g')
    k = mdl.addVar(lb=0, ub=mod.MAX_ROUTES, vtype=GRB.INTEGER, name='k')

    mdl.addConstr(k <= mod.MAX_ROUTES, name='max_routes')

    for i, j in arcs:                                     # flow-arc linking
        mdl.addConstr(y[i, j] <= mod.Q * x[i, j])
        mdl.addConstr(f[i, j] <= len(NR) * x[i, j])
    for i, j in arcs:                                     # lower bound (strengthening)
        if i != dep:
            mdl.addConstr(y[i, j] >= S[i] * x[i, j])
    for i in NR:                                          # load collected at i
        mdl.addConstr(quicksum(y[i, j] for j in N if (i, j) in arcs_s)
                      - quicksum(y[j, i] for j in N if (j, i) in arcs_s) == S[i] * g[i])
    for j in NR:                                          # vehicles leave empty
        if (dep, j) in arcs_s:
            mdl.addConstr(y[dep, j] == 0)

    mdl.addConstr(k == quicksum(x[dep, j] for j in NR if (dep, j) in arcs_s), name='k_departures')
    mdl.addConstr(quicksum(x[j, dep] for j in NR if (j, dep) in arcs_s) == k, name='k_arrivals')

    for i in NR:                                          # mandatory MustGo
        if crit[i]:
            mdl.addConstr(g[i] == 1)
    for j in NR:                                          # in-degree/out-degree = g_j
        mdl.addConstr(quicksum(x[i, j] for i in N if (i, j) in arcs_s) == g[j])
        mdl.addConstr(quicksum(x[j, t] for t in N if (j, t) in arcs_s) == g[j])

    mdl.addConstr(quicksum(f[dep, j] for j in NR if (dep, j) in arcs_s)
                  == quicksum(g[j] for j in NR))          # subtour elimination
    for j in NR:
        mdl.addConstr(quicksum(f[i, j] for i in N if (i, j) in arcs_s)
                      - quicksum(f[j, t] for t in N if (j, t) in arcs_s) == g[j])

    mdl.setObjective(mod.R * quicksum(S[i] * g[i] for i in NR)
                     - mod.C * quicksum(x[i, j] * D[i][j] for i, j in arcs)
                     - mod.OMEGA * k, GRB.MAXIMIZE)

    # ── 4. warm start ──────────────────────────────────────────────
    ws_routes = _warm_start(NR, dep, S, D, cfg, mg_ids, arcs_s)
    ws_arcs = {a for rt in ws_routes for a in rt}
    ws_nodes = {j for rt in ws_routes for (_, j) in rt if j != dep}
    for i, j in arcs:
        x[i, j].Start = 1.0 if (i, j) in ws_arcs else 0.0
    for i in NR:
        g[i].Start = 1.0 if i in ws_nodes else 0.0
    k.Start = float(len(ws_routes))
    print(f'  Warm start   : {len(ws_routes)}/{mod.MAX_ROUTES} routes  |  '
          f'{sum(1 for i in ws_nodes if crit[i])}/{len(mg_ids)} MG covered')

    # ── 5. optimisation ────────────────────────────────────────────
    t0 = time.time()
    mdl.optimize()
    ts = time.time() - t0

    if mdl.SolCount == 0:
        name = {1: 'LOADED', 2: 'OPTIMAL', 3: 'INFEASIBLE', 4: 'INF_OR_UNBD', 5: 'UNBOUNDED',
                9: 'TIME_LIMIT', 11: 'INTERRUPTED'}.get(mdl.status, str(mdl.status))
        print(f'  No solution (status={mdl.status} [{name}], SolCount=0)')
        if mdl.status == GRB.INFEASIBLE:
            print('  Suggestion: increase MAX_ROUTES or Q, or relax threshold_mg.')
        return None

    # ── 6. extraction ──────────────────────────────────────────────
    g_val = {i: int(round(g[i].X)) for i in NR}
    active = [(i, j) for i, j in arcs if x[i, j].X > 0.5]
    routes, orphans = _extract_routes(active, dep)
    if orphans:
        print(f'  WARNING: {len(orphans)} active arcs not connected to the depot: {orphans[:5]}')

    kv = int(round(k.X))
    collected = [id_map[j] for rt in routes for (_, j) in rt if j != dep]
    bin_route = {id_map[j]: nr for nr, rt in enumerate(routes, 1) for (_, j) in rt if j != dep}

    print(f'\n  Per-route capacity check (Q={mod.Q:g} kg):')
    for nr, rt in enumerate(routes, 1):
        kg_r = sum(S[j] for (_, j) in rt if j != dep)
        warning = '  *** EXCEEDS Q ***' if kg_r > mod.Q + 1e-3 else ''
        print(f'    Route {nr}: {kg_r:.1f} kg  ({kg_r/mod.Q*100:.1f}%){warning}')

    dist_km = sum(D[i][j] * x[i, j].X for i, j in arcs)
    tmin_tot = sum(TM[i][j] * x[i, j].X for i, j in arcs)
    waste_tot = sum(S[i] for i in NR if g_val[i])
    n_visit = sum(g_val.values())
    n_ini = sum(1 for i in NR if g_val[i] and kind[i] == 'MustGo')
    n_la_ = sum(1 for i in NR if g_val[i] and kind[i] == 'MustGoLA')
    n_opt = sum(1 for i in NR if g_val[i] and kind[i] == 'Optional')
    cap_pct = (waste_tot / (kv * mod.Q) * 100) if kv > 0 else 0.0

    nv_rows = [{
        'id_contentor': id_map[i],
        'Type_orig': kind_orig[i],
        'Type_solver': kind[i],
        'Level_pct': round(pct[i], 2),
        'Level_kg': round(S[i], 3),
        'CAP_CONT_kg': round(inst.cap.get(id_map[i], 0.0), 2),
        'Daily_Rate_pct': round(inst.ai_pct.get(id_map[i], 0.0), 2),
        'Reason': ('MG_downgraded_fleet_full' if (kind_orig[i] == 'MustGo' and not crit[i])
                   else 'Not_profitable'),
    } for i in NR if not g_val[i]]
    df_nv = (pd.DataFrame(nv_rows, columns=['id_contentor', 'Type_orig', 'Type_solver',
                                            'Level_pct', 'Level_kg', 'CAP_CONT_kg',
                                            'Daily_Rate_pct', 'Reason'])
             .sort_values(['Reason', 'Level_pct'], ascending=[True, False])
             .reset_index(drop=True))

    kpi = {
        'Instance': cfg.label,
        'Day': day_label,
        'Vehicles': kv,
        'MAX_ROUTES': mod.MAX_ROUTES,
        'N_Visited': n_visit,
        'N_MustGo': n_ini,
        'N_MG_downgraded': n_downgraded,
        'N_MustGoLA': n_la_,
        'N_Optional': n_opt,
        'N_Not_Visited': len(df_nv),
        'Total_Waste_kg': round(waste_tot, 2),
        'Total_Distance_m': round(dist_km * 1000, 1),
        'Total_Distance_km': round(dist_km, 4),
        'km_per_kg': round(dist_km / waste_tot, 6) if waste_tot > 0 else 0.0,
        'Travel_Time_min': round(tmin_tot, 2),
        'Capacity_Used_pct': round(cap_pct, 2),
        'Revenue_euro': round(mod.R * waste_tot, 4),
        'Distance_Cost_euro': round(mod.C * dist_km, 4),
        'Vehicle_Cost_euro': round(mod.OMEGA * kv, 4),
        'Net_Profit_euro': round(mdl.objVal, 4),
        'MIP_Gap_pct': round(mdl.MIPGap * 100, 4),
        'Solver_Time_s': round(ts, 2),
        'Solver_Time_h': round(ts / 3600, 4),
        'Solver_Status': mdl.status,
    }

    print(f'\n  [{day_label}] Vehicles:{kv}/{mod.MAX_ROUTES} | Visited:{n_visit} '
          f'(MG={n_ini} LA={n_la_} Opt={n_opt})')
    if n_downgraded:
        print(f'  Warning: {n_downgraded} MustGo downgraded to Optional (fleet too small)')
    print(f'  Waste:{waste_tot:.1f}kg | Dist:{dist_km:.3f}km | Time:{tmin_tot:.1f}min '
          f'| Cap:{cap_pct:.1f}%')
    print(f'  Revenue:{kpi["Revenue_euro"]:.2f}EUR | DistCost:{kpi["Distance_Cost_euro"]:.2f}EUR '
          f'| VehCost:{kpi["Vehicle_Cost_euro"]:.2f}EUR | Profit:{kpi["Net_Profit_euro"]:.2f}EUR')
    print(f'  Solver:{ts:.1f}s ({ts/3600:.2f}h)  Gap:{mdl.MIPGap*100:.2f}%')
    print(f'\n  Bins NOT visited : {len(df_nv)}')
    if not df_nv.empty:
        print(df_nv.head(20)[['id_contentor', 'Type_orig', 'Level_pct', 'CAP_CONT_kg', 'Reason']]
              .to_string(index=False))
        if len(df_nv) > 20:
            print(f'  ... (+{len(df_nv)-20} rows in sheet 7_Not_Visited)')

    return Solution(
        day=day_label, routes=routes, id_map=id_map, S=S, pct=pct, kind=kind, kind_orig=kind_orig,
        forced=crit, g_val=g_val, bin_route=bin_route, collected=collected, kpi=kpi,
        diag={**st, 'mg_weight': mg_weight, 'n_mg_forced': len(mg_ids),
              'n_downgraded': n_downgraded, 'orphans': len(orphans)},
        df_not_visited=df_nv, df_lookahead=la.detail, D=D, TM=TM, dep=dep,
    )
