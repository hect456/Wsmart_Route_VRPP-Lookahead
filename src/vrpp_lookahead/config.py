"""Project configuration — the SINGLE parameterisation point.

The model parameters (B, Q, R, C, OMEGA, MIP_GAP, TIME_LIMIT, MAX_ROUTES) are
edited in the YAML file under `config/` or on the command line:

    python scripts/03_run_vrpp.py --config config/instance_491_C7.yaml --Q 4000 --max-routes 3
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Model parameters the user may edit (YAML or CLI).
MODEL_PARAMETERS = ('B', 'Q', 'R', 'C', 'OMEGA', 'MIP_GAP', 'TIME_LIMIT', 'MAX_ROUTES')


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
            lookahead=Lookahead(**(data.get('lookahead') or {})),
            solver=Solver(**(data.get('solver') or {})),
            ors=ORS(**(data.get('ors') or {})),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        self.model.validate()
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
        m, la, sv = self.model, self.lookahead, self.solver
        print(f'Instance    : {self.label}   {self.description}')
        print(f'Root        : {self.root}')
        print(f'B={m.B:g} kg/m3 | Q={m.Q:g} kg | MAX_ROUTES={m.MAX_ROUTES} '
              f'-> fleet {m.fleet_capacity_kg:g} kg')
        print(f'R={m.R:g} eur/kg | C={m.C:g} eur/km | OMEGA={m.OMEGA:g} eur/vehicle')
        print(f'MIP_GAP={m.MIP_GAP*100:g}% | TIME_LIMIT={m.TIME_LIMIT}s ({m.TIME_LIMIT/3600:g}h)')
        print(f'Days={la.days} | Window={la.window} | THR_MG={la.threshold_mg:g}% '
              f'| THR_OVF={la.threshold_overflow:g}% | KNN={sv.knn}')
