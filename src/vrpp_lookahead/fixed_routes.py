"""Evaluation of externally supplied routes (e.g. the EVOX solution).

Given an ordered list of bin ids per route, this module measures that solution
with the project's own yardstick: the ORS distance matrix, the model parameters
of the YAML file and the MustGo / MustGoLA classification of `lookahead.py`.

Nothing is optimised here — the sequence is taken exactly as supplied. This is
what makes the numbers comparable against a VRPP solution: both are measured the
same way.

Each route is assumed to start and end at the depot (`id = 0`), which must NOT be
part of the supplied id list.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import Config
from .instance import Instance, _resolve_sheet
from .lookahead import lookahead

# Row order of the comparison report, and the key each row reads.
REPORT_ROWS = (
    ('Bins collected per route', 'n_bins'),
    ('MustGo bins', 'n_mustgo'),
    ('MustGo-LookAhead bins', 'n_mustgo_la'),
    ('Optional bins', 'n_optional'),
    ('Weight collected per route (kg)', 'weight_kg'),
    ('Distance per route (km)', 'distance_km'),
    ('Travel time per route (min)', 'travel_time_min'),
    ('Profit per route (euro)', 'profit_euro'),
    ('Ratio per route (km/Ton)', 'km_per_ton'),
    ('Capacity used (%)', 'cap_used_pct'),
    ('Driving at shift speed (min)', 'shift_driving_min'),
    ('Service time, bins x rate (min)', 'shift_service_min'),
    ('WORKING DAY per route (h)', 'shift_total_h'),
)
TOTAL_ROWS_SHIFT = (
    ('Longest working day (h)', 'max_shift_total_h'),
    ('Routes over the shift limits', 'n_routes_over_shift'),
)
TOTAL_ROWS = (
    ('Routes used (vehicles)', 'n_routes'),
    ('Total bins not visited (no coverage)', 'n_not_visited'),
    ('  of which MustGo (uncovered)', 'n_mustgo_missed'),
    ('  of which MustGo-LookAhead (uncovered)', 'n_mustgo_la_missed'),
    ('Total distance (km)', 'total_distance_km'),
    ('Total weight (kg)', 'total_weight_kg'),
    ('Total profit (euro)', 'total_profit_euro'),
    ('Total ratio (km/Ton)', 'total_km_per_ton'),
)


@dataclass
class RouteMetrics:
    """Per-route and overall metrics of a fixed-route solution."""

    per_route: pd.DataFrame     # one row per route, columns = the keys above
    totals: dict
    not_visited: list           # bin ids never visited
    label: str = ''


def _route_distance(inst: Instance, route: list) -> tuple:
    """(km, minutes) of depot -> route[0] -> ... -> route[-1] -> depot."""
    order = [0] + list(route)
    dist, tmin = inst.sub(order)
    n = len(order)
    km = sum(dist[i][i + 1] for i in range(n - 1)) + dist[n - 1][0]
    mins = sum(tmin[i][i + 1] for i in range(n - 1)) + tmin[n - 1][0]
    return float(km), float(mins)


def evaluate_fixed_routes(routes: dict, inst: Instance, cfg: Config,
                          label: str = 'fixed') -> RouteMetrics:
    """Measure a solution whose routes and visiting order are given.

    `routes` maps a route number to the ordered list of bin ids it serves,
    e.g. {1: [266, 268, ...], 2: [521, ...]}. The depot is implicit.
    """
    mod, sh = cfg.model, cfg.shift
    state = inst.initial_state()
    la = lookahead(state, inst, cfg, verbose=False)
    mustgo, mustgo_la = set(la.mustgo), set(la.mustgo_la)

    known = set(inst.ids)
    levels = state.set_index('id_contentor')['level_kg'].to_dict()

    seen: list = []
    rows = []
    for nr in sorted(routes):
        seq = list(routes[nr])
        unknown = [b for b in seq if b not in known]
        assert not unknown, f'Route {nr}: ids not present in the instance: {unknown[:10]}'
        assert 0 not in seq, f'Route {nr}: the depot (id 0) must not be listed explicitly'
        seen.extend(seq)

        weight = sum(float(levels.get(b, 0.0)) for b in seq)
        km, mins = _route_distance(inst, seq) if seq else (0.0, 0.0)
        profit = mod.R * weight - mod.C * km - mod.OMEGA

        # Working day: driving at the shift's average speed (NOT the ORS free-flow
        # time, which no collection round achieves) plus one service stop per bin.
        drive = sh.travel_min(km)
        service = len(seq) * sh.service_time_min
        total_min = drive + service

        row = {
            'route': nr,
            'n_bins': len(seq),
            'n_mustgo': sum(1 for b in seq if b in mustgo),
            'n_mustgo_la': sum(1 for b in seq if b in mustgo_la),
            'n_optional': sum(1 for b in seq if b not in mustgo and b not in mustgo_la),
            'weight_kg': round(weight, 2),
            'distance_km': round(km, 4),
            'travel_time_min': round(mins, 2),
            'profit_euro': round(profit, 4),
            'km_per_ton': round(km / (weight / 1000.0), 4) if weight > 0 else 0.0,
            'cap_used_pct': round(weight / mod.Q * 100.0, 2),
            'exceeds_Q': 'YES !!!' if weight > mod.Q + 1e-3 else 'no',
            'shift_driving_min': round(drive, 2),
            'shift_service_min': round(service, 2),
            'shift_total_min': round(total_min, 2),
            'shift_total_h': round(total_min / 60.0, 3),
        }
        for h in sh.report_h:
            row[f'fits_{h:g}h'] = 'no  EXCEEDS' if total_min > h * 60.0 + 1e-6 else 'yes'
        rows.append(row)

    duplicated = len(seen) - len(set(seen))
    assert duplicated == 0, f'{duplicated} bins appear in more than one route'

    collected = set(seen)
    all_bins = set(inst.bins['id_contentor'])
    not_visited = sorted(all_bins - collected)

    per_route = pd.DataFrame(rows)
    total_km = float(per_route['distance_km'].sum()) if rows else 0.0
    total_kg = float(per_route['weight_kg'].sum()) if rows else 0.0
    totals = {
        'n_routes': len(rows),
        'n_not_visited': len(not_visited),
        'n_mustgo_missed': len(mustgo - collected),
        'n_mustgo_la_missed': len(mustgo_la - collected),
        'total_distance_km': round(total_km, 4),
        'total_weight_kg': round(total_kg, 2),
        'total_travel_time_min': round(float(per_route['travel_time_min'].sum()), 2) if rows else 0.0,
        'total_profit_euro': round(float(per_route['profit_euro'].sum()), 4),
        'total_km_per_ton': round(total_km / (total_kg / 1000.0), 4) if total_kg > 0 else 0.0,
        # The shift is a per-crew limit, so the binding figure is the LONGEST route,
        # never the sum: two 5 h routes run in parallel, they do not make a 10 h day.
        'max_shift_total_h': round(float(per_route['shift_total_h'].max()), 3) if rows else 0.0,
        'n_routes_over_shift': int(sum(
            1 for _, r in per_route.iterrows()
            if r['shift_total_min'] > min(sh.report_h) * 60.0 + 1e-6)) if rows else 0,
    }

    return RouteMetrics(per_route=per_route, totals=totals,
                        not_visited=not_visited, label=label)


ROUTE_KPI_SHEETS = ('3_KPI_Routes', '3_KPI_Rotas')
ALL_BINS_SHEETS = ('8_All_Bins', '8_Todos_Contentores')
LOOKAHEAD_SHEETS = ('1_Lookahead',)


def solution_from_workbook(path, inst: Instance):
    """Rebuild a plottable `Solution` from a result workbook, without re-solving.

    Only the fields the map renderer reads are populated — routes, node mapping,
    levels and the per-bin classification. Everything else is left empty, so the
    object is good for `reporting.plot_routes` and nothing else.

    Levels and groups are read from the workbook rather than recomputed, so a
    map can be rebuilt for any simulated day, not just day 1.
    """
    from .vrpp import Solution

    available = pd.ExcelFile(path).sheet_names
    routes = routes_from_solution_workbook(path)

    df_all = pd.read_excel(path, sheet_name=_resolve_sheet(available, ALL_BINS_SHEETS, str(path)))
    col_kg = 'Level_kg' if 'Level_kg' in df_all.columns else 'Nivel_kg'
    col_pct = 'Level_pct' if 'Level_pct' in df_all.columns else 'Nivel_pct'
    col_got = 'Collected' if 'Collected' in df_all.columns else 'Recolhido'
    df_all = df_all.set_index('id_contentor')

    df_la = pd.read_excel(path, sheet_name=_resolve_sheet(available, LOOKAHEAD_SHEETS, str(path)))
    col_grp = 'Group' if 'Group' in df_la.columns else 'Grupo'
    groups = df_la.set_index('id_contentor')[col_grp].to_dict()
    label = {'MustGo': 'MustGo', 'MustGoLA': 'MustGoLA'}

    ids = list(inst.ids)                       # ids[0] == 0 == depot
    id_map = {i: b for i, b in enumerate(ids)}
    pos = {b: i for i, b in id_map.items()}

    collected = {b for seq in routes.values() for b in seq}
    S, pct, g_val, kind_orig = {0: 0.0}, {0: 0.0}, {}, {}
    for i, b in id_map.items():
        if i == 0:
            continue
        S[i] = float(df_all[col_kg].get(b, 0.0))
        pct[i] = float(df_all[col_pct].get(b, 0.0))
        g_val[i] = 1 if b in collected else 0
        kind_orig[i] = label.get(groups.get(b), 'Optional')

    arc_routes = []
    for nr in sorted(routes):
        seq = [pos[b] for b in routes[nr]]
        arc_routes.append(list(zip([0] + seq, seq + [0])))

    empty = pd.DataFrame()
    return Solution(
        day=Path(path).stem.replace('result_', ''), routes=arc_routes, id_map=id_map,
        S=S, pct=pct, kind=dict(kind_orig), kind_orig=kind_orig, forced={},
        g_val=g_val, bin_route={}, collected=sorted(collected), kpi={}, diag={},
        df_not_visited=empty, df_lookahead=empty,
        D=inst.dist, TM=inst.tmin, dep=0,
    )


def routes_from_solution_workbook(path, sheet: str | None = None) -> dict:
    """Read back the routes of a result workbook written by `reporting.py`.

    Used to feed an optimised solution through the very same measuring code as
    the external one, so the two blocks of the report are strictly comparable.
    Accepts the legacy Portuguese sheet and column names of pre-translation
    workbooks.
    """
    if sheet is None:
        available = pd.ExcelFile(path).sheet_names
        sheet = next((s for s in ROUTE_KPI_SHEETS if s in available), None)
        assert sheet, f'None of the sheets {ROUTE_KPI_SHEETS} found in {path} ({available})'
    df = pd.read_excel(path, sheet_name=sheet)
    col_route = 'Route' if 'Route' in df.columns else 'Rota'
    col_seq = 'Sequence' if 'Sequence' in df.columns else 'Sequencia'

    routes = {}
    for _, row in df.iterrows():
        parts = [p.strip() for p in str(row[col_seq]).split('>')]
        ids = [int(p) for p in parts if p and int(p) != 0]
        routes[int(row[col_route])] = ids
    return routes
