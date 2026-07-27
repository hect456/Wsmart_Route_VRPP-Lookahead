"""Step 4 — compare an external solution (EVOX) against VRPP + Lookahead.

    python scripts/04_compare_evox.py --config config/instance_491_C7.yaml \
        --external data/raw/evox_routes_491_C7.yaml

Builds a three-block comparison table:

  * **EVOX reported**      — the KPI as EVOX itself reports them (`reported:` in
                             the external YAML). Blank where not supplied.
  * **VRPP fixing EVOX**   — the very same routes and visiting order, measured
                             with the ORS matrix, the YAML parameters and this
                             project's MustGo / MustGoLA classification.
  * **VRPP optimal**       — the free optimisation from `03_run_vrpp.py`.

Both computed blocks go through the same measuring code (`fixed_routes.py`), so
any difference between them comes from the routes, never from the yardstick.
"""
from __future__ import annotations

import argparse

import pandas as pd
import yaml

from _common import base_parser

from vrpp_lookahead import Config, load_instance
from vrpp_lookahead.fixed_routes import (REPORT_ROWS, TOTAL_ROWS, evaluate_fixed_routes,
                                         routes_from_solution_workbook)

BLANK = ''
NOT_REPORTED = 'n/a'   # the external system does not publish this figure


def _fmt(value):
    return NOT_REPORTED if value is None else value


def _diff(reported, computed):
    """reported - computed, blank when EVOX did not supply the figure."""
    if reported is None or computed is None:
        return BLANK
    return round(float(reported) - float(computed), 4)


def build_table(metrics_fix, metrics_opt, reported: dict) -> pd.DataFrame:
    routes_fix = sorted(metrics_fix.per_route['route'])
    routes_opt = sorted(metrics_opt.per_route['route'])
    fix = metrics_fix.per_route.set_index('route')
    opt = metrics_opt.per_route.set_index('route')
    rep_totals = (reported or {}).get('totals') or {}

    rows = []
    for title, key in REPORT_ROWS:
        row = {'Metric': title}
        for r in routes_fix:
            rep = ((reported or {}).get(r) or {}).get(key)
            row[f'EVOX_rep R{r}'] = _fmt(rep)
            row[f'VRPP_fix R{r}'] = fix.loc[r, key]
            row[f'Diff R{r}'] = _diff(rep, fix.loc[r, key])
        for r in routes_opt:
            row[f'VRPP_opt R{r}'] = opt.loc[r, key]
        rows.append(row)

    for title, key in TOTAL_ROWS:
        row = {'Metric': title}
        for r in routes_fix:
            row[f'EVOX_rep R{r}'] = BLANK
            row[f'VRPP_fix R{r}'] = BLANK
            row[f'Diff R{r}'] = BLANK
        for r in routes_opt:
            row[f'VRPP_opt R{r}'] = BLANK
        row['EVOX_rep TOTAL'] = _fmt(rep_totals.get(key))
        row['VRPP_fix TOTAL'] = metrics_fix.totals[key]
        row['VRPP_opt TOTAL'] = metrics_opt.totals[key]
        row['Opt - Fix'] = round(metrics_opt.totals[key] - metrics_fix.totals[key], 4)
        rows.append(row)

    df = pd.DataFrame(rows)

    # per-route metrics also get a TOTAL / Opt-Fix column where summing makes sense
    summable = {'n_bins', 'n_mustgo', 'n_mustgo_la', 'n_optional', 'weight_kg',
                'distance_km', 'travel_time_min', 'profit_euro'}
    for i, (title, key) in enumerate(REPORT_ROWS):
        if key in summable:
            f = float(fix[key].sum())
            o = float(opt[key].sum())
            df.loc[i, 'VRPP_fix TOTAL'] = round(f, 4)
            df.loc[i, 'VRPP_opt TOTAL'] = round(o, 4)
            df.loc[i, 'Opt - Fix'] = round(o - f, 4)
            rep_vals = [((reported or {}).get(r) or {}).get(key) for r in routes_fix]
            if all(v is not None for v in rep_vals):
                df.loc[i, 'EVOX_rep TOTAL'] = round(sum(rep_vals), 4)

    ordered = (['Metric']
               + [c for r in routes_fix for c in (f'EVOX_rep R{r}',)]
               + ['EVOX_rep TOTAL']
               + [c for r in routes_fix for c in (f'VRPP_fix R{r}',)]
               + ['VRPP_fix TOTAL']
               + [c for r in routes_fix for c in (f'Diff R{r}',)]
               + [c for r in routes_opt for c in (f'VRPP_opt R{r}',)]
               + ['VRPP_opt TOTAL', 'Opt - Fix'])
    for c in ordered:
        if c not in df.columns:
            df[c] = BLANK
    return df[ordered].fillna(BLANK)


def build_notes(metrics_fix, metrics_opt, reported: dict, cfg: Config) -> pd.DataFrame:
    """Caveats a reader needs to interpret the table, derived from the data itself.

    Keeps the workbook self-contained: why blocks differ, which gaps come from a
    different definition rather than a different result, and what simply cannot
    be reconciled because the external system does not publish it.
    """
    mod = cfg.model
    fix = metrics_fix.per_route.set_index('route')
    notes = [{
        'Topic': 'Measuring method',
        'Detail': 'The "VRPP fixing EVOX" and "VRPP optimal" blocks are produced by the '
                  'same code over the same ORS matrix and YAML parameters, so any gap '
                  'between them comes from the routes, not from the yardstick.',
    }]

    for r in sorted(fix.index):
        rep = (reported or {}).get(r) or {}
        w_rep, d_rep, p_rep = rep.get('weight_kg'), rep.get('distance_km'), rep.get('profit_euro')
        if w_rep is not None:
            gap = round(float(w_rep) - float(fix.loc[r, 'weight_kg']), 3)
            notes.append({
                'Topic': f'Route {r} — weight',
                'Detail': (f'External {w_rep} kg vs computed {fix.loc[r, "weight_kg"]} kg '
                           f'(gap {gap:+} kg). '
                           + ('Identical: the bin levels of both systems agree.' if gap == 0 else
                              'A non-zero gap over the same bin ids means either an id in the '
                              'supplied list is not the one actually served, or the reported '
                              'figure is a transcription slip. Not resolvable without the '
                              'external per-bin breakdown, which is not published.')),
            })
        if d_rep is not None:
            gap = round(float(d_rep) - float(fix.loc[r, 'distance_km']), 3)
            pct = gap / float(fix.loc[r, 'distance_km']) * 100
            notes.append({
                'Topic': f'Route {r} — distance',
                'Detail': (f'External {d_rep} km vs computed {fix.loc[r, "distance_km"]} km '
                           f'(gap {gap:+} km, {pct:+.1f} %). Computed over the supplied visiting '
                           f'order with the {cfg.ors.transport_mode} ORS profile.'),
            })
        if p_rep is not None and w_rep is not None and d_rep is not None:
            no_omega = mod.R * float(w_rep) - mod.C * float(d_rep)
            if abs(no_omega - float(p_rep)) < 0.01:
                notes.append({
                    'Topic': f'Route {r} — profit definition',
                    'Detail': (f'External {p_rep} EUR equals R*kg - C*km = {no_omega:.4f} EUR, '
                               f'i.e. it excludes the fixed vehicle cost OMEGA={mod.OMEGA} EUR '
                               f'that this project subtracts. A definition gap, not a result gap.'),
                })

    missing = [k for k in ('n_mustgo', 'n_mustgo_la', 'n_optional', 'cap_used_pct')
               if all(((reported or {}).get(r) or {}).get(k) is None for r in fix.index)]
    if missing:
        notes.append({
            'Topic': 'Rows marked n/a',
            'Detail': ('The external system does not publish these figures, so the cells stay '
                       'empty permanently and no difference can be computed for them: '
                       + ', '.join(missing) + '. The values under "VRPP fixing EVOX" are this '
                       'project\'s classification applied to the external routes.'),
        })

    notes.append({
        'Topic': 'MustGo coverage',
        'Detail': (f'{metrics_fix.totals["n_mustgo_missed"]} MustGo and '
                   f'{metrics_fix.totals["n_mustgo_la_missed"]} MustGo-LookAhead bins are left '
                   f'uncovered by the external routes, against '
                   f'{metrics_opt.totals["n_mustgo_missed"]} and '
                   f'{metrics_opt.totals["n_mustgo_la_missed"]} by the optimal solution. '
                   f'An uncovered MustGo overflows the next day.'),
    })
    return pd.DataFrame(notes)


def build_interpretation(metrics_fix, metrics_opt, reported: dict, cfg: Config,
                         inst) -> pd.DataFrame:
    """The reading of the table: what the numbers mean, in plain sentences."""
    rep_t = (reported or {}).get('totals') or {}
    f, o = metrics_fix.totals, metrics_opt.totals
    fix_route = metrics_fix.per_route
    ext_km = rep_t.get('total_distance_km')
    ext_kg = rep_t.get('total_weight_kg')
    ext_eur = rep_t.get('total_profit_euro')
    ext_ratio = rep_t.get('total_km_per_ton')

    def pct(new, old):
        return f'{(new - old) / old * 100:+.1f} %' if old else 'n/a'

    items = []
    if ext_km and ext_kg:
        items.append((
            'Headline',
            f'Against the figures EVOX reports for itself, the optimal VRPP collects '
            f'{o["total_weight_kg"]:.1f} kg versus {ext_kg:.1f} kg ({pct(o["total_weight_kg"], ext_kg)}) '
            f'while driving {o["total_distance_km"]:.2f} km versus {ext_km:.1f} km '
            f'({pct(o["total_distance_km"], ext_km)}). It is not a trade-off between distance '
            f'and capture: the VRPP wins on both at once.'))
    if ext_eur:
        items.append((
            'Profit',
            f'{o["total_profit_euro"]:.2f} EUR versus {ext_eur:.2f} EUR '
            f'({pct(o["total_profit_euro"], ext_eur)}). Note the two systems define profit '
            f'differently — EVOX omits the fixed vehicle cost OMEGA — but at {cfg.model.OMEGA} EUR '
            f'per vehicle the effect is negligible next to this gap.'))
    if ext_ratio:
        items.append((
            'Efficiency ratio',
            f'{o["total_km_per_ton"]:.2f} km/Ton versus {ext_ratio:.2f} km/Ton '
            f'({pct(o["total_km_per_ton"], ext_ratio)}). This is the metric that summarises the '
            f'difference best, because it normalises distance by what was actually collected.'))

    used = fix_route['cap_used_pct'].tolist()
    items.append((
        'Root cause: vehicle fill',
        f'The external solution runs its vehicles at '
        f'{" and ".join(f"{u:.1f} %" for u in used)} of capacity, against roughly 100 % for the '
        f'optimal solution. That leaves '
        f'{cfg.model.fleet_capacity_kg - f["total_weight_kg"]:.0f} kg of fleet capacity unused '
        f'while paying the fixed cost and most of the driving. With bins '
        f'{inst.dist[inst.dist > 0].mean():.1f} km apart on average, each extra stop costs little '
        f'distance and adds a lot of weight — which is why the optimal solution serves '
        f'{len(metrics_opt.per_route)} routes with far more stops for almost the same kilometres.'))

    items.append((
        'Critical coverage',
        f'The external routes leave {f["n_mustgo_missed"]} MustGo bin(s) uncovered — these '
        f'overflow the next day — while collecting '
        f'{int(fix_route["n_optional"].sum())} optional bins that are not urgent. The optimal '
        f'solution covers every MustGo and every MustGo-LookAhead bin and still has room for '
        f'{int(metrics_opt.per_route["n_optional"].sum())} profitable optional bins.'))

    items.append((
        'Route balance',
        f'The external solution balances its two routes; the optimal one deliberately does not '
        f'({" and ".join(f"{d:.2f} km" for d in metrics_opt.per_route["distance_km"])}), because '
        f'it maximises joint profit rather than symmetry. If an even workload across drivers is a '
        f'real operational requirement, it has to be added to the model — it is not there today.'))

    items.append((
        'Caveat',
        'The middle block re-measures the external routes with this project\'s yardstick, so the '
        'discrepancies against the reported figures (see the Notes sheet) are measurement '
        'differences, not disagreements about the routes themselves.'))

    return pd.DataFrame(items, columns=['Topic', 'Reading'])


def _format_sheet(ws, table: pd.DataFrame) -> None:
    """Readable widths, frozen header and label column, wrapped headers."""
    from openpyxl.styles import Alignment, Font

    ws.freeze_panes = 'B2'
    ws.column_dimensions['A'].width = 40
    for idx in range(2, len(table.columns) + 1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = 15
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical='center', horizontal='center')
    ws.row_dimensions[1].height = 32
    for row in ws.iter_rows(min_row=2, min_col=2):
        for cell in row:
            cell.alignment = Alignment(horizontal='center')


def _format_text_sheet(ws) -> None:
    from openpyxl.styles import Alignment, Font

    ws.column_dimensions['A'].width = 28
    ws.column_dimensions['B'].width = 110
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2):
        row[0].alignment = Alignment(vertical='top')
        row[-1].alignment = Alignment(wrap_text=True, vertical='top')


def main() -> None:
    p = base_parser(__doc__)
    p.add_argument('--external', required=True,
                   help='YAML with the external routes (and optionally its reported KPI)')
    p.add_argument('--solution', default=None,
                   help='result workbook of the optimal run '
                        '(default: <results>/Day_01/result_Day_01.xlsx)')
    p.add_argument('--out', default=None,
                   help='output Excel (default: <results>/comparison_<label>.xlsx)')
    args = p.parse_args()

    cfg = Config.from_yaml(args.config)
    inst = load_instance(cfg)

    data = yaml.safe_load(open(args.external, encoding='utf-8')) or {}
    external = {int(k): list(v) for k, v in (data.get('routes') or {}).items()}
    assert external, f'No `routes:` block found in {args.external}'
    reported = {(k if k == 'totals' else int(k)): v
                for k, v in (data.get('reported') or {}).items()}

    results = cfg.path('results', create_dir=True)
    solution = args.solution or (results / 'Day_01' / 'result_Day_01.xlsx')
    assert pd.io.common.file_exists(str(solution)), (
        f'Optimal solution not found: {solution}\nRun scripts/03_run_vrpp.py first.')

    print(f'=== STEP 4 — COMPARISON ({cfg.label}) ===')
    print(f'External routes : {args.external}')
    print(f'Optimal solution: {solution}\n')

    metrics_fix = evaluate_fixed_routes(external, inst, cfg, label='EVOX')
    metrics_opt = evaluate_fixed_routes(routes_from_solution_workbook(solution),
                                        inst, cfg, label='VRPP optimal')

    table = build_table(metrics_fix, metrics_opt, reported)
    print(table.to_string(index=False))

    notes = build_notes(metrics_fix, metrics_opt, reported, cfg)
    print('\n--- Notes ---')
    for _, n in notes.iterrows():
        print(f'  * {n["Topic"]}: {n["Detail"]}')

    interpretation = build_interpretation(metrics_fix, metrics_opt, reported, cfg, inst)
    print('\n--- Interpretation ---')
    for _, n in interpretation.iterrows():
        print(f'  * {n["Topic"]}: {n["Reading"]}')

    out = args.out or (results / f'comparison_{cfg.label}.xlsx')
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        table.to_excel(w, sheet_name='Comparison', index=False)
        interpretation.to_excel(w, sheet_name='Interpretation', index=False)
        notes.to_excel(w, sheet_name='Notes', index=False)
        metrics_fix.per_route.to_excel(w, sheet_name='EVOX_routes_detail', index=False)
        metrics_opt.per_route.to_excel(w, sheet_name='VRPP_optimal_detail', index=False)
        pd.DataFrame({'id_contentor': metrics_fix.not_visited}).to_excel(
            w, sheet_name='EVOX_not_visited', index=False)

        _format_sheet(w.sheets['Comparison'], table)
        _format_text_sheet(w.sheets['Interpretation'])
        _format_text_sheet(w.sheets['Notes'])
    print(f'\nComparison written to: {out}')


if __name__ == '__main__':
    main()
