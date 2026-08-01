"""VRPP + Lookahead — selective waste collection with an ORS distance matrix.

Pipeline:
    1. ors_matrix.build_ors_matrix(cfg)   coordinates -> ORS matrix (km / min)
    2. instance.build_instance(cfg)       attributes + coordinates + matrix -> instance
    3. simulation.run(cfg)                instance -> routes, KPI, maps

Every parameter lives in `config.Config` (YAML file under config/).
"""
from .config import Config, Lookahead, Model, ORS, Paths, Solver, runtime_environment
from .instance import (Instance, build_instance, diagnose, instance_summary, load_instance,
                       resolve_max_routes)
from .lookahead import LookaheadResult, lookahead
from .ors_matrix import build_ors_matrix
from .reporting import export_excel, export_summary, plot_routes
from .simulation import run, simulate
from .vrpp import Solution, solve_vrpp

__version__ = '1.0.0'

__all__ = [
    'Config', 'Model', 'Lookahead', 'Solver', 'ORS', 'Paths', 'runtime_environment',
    'Instance', 'build_instance', 'load_instance', 'instance_summary', 'diagnose',
    'resolve_max_routes',
    'lookahead', 'LookaheadResult',
    'solve_vrpp', 'Solution',
    'plot_routes', 'export_excel', 'export_summary',
    'simulate', 'run',
    'build_ors_matrix',
]
