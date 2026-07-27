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

import pandas as pd

from .config import Config
from .instance import Instance
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
)
TOTAL_ROWS = (
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
    mod = cfg.model
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

        rows.append({
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
        })

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
    }

    return RouteMetrics(per_route=per_route, totals=totals,
                        not_visited=not_visited, label=label)


ROUTE_KPI_SHEETS = ('3_KPI_Routes', '3_KPI_Rotas')


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
