"""Step 3 — Simulation loop (lookahead -> VRPP -> state update).

State of the following day:
    collected -> 0 kg
    remaining -> min(CAP_CONT_i, level_i + ai_kg_i)
"""
from __future__ import annotations

import pandas as pd

from .config import Config
from .instance import Instance
from .lookahead import lookahead
from .reporting import export_excel, export_summary, plot_routes
from .vrpp import solve_vrpp


def simulate(inst: Instance, cfg: Config) -> list:
    base_folder = cfg.path('results', create_dir=True)
    base_folder.mkdir(parents=True, exist_ok=True)
    state = inst.initial_state()
    kpis = []

    for day in range(1, cfg.lookahead.days + 1):
        label = f'Day_{day:02d}'
        folder = base_folder / label

        print(f'\n{"="*64}\n  {label} — LOOKAHEAD (window={cfg.lookahead.window} days)\n{"="*64}')
        la = lookahead(state, inst, cfg)

        print(f'\n{"="*64}\n  {label} — VRPP\n{"="*64}')
        collected: set = set()
        if la.all_ids:
            sol = solve_vrpp(state, la, inst, cfg, label)
            if sol is not None:
                if cfg.solver.generate_maps:
                    plot_routes(sol, inst, folder)
                export_excel(sol, inst, cfg, folder)
                kpis.append(sol.kpi)
                collected = set(sol.collected)
        else:
            print('  No MG bin — no collection today.')

        levels = state['level_kg'].to_numpy(dtype=float).copy()
        for pos, bin_id in enumerate(state['id_contentor']):
            if bin_id in collected:
                levels[pos] = 0.0
            else:
                levels[pos] = min(inst.cap.get(bin_id, float('inf')),
                                  levels[pos] + inst.ai_kg.get(bin_id, 0.0))
        state['level_kg'] = levels

    print('\nSimulation finished.')
    return kpis


def run(cfg: Config, diagnostics: pd.DataFrame | None = None) -> list:
    """Loads the instance, simulates it and writes the consolidated summary."""
    from .instance import diagnose, instance_summary, load_instance

    cfg.summary()
    print()
    inst = load_instance(cfg)
    instance_summary(inst)
    print()
    diag = diagnostics if diagnostics is not None else diagnose(inst, cfg)
    kpis = simulate(inst, cfg)
    export_summary(kpis, cfg, diag)
    return kpis
