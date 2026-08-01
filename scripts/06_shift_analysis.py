"""Step 6 — compare solutions on equal terms once the working day is counted.

    python scripts/06_shift_analysis.py --config config/instance_491_C7_shift8h.yaml \
        --external "EVOX=data/raw/evox_routes_491_C7.yaml" \
        --solution "VRPP no shift=results/491_C7_gap1/Day_01/result_Day_01.xlsx" \
        --solution "VRPP 8 h shift=results/491_C7_shift8h/Day_01/result_Day_01.xlsx"

Why this is a separate report from `04_compare_evox.py`:

Step 4 answers "who collects more". That question stops being well posed the
moment routes differ in how long the crew works — a solution that drives two
extra hours *should* collect more, and saying so proves nothing. This report
divides by the hours actually worked, so the comparison survives the objection.

It also names, for each solution, which constraint it is actually up against:
capacity, the shift, or neither. A solution bound by neither is not optimising
against the fleet it has — it is following a policy imposed from outside the
model, and that is worth stating plainly rather than reading as inefficiency.
"""
from __future__ import annotations

import argparse

import pandas as pd
import yaml

from _common import base_parser

from vrpp_lookahead import Config, load_instance
from vrpp_lookahead.fixed_routes import evaluate_fixed_routes, routes_from_solution_workbook

# A constraint counts as binding within this much of its limit. Loose enough to
# absorb the solver's own tolerance, tight enough that "neither binds" means it.
BINDING_PCT = 2.0


def _labelled(spec: str, default_label: str) -> tuple:
    """`"Name=path"` -> (Name, path); a bare path keeps the default label."""
    if '=' in spec:
        label, path = spec.split('=', 1)
        return label.strip(), path.strip()
    return default_label, spec


def _binds(per_route: pd.DataFrame, cfg: Config) -> str:
    """Which constraint the solution is actually up against."""
    cap = float(per_route['cap_used_pct'].max())
    shift = float(per_route['shift_total_min'].max()) / cfg.shift.max_shift_min * 100.0
    hit = []
    if cap >= 100.0 - BINDING_PCT:
        hit.append('capacity')
    if shift >= 100.0 - BINDING_PCT:
        hit.append('shift')
    if not hit:
        return f'neither (capacity {cap:.1f} %, shift {shift:.1f} % of the limit)'
    return ' and '.join(hit) + f' (capacity {cap:.1f} %, shift {shift:.1f} % of the limit)'


def analyse(solutions: dict, inst, cfg: Config) -> tuple:
    """Per-solution totals normalised by crew hours, plus the per-route detail."""
    rows, detail = [], []
    for label, routes in solutions.items():
        m = evaluate_fixed_routes(routes, inst, cfg, label=label)
        pr = m.per_route
        # Crew hours SUM across routes: two crews working 8 h each cost 16 crew
        # hours. This is the denominator, and it is the one figure in this
        # report that is deliberately a sum rather than a max — the shift limit
        # is per crew, the labour bill is not.
        crew_h = float(pr['shift_total_h'].sum())
        kg = m.totals['total_weight_kg']
        eur = m.totals['total_profit_euro']
        km = m.totals['total_distance_km']
        rows.append({
            'Solution': label,
            'Routes': len(pr),
            'Bins': int(pr['n_bins'].sum()),
            'Weight_kg': round(kg, 1),
            'Distance_km': round(km, 2),
            'Longest_route_h': round(float(pr['shift_total_h'].max()), 3),
            'Crew_hours': round(crew_h, 2),
            'Profit_euro': round(eur, 2),
            'kg_per_crew_hour': round(kg / crew_h, 1) if crew_h else 0.0,
            'euro_per_crew_hour': round(eur / crew_h, 2) if crew_h else 0.0,
            'bins_per_crew_hour': round(int(pr['n_bins'].sum()) / crew_h, 1) if crew_h else 0.0,
            'Binding_constraint': _binds(pr, cfg),
        })
        for _, r in pr.iterrows():
            detail.append({
                'Solution': label, 'Route': int(r['route']), 'Bins': int(r['n_bins']),
                'Weight_kg': r['weight_kg'], 'Cap_used_pct': r['cap_used_pct'],
                'Distance_km': r['distance_km'],
                'Driving_min': r['shift_driving_min'], 'Service_min': r['shift_service_min'],
                'Working_day_h': r['shift_total_h'],
                'Idle_capacity_kg': round(cfg.model.Q - r['weight_kg'], 1),
                'Idle_shift_min': round(cfg.shift.max_shift_min - r['shift_total_min'], 1),
            })
    return pd.DataFrame(rows), pd.DataFrame(detail)


def build_marginal(summary: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """What each extra vehicle actually buys, once its crew hours are counted.

    The objective cannot answer "how many vehicles". It prices a vehicle at
    OMEGA — 0.1 EUR here — and prices the crew's time at nothing at all, so any
    bin whose waste is worth more than the kilometres to reach it makes a new
    vehicle worth sending. Left free, the model will therefore keep adding
    vehicles until it runs out of profitable bins. That is not a fleet
    recommendation; it is the consequence of a cost the model does not carry.

    The decision has to be made outside the objective, and this is the table
    that supports it: sort the runs by fleet size and read what the Nth vehicle
    adds in profit against the ~one shift of crew time it costs. The operator
    compares that euro-per-crew-hour against what an hour of crew actually costs
    them — a number this project does not have and does not invent.

    Only meaningful across runs of the SAME instance and parameters that differ
    in nothing but the fleet cap.
    """
    df = summary.sort_values('Routes').reset_index(drop=True)
    rows = []
    for n in range(len(df)):
        cur = df.loc[n]
        row = {
            'Solution': cur['Solution'],
            'Routes': cur['Routes'],
            'Crew_hours': cur['Crew_hours'],
            'Weight_kg': cur['Weight_kg'],
            'Profit_euro': cur['Profit_euro'],
            'euro_per_crew_hour': cur['euro_per_crew_hour'],
        }
        if n == 0:
            row.update({'Extra_crew_hours': '', 'Extra_weight_kg': '',
                        'Extra_profit_euro': '', 'Marginal_euro_per_crew_hour': '',
                        'Marginal_vs_average': ''})
        else:
            prev = df.loc[n - 1]
            d_h = round(cur['Crew_hours'] - prev['Crew_hours'], 2)
            d_kg = round(cur['Weight_kg'] - prev['Weight_kg'], 1)
            d_eur = round(cur['Profit_euro'] - prev['Profit_euro'], 2)
            marginal = round(d_eur / d_h, 2) if d_h else float('nan')
            row.update({
                'Extra_crew_hours': d_h,
                'Extra_weight_kg': d_kg,
                'Extra_profit_euro': d_eur,
                'Marginal_euro_per_crew_hour': marginal,
                # The comparison that matters: an extra vehicle that earns less
                # per hour than the ones already out is diluting the operation
                # even while it raises the headline total.
                'Marginal_vs_average': ('below the average — dilutes'
                                        if marginal < prev['euro_per_crew_hour']
                                        else 'above the average — improves'),
            })
        rows.append(row)
    return pd.DataFrame(rows)


def build_findings(summary: pd.DataFrame, baseline: str, cfg: Config) -> pd.DataFrame:
    """The readings that only become visible once hours are in the denominator."""
    if baseline not in set(summary['Solution']):
        baseline = summary['Solution'].iloc[0]
    base = summary.set_index('Solution').loc[baseline]
    sh = cfg.shift

    def rel(row, col):
        return (row[col] / base[col] - 1) * 100 if base[col] else float('nan')

    out = [{
        'Topic': 'Method',
        'Reading': (f'Every route is re-measured over the same ORS matrix at '
                    f'{sh.speed_kmh:g} km/h with {sh.service_time_min:g} min per bin. Crew hours '
                    f'are summed across routes, so a solution that works longer pays for it in '
                    f'the denominator. Baseline for the percentages: {baseline}.'),
    }]

    for _, row in summary.iterrows():
        if row['Solution'] == baseline:
            continue
        out.append({
            'Topic': f'{row["Solution"]} vs {baseline}',
            'Reading': (
                f'Raw: {rel(row, "Weight_kg"):+.1f} % weight, {rel(row, "Profit_euro"):+.1f} % '
                f'profit, {rel(row, "Distance_km"):+.1f} % distance — but also '
                f'{rel(row, "Crew_hours"):+.1f} % crew hours, which is the objection a raw '
                f'comparison invites. Per crew hour: {rel(row, "kg_per_crew_hour"):+.1f} % kg '
                f'and {rel(row, "euro_per_crew_hour"):+.1f} % euro. The advantage that survives '
                f'the normalisation is the defensible one.'),
        })

    if summary['Routes'].nunique() > 1:
        best = summary.loc[summary['euro_per_crew_hour'].idxmax()]
        out.append({
            'Topic': 'How many vehicles',
            'Reading': (
                f'The objective cannot answer this. It charges OMEGA={cfg.model.OMEGA:g} EUR for a '
                f'vehicle and nothing at all for the crew\'s time, so a free fleet keeps adding '
                f'vehicles while any profitable bin is left — a consequence of a cost the model '
                f'does not carry, not a fleet recommendation. Per crew hour the best of the runs '
                f'compared here is {best["Solution"]} at {best["euro_per_crew_hour"]:.2f} EUR/h '
                f'with {best["Routes"]} route(s). See the Marginal_vehicle sheet for what each '
                f'extra vehicle adds, and compare it against what an hour of crew actually costs '
                f'you — a figure this project does not have.'),
        })

    for _, row in summary.iterrows():
        out.append({
            'Topic': f'What binds — {row["Solution"]}',
            'Reading': (
                f'{row["Binding_constraint"]}. '
                + ('A solution against neither limit is not being held back by the fleet it has: '
                   'it stops for a reason outside this model, typically a fill-level policy. Its '
                   'idle capacity and idle hours are in the Per_route sheet.'
                   if row['Binding_constraint'].startswith('neither') else
                   'The limit is doing real work here — relaxing it would change the answer.')),
        })

    return pd.DataFrame(out)


def main() -> None:
    p = base_parser(__doc__)
    p.add_argument('--external', action='append', default=[],
                   help='"Label=path.yaml" with externally supplied routes (repeatable)')
    p.add_argument('--solution', action='append', default=[],
                   help='"Label=result_Day_XX.xlsx" of an optimised run (repeatable)')
    p.add_argument('--baseline', default=None,
                   help='label the percentages are computed against (default: the first one)')
    p.add_argument('--out', default=None,
                   help='output Excel (default: <results>/shift_analysis_<label>.xlsx)')
    args = p.parse_args()

    cfg = Config.from_yaml(args.config)
    inst = load_instance(cfg)
    assert args.external or args.solution, 'Supply at least one --external or --solution'

    solutions = {}
    for spec in args.external:
        label, path = _labelled(spec, 'external')
        data = yaml.safe_load(open(path, encoding='utf-8')) or {}
        routes = {int(k): list(v) for k, v in (data.get('routes') or {}).items()}
        assert routes, f'No `routes:` block found in {path}'
        solutions[label] = routes
    for spec in args.solution:
        label, path = _labelled(spec, 'optimised')
        solutions[label] = routes_from_solution_workbook(path)

    print(f'=== STEP 6 — WORKING-DAY ANALYSIS ({cfg.label}) ===')
    print(f'{cfg.shift.speed_kmh:g} km/h | {cfg.shift.service_time_min:g} min per bin | '
          f'shift limit {cfg.shift.max_shift_h:g} h\n')

    summary, detail = analyse(solutions, inst, cfg)
    marginal = build_marginal(summary, cfg)
    findings = build_findings(summary, args.baseline or summary['Solution'].iloc[0], cfg)

    print(summary.to_string(index=False))
    print('\n--- Marginal value of each extra vehicle ---')
    print(marginal.to_string(index=False))
    print('\n--- Per route ---')
    print(detail.to_string(index=False))
    print('\n--- Findings ---')
    for _, f in findings.iterrows():
        print(f'  * {f["Topic"]}: {f["Reading"]}')

    out = args.out or (cfg.path('results', create_dir=True) /
                       f'shift_analysis_{cfg.label}.xlsx')
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        summary.to_excel(w, sheet_name='Summary', index=False)
        marginal.to_excel(w, sheet_name='Marginal_vehicle', index=False)
        detail.to_excel(w, sheet_name='Per_route', index=False)
        findings.to_excel(w, sheet_name='Findings', index=False)
        for name, width in (('Summary', 22), ('Marginal_vehicle', 22),
                            ('Per_route', 16), ('Findings', 30)):
            ws = w.sheets[name]
            ws.column_dimensions['A'].width = width
            if name == 'Findings':
                ws.column_dimensions['B'].width = 110
                for row in ws.iter_rows(min_row=2):
                    from openpyxl.styles import Alignment
                    row[-1].alignment = Alignment(wrap_text=True, vertical='top')
    print(f'\nWritten to: {out}')


if __name__ == '__main__':
    main()
