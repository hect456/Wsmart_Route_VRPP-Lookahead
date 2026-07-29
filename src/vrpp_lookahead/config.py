"""Project configuration — the SINGLE parameterisation point.

The model parameters (B, Q, R, C, OMEGA, MIP_GAP, TIME_LIMIT, MAX_ROUTES) are
edited in the YAML file under `config/` or on the command line:

    python scripts/03_run_vrpp.py --config config/instance_491_C7.yaml --Q 4000 --max-routes 3
"""
from __future__ import annotations

import os
import platform
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# Model parameters the user may edit (YAML or CLI).
MODEL_PARAMETERS = ('B', 'Q', 'R', 'C', 'OMEGA', 'MIP_GAP', 'TIME_LIMIT', 'MAX_ROUTES')


def runtime_environment(cfg: 'Config | None' = None) -> dict:
    """Solver and interpreter versions of the current run.

    Recorded next to the parameters because a MILP stopped at a non-zero gap is
    reproducible only for the same solver version, seed and thread count: within
    the gap, several solutions are equally acceptable and which one is returned
    depends on the search path. The parameters alone do not pin the result down.
    """
    def _version(module: str) -> str:
        try:
            return __import__(module).__version__
        except Exception:
            return 'not installed'

    try:
        import gurobipy
        gurobi = '.'.join(str(p) for p in gurobipy.gurobi.version())
    except Exception:
        gurobi = 'not installed'

    cores = os.cpu_count()
    threads_cfg = cfg.solver.threads if cfg else None
    seed_cfg = cfg.solver.seed if cfg else None

    env = {
        'run_started_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'gurobi_version': gurobi,
        'python_version': sys.version.split()[0],
        'platform': f'{platform.system()} {platform.release()}',
        'cpu_count': cores,
        'threads_configured': threads_cfg,
        'threads_effective': cores if threads_cfg in (0, None) else threads_cfg,
        'seed_configured': seed_cfg,
        'pandas_version': _version('pandas'),
        'numpy_version': _version('numpy'),
        'package_version': __import__('vrpp_lookahead').__version__,
    }

    caveats = []
    if seed_cfg is None:
        caveats.append('solver.seed is null — set it to pin the search path.')
    if threads_cfg in (0, None):
        caveats.append(f'solver.threads is 0 — resolved to {cores} on this machine; '
                       f'a machine with a different core count may return a different '
                       f'solution of equal quality.')
    env['reproducibility'] = caveats or ['seed and thread count are pinned.']
    return env


@dataclass
class Model:
    """Physical and economic parameters of the VRPP."""

    B: float = 16.0          # waste density (kg/m3)
    Q: float = 3500.0        # vehicle capacity (kg)
    R: float = 0.1625        # revenue (euro/kg)
    C: float = 1.0           # travel cost (euro/km)
    OMEGA: float = 0.1       # fixed cost per vehicle (euro)
    MAX_ROUTES: int = 2      # k <= MAX_ROUTES
    MIP_GAP: float = 0.05    # solver tolerance
    TIME_LIMIT: int = 21600  # solver time limit (s)

    @property
    def fleet_capacity_kg(self) -> float:
        return self.MAX_ROUTES * self.Q

    def validate(self) -> None:
        assert self.B > 0, 'B must be > 0'
        assert self.Q > 0, 'Q must be > 0'
        assert self.MAX_ROUTES >= 1, 'MAX_ROUTES must be >= 1'
        assert 0 <= self.MIP_GAP < 1, 'MIP_GAP must be in [0, 1)'
        assert self.TIME_LIMIT > 0, 'TIME_LIMIT must be > 0'


@dataclass
class Shift:
    """Working day: how long a route actually costs a crew.

    The objective function prices distance, never time, so a route that is
    profitable on paper may not fit in a shift. These parameters convert a route
    into hours of work and let that be checked — or, with `enforce: true`,
    imposed on the solver as a hard constraint.

    `speed_kmh` deliberately overrides the ORS travel times. ORS returns
    free-flow driving times for the vehicle profile, which a collection round
    does not achieve: it stops constantly, manoeuvres and restarts. A single
    average round speed is an assumption the operator can state and defend.

    `service_time_min` is the part the model ignored entirely until now, and it
    is the one that matters here: it is what makes a stop cost something. With
    it at zero, visiting 300 bins costs the same as visiting 100.
    """

    speed_kmh: float = 30.0           # average speed of the round (km/h)
    service_time_min: float = 1.5     # time spent at each bin (min)
    max_shift_h: float = 8.0          # shift length imposed when `enforce` is on (h)
    enforce: bool = False             # True = hard constraint in the MILP
    report_h: list = field(default_factory=lambda: [7.0, 8.0])   # reported as fits/exceeds

    @property
    def max_shift_min(self) -> float:
        return self.max_shift_h * 60.0

    def travel_min(self, km: float) -> float:
        """Driving minutes for `km` at the configured average speed."""
        return km / self.speed_kmh * 60.0

    def route_min(self, km: float, n_bins: int) -> float:
        """Total crew minutes: driving + one service stop per bin."""
        return self.travel_min(km) + n_bins * self.service_time_min

    def validate(self) -> None:
        assert self.speed_kmh > 0, 'shift.speed_kmh must be > 0'
        assert self.service_time_min >= 0, 'shift.service_time_min must be >= 0'
        assert self.max_shift_h > 0, 'shift.max_shift_h must be > 0'


@dataclass
class Lookahead:
    """Horizon and thresholds of the MustGo / MustGoLA classification."""

    days: int = 1                       # simulated days
    window: int = 2                     # days of the lookahead horizon
    threshold_mg: float = 100.0         # % : level + ai       >= thr  -> MustGo
    threshold_overflow: float = 100.0   # % : level + ai*k     >= ovf  -> MustGoLA
    near_empty_level_pct: float = 5.0   # diagnostics only: almost empty points

    def validate(self) -> None:
        assert self.days >= 1, 'lookahead.days must be >= 1'
        assert self.window >= 1, 'lookahead.window must be >= 1'


@dataclass
class Solver:
    """Gurobi tuning and arc pre-filtering."""

    knn: int = 25                    # nearest neighbours per node
    seed: int | None = None          # None = Gurobi default
    output_flag: int = 1
    mip_focus: int = 1
    heuristics: float = 0.5
    threads: int = 0
    cuts: int = 3
    presolve: int = 1
    node_method: int = 2
    keep_mustgo_arcs: bool = False   # True = never filter out MustGo-MustGo arcs
    generate_maps: bool = True


@dataclass
class ORS:
    """Parameters of the OpenRouteService Matrix API."""

    transport_mode: str = 'driving-hgv'
    max_routes_per_request: int = 3500
    pause_s: float = 1.6
    manual_block: int | None = None
    excel_sheet: Any = 0
    api_key_env: str = 'ORS_API_KEY'

    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env, '').strip()
        if not key:
            raise RuntimeError(
                f'Environment variable {self.api_key_env} is not set.\n'
                f'Copy .env.example to .env and put your ORS key there '
                f'(free sign-up at https://openrouteservice.org/dev/#/signup).'
            )
        return key


@dataclass
class Paths:
    """File paths (relative to the project root, or absolute)."""

    coordinates: str = ''   # input: ID_bin | Latitude | Longitude
    attributes: str = ''    # input: id_contentor | Si | ai | Vol_cont | Vol_kg | Ncont
    ors_matrix: str = ''    # step 1 output / step 2 input
    instance: str = ''      # step 2 output / step 3 input (4-sheet workbook)
    results: str = ''       # step 3 output


@dataclass
class Config:
    label: str = 'instance'
    description: str = ''
    root: Path = field(default_factory=Path.cwd)
    paths: Paths = field(default_factory=Paths)
    model: Model = field(default_factory=Model)
    shift: Shift = field(default_factory=Shift)
    lookahead: Lookahead = field(default_factory=Lookahead)
    solver: Solver = field(default_factory=Solver)
    ors: ORS = field(default_factory=ORS)

    # ── construction ──────────────────────────────────────────────
    @classmethod
    def from_yaml(cls, path: str | Path) -> 'Config':
        path = Path(path).resolve()
        assert path.exists(), f'Config not found: {path}'
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        root = Path(data.get('root') or path.parent.parent).resolve()
        cfg = cls(
            label=str(data.get('label', 'instance')),
            description=str(data.get('description', '')),
            root=root,
            paths=Paths(**(data.get('paths') or {})),
            model=Model(**(data.get('model') or {})),
            shift=Shift(**(data.get('shift') or {})),
            lookahead=Lookahead(**(data.get('lookahead') or {})),
            solver=Solver(**(data.get('solver') or {})),
            ors=ORS(**(data.get('ors') or {})),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        self.model.validate()
        self.shift.validate()
        self.lookahead.validate()

    # ── paths ─────────────────────────────────────────────────────
    def path(self, name: str, create_dir: bool = False) -> Path:
        value = getattr(self.paths, name, '')
        assert value, f'paths.{name} is not defined in the YAML'
        p = Path(value)
        p = p if p.is_absolute() else (self.root / p)
        p = p.resolve()
        if create_dir:
            (p if p.suffix == '' else p.parent).mkdir(parents=True, exist_ok=True)
        return p

    def apply_overrides(self, overrides: dict[str, Any]) -> 'Config':
        """Override model parameters coming from the command line."""
        for key, value in (overrides or {}).items():
            if value is None:
                continue
            if key in MODEL_PARAMETERS:
                setattr(self.model, key, value)
            elif hasattr(self.shift, key):
                setattr(self.shift, key, value)
            elif hasattr(self.lookahead, key):
                setattr(self.lookahead, key, value)
            elif hasattr(self.solver, key):
                setattr(self.solver, key, value)
            else:
                raise KeyError(f'Unknown parameter: {key}')
        self.validate()
        return self

    def as_dict(self) -> dict:
        d = asdict(self)
        d['root'] = str(self.root)
        return d

    # ── presentation ──────────────────────────────────────────────
    def summary(self) -> None:
        m, la, sv, sh = self.model, self.lookahead, self.solver, self.shift
        print(f'Instance    : {self.label}   {self.description}')
        print(f'Root        : {self.root}')
        print(f'B={m.B:g} kg/m3 | Q={m.Q:g} kg | MAX_ROUTES={m.MAX_ROUTES} '
              f'-> fleet {m.fleet_capacity_kg:g} kg')
        print(f'R={m.R:g} eur/kg | C={m.C:g} eur/km | OMEGA={m.OMEGA:g} eur/vehicle')
        print(f'MIP_GAP={m.MIP_GAP*100:g}% | TIME_LIMIT={m.TIME_LIMIT}s ({m.TIME_LIMIT/3600:g}h)')
        print(f'Days={la.days} | Window={la.window} | THR_MG={la.threshold_mg:g}% '
              f'| THR_OVF={la.threshold_overflow:g}% | KNN={sv.knn}')
        print(f'Shift: {sh.speed_kmh:g} km/h | {sh.service_time_min:g} min/bin | '
              + (f'max {sh.max_shift_h:g} h ENFORCED as a constraint'
                 if sh.enforce else f'reported only against {sh.report_h} h (not enforced)'))
