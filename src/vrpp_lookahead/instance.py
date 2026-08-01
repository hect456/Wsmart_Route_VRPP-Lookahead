"""Step 2 — VRPP instance: building and loading.

The instance workbook always has 4 sheets (format shared by every scenario):

    bins             id_contentor | Si | ai | Vol_cont | Vol_kg | Ncont
    LatLong          id_contentor | Latitude | Longitude          (includes the depot, id=0)
    distance_matrix  N x N matrix in km   (index/columns = ids, depot in the 1st position)
    time_matrix      N x N matrix in min

Derived quantities (defined ONCE for the whole project):

    CAP_CONT_i = B * Ncont_i * Vol_cont_i     capacity of the point (kg)
    Si_kg_i    = Vol_kg_i                     current level (kg, read from the Excel file)
    ai_kg_i    = ai_i / 100 * CAP_CONT_i      daily accumulation (kg)

The `id_contentor | Si | ai | Vol_cont | Vol_kg | Ncont` column names come from the
raw Excel files supplied by the user and are therefore kept in Portuguese.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config

# Sheet names written by this module, followed by the legacy names still found in
# workbooks generated before the project was translated (read-only compatibility).
INSTANCE_SHEETS = {
    'bins': ('bins', 'contentores'),
    'LatLong': ('LatLong',),
    'distance_matrix': ('distance_matrix', 'matrizdistancias'),
    'time_matrix': ('time_matrix', 'matrizmin'),
}
ORS_DIST_SHEETS = ('distance_km', 'distancia_km')
ORS_DUR_SHEETS = ('duration_min', 'duracion_min')

BIN_COLUMNS = ('id_contentor', 'ai', 'Vol_cont', 'Vol_kg', 'Ncont')


def _resolve_sheet(available: list, candidates: tuple, where: str) -> str:
    """First candidate sheet present in the workbook (English name, then legacy)."""
    for name in candidates:
        if name in available:
            return name
    raise AssertionError(f'None of the sheets {candidates} found in {where} ({available})')


# ══════════════════════════════════════════════════════════════════
# 2.1 — Building the instance workbook
# ══════════════════════════════════════════════════════════════════
def build_instance(cfg: Config, dist_sheet: str | None = None,
                   dur_sheet: str | None = None) -> Path:
    """Combines attributes + coordinates + ORS matrix into a 4-sheet workbook.

    The node order is the one of the ORS matrix (depot in the first position),
    which guarantees `distance_matrix.index[1:] == bins.id_contentor`.
    """
    p_attr = cfg.path('attributes')
    p_coord = cfg.path('coordinates')
    p_ors = cfg.path('ors_matrix')
    destination = cfg.path('instance', create_dir=True)

    # One workbook may hold several scenarios as separate sheets (a re-measured
    # fill level, a different day). `paths.attributes_sheet` picks which one this
    # instance is built from; 0 keeps the historical behaviour of reading the first.
    sheet = cfg.paths.attributes_sheet
    attr = pd.read_excel(p_attr, sheet_name=sheet)
    attr.columns = attr.columns.astype(str).str.strip()
    if 'ID_bin' in attr.columns and 'id_contentor' not in attr.columns:
        attr = attr.rename(columns={'ID_bin': 'id_contentor'})
    for col in BIN_COLUMNS:
        assert col in attr.columns, f'Column "{col}" missing in {p_attr.name}'
    attr['id_contentor'] = attr['id_contentor'].astype(int)

    coord = pd.read_excel(p_coord)
    coord.columns = coord.columns.astype(str).str.strip()
    if 'ID_bin' in coord.columns:
        coord = coord.rename(columns={'ID_bin': 'id_contentor'})
    coord = coord[['id_contentor', 'Latitude', 'Longitude']].copy()
    coord['id_contentor'] = coord['id_contentor'].astype(int)

    ors_sheets = pd.ExcelFile(p_ors).sheet_names
    dist_sheet = dist_sheet or _resolve_sheet(ors_sheets, ORS_DIST_SHEETS, p_ors.name)
    dur_sheet = dur_sheet or _resolve_sheet(ors_sheets, ORS_DUR_SHEETS, p_ors.name)

    dist = pd.read_excel(p_ors, sheet_name=dist_sheet, index_col=0)
    dur = pd.read_excel(p_ors, sheet_name=dur_sheet, index_col=0)
    for df in (dist, dur):
        df.index = df.index.astype(int)
        df.columns = df.columns.astype(int)

    ids = list(dist.index)
    assert ids[0] == 0, 'The depot (id=0) must be the first node of the ORS matrix'
    assert list(dist.columns) == ids, 'ORS matrix: index and columns in different orders'
    assert list(dur.index) == ids and list(dur.columns) == ids, \
        f'{dur_sheet} in a different order from {dist_sheet}'

    bin_ids = ids[1:]
    missing_attr = set(bin_ids) - set(attr['id_contentor'])
    assert not missing_attr, f'{len(missing_attr)} ids without attributes: {sorted(missing_attr)[:10]}'
    missing_coord = set(ids) - set(coord['id_contentor'])
    assert not missing_coord, f'{len(missing_coord)} ids without coordinates: {sorted(missing_coord)[:10]}'

    bins = (attr.set_index('id_contentor').loc[bin_ids].reset_index())
    latlong = (coord.drop_duplicates('id_contentor').set_index('id_contentor')
               .loc[ids].reset_index())

    nan_dist = int(np.sum(~np.isfinite(dist.to_numpy(dtype=float))))
    if nan_dist:
        print(f'  WARNING: {nan_dist} NaN cells in the distance matrix — '
              f'step 3 will reject the instance.')

    with pd.ExcelWriter(destination, engine='openpyxl') as w:
        bins.to_excel(w, sheet_name='bins', index=False)
        latlong.to_excel(w, sheet_name='LatLong', index=False)
        dist.to_excel(w, sheet_name='distance_matrix')
        dur.to_excel(w, sheet_name='time_matrix')

    print(f'Instance created: {destination}')
    print(f'  Nodes      : {len(ids)} (depot + {len(bin_ids)} bins)')
    print(f'  Attributes : {p_attr.name}  (sheet {sheet!r})')
    print(f'  Coordinates: {p_coord.name}')
    print(f'  ORS matrix : {p_ors.name}  (sheets {dist_sheet} / {dur_sheet})')
    return destination


# ══════════════════════════════════════════════════════════════════
# 2.2 — Loading + validation
# ══════════════════════════════════════════════════════════════════
@dataclass
class Instance:
    bins: pd.DataFrame          # one row per collection point (depot excluded)
    ids: list                   # node order; ids[0] == 0 == depot
    dist: np.ndarray            # km
    tmin: np.ndarray            # min
    latlon: dict
    cap: dict                   # id -> CAP_CONT (kg)
    ai_kg: dict                 # id -> daily accumulation (kg)
    ai_pct: dict                # id -> daily rate (%)
    label: str = ''
    _pos: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._pos = {c: p for p, c in enumerate(self.ids)}

    @property
    def n(self) -> int:
        return len(self.bins)

    def sub(self, selected_ids: list):
        """Sub-matrices (dist, tmin) in the order of `selected_ids`."""
        idx = [self._pos[i] for i in selected_ids]
        block = np.ix_(idx, idx)
        return self.dist[block], self.tmin[block]

    def initial_state(self) -> pd.DataFrame:
        return self.bins[['id_contentor', 'Si_kg']].rename(columns={'Si_kg': 'level_kg'}).copy()


def load_instance(cfg: Config) -> Instance:
    path = cfg.path('instance')
    assert path.exists(), (f'Instance not found: {path}\n'
                           f'Run scripts/02_build_instance.py first')

    available = pd.ExcelFile(path).sheet_names
    sheet = {key: _resolve_sheet(available, names, path.name)
             for key, names in INSTANCE_SHEETS.items()}

    bins = pd.read_excel(path, sheet_name=sheet['bins'])
    latlong = pd.read_excel(path, sheet_name=sheet['LatLong'])
    dists = pd.read_excel(path, sheet_name=sheet['distance_matrix'], index_col=0)
    mins = pd.read_excel(path, sheet_name=sheet['time_matrix'], index_col=0)

    for col in BIN_COLUMNS:
        assert col in bins.columns, f'Column "{col}" missing in the bins sheet'

    bins['id_contentor'] = bins['id_contentor'].astype(int)
    latlong['id_contentor'] = latlong['id_contentor'].astype(int)
    for df in (dists, mins):
        df.index = df.index.astype(int)
        df.columns = df.columns.astype(int)

    dup = int(bins['id_contentor'].duplicated().sum())
    assert dup == 0, f'{dup} duplicated id_contentor in the bins sheet'

    ids = list(dists.index)
    assert ids[0] == 0, 'The depot (id=0) must be the first row of the distance matrix'
    assert list(dists.columns) == ids, 'distance matrix: index and columns in different orders'
    assert list(mins.index) == ids and list(mins.columns) == ids, \
        'time matrix with indices different from the distance matrix'

    missing_ll = set(ids) - set(latlong['id_contentor'])
    assert not missing_ll, f'{len(missing_ll)} nodes without coordinates: {sorted(missing_ll)[:5]}'
    inconsistent = (set(ids) - {0}) ^ set(bins['id_contentor'])
    assert not inconsistent, \
        f'{len(inconsistent)} inconsistent ids between the matrix and the bins sheet'

    dist = dists.to_numpy(dtype=float)
    n_nan = int(np.sum(~np.isfinite(dist)))
    assert n_nan == 0, f'distance matrix with {n_nan} NaN/Inf entries'

    raw = mins.to_numpy(dtype=float)
    tmin = np.where(np.isfinite(raw), raw, 0.0)

    B = cfg.model.B
    bins['CAP_CONT'] = B * bins['Ncont'] * bins['Vol_cont'].astype(float)
    bins['Si_kg'] = bins['Vol_kg'].astype(float)
    bins['ai_kg'] = (bins['ai'] / 100.0) * bins['CAP_CONT']
    assert (bins['CAP_CONT'] > 0).all(), 'CAP_CONT <= 0 (Ncont or Vol_cont are zero)'

    # align the bin order with the matrices -> predictable indexing
    bins = bins.set_index('id_contentor').loc[ids[1:]].reset_index()

    # A free fleet (`MAX_ROUTES: null`) is sized from the data, so it can only be
    # resolved once the instance is on the table. Done here so that every entry
    # point — solve, diagnose, comparison — sees the same resolved value.
    resolve_max_routes(cfg, bins, dist)

    return Instance(
        bins=bins,
        ids=ids,
        dist=dist,
        tmin=tmin,
        latlon=latlong.set_index('id_contentor')[['Latitude', 'Longitude']].to_dict('index'),
        cap=bins.set_index('id_contentor')['CAP_CONT'].to_dict(),
        ai_kg=bins.set_index('id_contentor')['ai_kg'].to_dict(),
        ai_pct=bins.set_index('id_contentor')['ai'].to_dict(),
        label=cfg.label,
    )


def _nearest_neighbour_km(dist: np.ndarray) -> float:
    """Length of a nearest-neighbour tour from the depot over every node (km).

    Only used to size a free fleet. It is a crude tour — typically 25 % longer
    than the optimum — which is exactly the right bias here: over-estimating the
    driving over-estimates the vehicles needed, and the bound must not bind.
    """
    n = len(dist)
    unvisited = set(range(1, n))
    cur, total = 0, 0.0
    while unvisited:
        nxt = min(unvisited, key=lambda j: dist[cur][j])
        total += float(dist[cur][nxt])
        unvisited.discard(nxt)
        cur = nxt
    return total + float(dist[cur][0])


def resolve_max_routes(cfg: Config, bins: pd.DataFrame, dist: np.ndarray) -> int:
    """Replace `MAX_ROUTES: null` by a fleet that cannot cap the solution.

    "Free" cannot mean "unbounded": `k` is an integer variable and needs an upper
    bound. It means the bound must be loose enough that removing it would change
    nothing — i.e. enough vehicles to collect *every* bin of the instance. Two
    resources can force an extra vehicle, so the bound is the larger of:

      * capacity — ceil(total kg / Q);
      * working day, when `shift.enforce` is on — ceil(total crew minutes /
        shift), the minutes being a nearest-neighbour tour of all bins at the
        shift speed plus one service stop per bin.

    Without the second term a free fleet would silently collapse to the capacity
    bound, which is wrong the moment the shift is what limits a route: two
    vehicles hold all the waste of this instance, but they cannot be on the road
    long enough to reach it.
    """
    mod, sh = cfg.model, cfg.shift
    if mod.MAX_ROUTES is not None:
        return mod.MAX_ROUTES

    total_kg = float(bins['Si_kg'].sum())
    k_cap = max(1, math.ceil(total_kg / mod.Q))

    k_shift = 1
    if sh.enforce:
        tour_km = _nearest_neighbour_km(dist)
        minutes = sh.route_min(tour_km, len(bins))
        k_shift = max(1, math.ceil(minutes / sh.max_shift_min))

    mod.MAX_ROUTES = max(k_cap, k_shift)
    mod.MAX_ROUTES_auto = True
    print(f'MAX_ROUTES  : free -> {mod.MAX_ROUTES} '
          f'(capacity needs {k_cap}, working day needs {k_shift}) '
          f'— a bound that cannot cap the solution, not a fleet decision')
    return mod.MAX_ROUTES


# ══════════════════════════════════════════════════════════════════
# 2.3 — Diagnostics (before optimising)
# ══════════════════════════════════════════════════════════════════
def instance_summary(inst: Instance) -> None:
    c = inst.bins
    print(f'Bins        : {inst.n}   (total nodes: {len(inst.ids)} = depot + {inst.n})')
    print(f'Depot       : Lat={inst.latlon[0]["Latitude"]:.6f}  Lon={inst.latlon[0]["Longitude"]:.6f}')
    print(f'Distances   : mean {inst.dist[inst.dist > 0].mean():.2f} km | max {inst.dist.max():.2f} km')
    print(f'CAP_CONT    : min {c["CAP_CONT"].min():.1f} | mean {c["CAP_CONT"].mean():.1f} '
          f'| max {c["CAP_CONT"].max():.1f} kg | TOTAL {c["CAP_CONT"].sum():.2f} kg')
    print(f'Si_kg       : min {c["Si_kg"].min():.2f} | mean {c["Si_kg"].mean():.2f} '
          f'| max {c["Si_kg"].max():.2f} kg | TOTAL {c["Si_kg"].sum():.2f} kg')
    print(f'ai_kg       : min {c["ai_kg"].min():.2f} | mean {c["ai_kg"].mean():.2f} '
          f'| max {c["ai_kg"].max():.2f} kg/day | TOTAL {c["ai_kg"].sum():.2f} kg/day')


def diagnose(inst: Instance, cfg: Config) -> pd.DataFrame:
    """Classifies the points on day 1 and checks whether the fleet covers the MustGo."""
    la, mod = cfg.lookahead, cfg.model
    d = inst.bins.copy()
    thr = la.threshold_mg / 100.0 * d['CAP_CONT']
    ovf = la.threshold_overflow / 100.0 * d['CAP_CONT']

    d['mg'] = (d['Si_kg'] + d['ai_kg']) >= thr
    if la.window >= 2:
        future = np.any([(d['Si_kg'] + d['ai_kg'] * k) >= ovf
                         for k in range(2, la.window + 1)], axis=0)
    else:
        future = np.zeros(len(d), dtype=bool)
    d['mgla'] = ~d['mg'] & future
    d['empty'] = ~d['mg'] & ~d['mgla'] & (d['Si_kg'] / d['CAP_CONT'] * 100 < la.near_empty_level_pct)
    d['opt'] = ~d['mg'] & ~d['mgla'] & ~d['empty']

    total = float(d['Si_kg'].sum())
    summary = pd.DataFrame([{
        'Group': name,
        'N_points': int(mask.sum()),
        'Si_kg': round(float(d.loc[mask, 'Si_kg'].sum()), 2),
        'Pct_Si_total': round(float(d.loc[mask, 'Si_kg'].sum()) / total * 100, 2) if total else 0.0,
    } for name, mask in (('MustGo', d['mg']), ('MustGoLA', d['mgla']),
                         ('Optional', d['opt']), ('Near empty', d['empty']))])

    mg_weight = float(d.loc[d['mg'], 'Si_kg'].sum())
    min_routes = math.ceil(mg_weight / mod.Q) if mg_weight > 0 else 0

    print(summary.to_string(index=False))
    print(f'\nTotal Si_kg    : {total:.2f} kg  '
          f'({total / d["CAP_CONT"].sum() * 100:.1f}% of total CAP_CONT)')
    print(f'Total CAP_CONT : {d["CAP_CONT"].sum():.2f} kg')
    print(f'MustGo weight  : {mg_weight:.2f} kg  ->  at least {min_routes} route(s) of {mod.Q:g} kg')
    print(f'MAX_ROUTES     : {mod.MAX_ROUTES}  (capacity {mod.fleet_capacity_kg:g} kg)')
    if min_routes > mod.MAX_ROUTES:
        print(f'  !!! MAX_ROUTES too low: some MustGo will be downgraded to Optional. '
              f'Suggestion: MAX_ROUTES={min_routes}')
    else:
        print(f'  OK — {mod.fleet_capacity_kg - mg_weight:.2f} kg of slack over the MustGo')
    return summary
