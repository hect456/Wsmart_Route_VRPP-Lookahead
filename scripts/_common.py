"""Utilities shared by scripts 01 / 02 / 03.

  * puts `src/` on sys.path (installing the package is not required);
  * loads the `.env` file (ORS key) without external dependencies;
  * defines the command-line arguments that override the YAML.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

DEFAULT_CONFIG = ROOT / 'config' / 'instance_491_C7.yaml'


def load_dotenv(path: Path | None = None) -> None:
    """Reads KEY=VALUE from `.env` into the environment (existing values win)."""
    path = path or (ROOT / '.env')
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def base_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description,
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--config', default=str(DEFAULT_CONFIG),
                   help='YAML configuration file of the instance')
    return p


def add_model_arguments(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """One-off overrides of the model parameters (the YAML remains the source)."""
    g = p.add_argument_group('model parameters (override the YAML)')
    g.add_argument('--B', type=float, help='waste density (kg/m3)')
    g.add_argument('--Q', type=float, help='vehicle capacity (kg)')
    g.add_argument('--R', type=float, help='revenue (euro/kg)')
    g.add_argument('--C', type=float, help='travel cost (euro/km)')
    g.add_argument('--OMEGA', type=float, help='fixed cost per vehicle (euro)')
    g.add_argument('--MAX_ROUTES', '--max-routes', dest='MAX_ROUTES', type=int,
                   help='maximum number of routes (k <= MAX_ROUTES); 0 = free fleet, '
                        'sized from the instance so the bound cannot bind')
    g.add_argument('--MIP_GAP', '--mip-gap', dest='MIP_GAP', type=float,
                   help='solver tolerance (0.05 = 5%%)')
    g.add_argument('--TIME_LIMIT', '--time-limit', dest='TIME_LIMIT', type=int,
                   help='solver time limit (seconds)')

    h = p.add_argument_group('lookahead and solver')
    h.add_argument('--days', type=int, help='simulated days')
    h.add_argument('--window', type=int, help='days of the lookahead horizon')
    h.add_argument('--threshold_mg', '--threshold-mg', dest='threshold_mg', type=float,
                   help='%% for MustGo')
    h.add_argument('--threshold_overflow', '--threshold-overflow', dest='threshold_overflow',
                   type=float, help='%% for MustGoLA')
    h.add_argument('--knn', type=int, help='nearest neighbours per node')
    h.add_argument('--seed', type=int, help='Gurobi seed')
    h.add_argument('--mip-focus', dest='mip_focus', type=int,
                   help='Gurobi MIPFocus: 1 = find solutions, 2 = prove optimality, '
                        '3 = move the objective bound (use when the bound has stalled)')
    h.add_argument('--warm-start', dest='warm_start_from',
                   help='result_Day_XX.xlsx of an earlier run on the SAME instance, used as '
                        'the MIP start instead of the greedy walk')
    h.add_argument('--no-maps', dest='generate_maps', action='store_false', default=None,
                   help='do not generate the Folium maps')
    return p


OVERRIDE_KEYS = ('B', 'Q', 'R', 'C', 'OMEGA', 'MAX_ROUTES', 'MIP_GAP', 'TIME_LIMIT',
                 'days', 'window', 'threshold_mg', 'threshold_overflow', 'knn', 'seed',
                 'mip_focus', 'warm_start_from', 'generate_maps')


def overrides(args: argparse.Namespace) -> dict:
    return {k: getattr(args, k) for k in OVERRIDE_KEYS
            if getattr(args, k, None) is not None}
