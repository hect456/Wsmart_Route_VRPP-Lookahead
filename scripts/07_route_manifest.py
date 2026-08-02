"""Step 7 — leg-by-leg manifest of a solution, for auditing the total distance.

    python scripts/07_route_manifest.py --config config/instance_491_C7_simul3107_1rota.yaml

Every other report in this project states a total and asks to be believed. This
one shows the arithmetic: one row per leg, with the pair of node ids, both sets
of coordinates, the distance of that leg as read from the ORS matrix, and the
running total. The reported KPI is then re-derived from the rows and compared
against the figure in the result workbook, so a discrepancy would be visible
rather than hidden inside a sum.

It is also the sheet to hand to whoever wants to check a leg against Google
Maps or an odometer: the coordinates are there, so any single row can be
verified on its own without re-running anything.
"""
from __future__ import annotations

import pandas as pd

from _common import base_parser

from vrpp_lookahead import Config, load_instance
from vrpp_lookahead.fixed_routes import routes_from_solution_workbook


def manifest(routes: dict, inst, cfg: Config) -> tuple:
    """One row per leg (depot -> ... -> depot) for every route."""
    pos = {b: i for i, b in enumerate(inst.ids)}
    level = inst.bins.set_index('id_contentor')['Si_kg'].to_dict()
    sh = cfg.shift

    rows, totals = [], []
    for nr in sorted(routes):
        seq = [0] + list(routes[nr]) + [0]          # depot at both ends
        km_acc = kg_acc = min_acc = 0.0
        for step in range(1, len(seq)):
            a, b = seq[step - 1], seq[step]
            km = float(inst.dist[pos[a]][pos[b]])
            drive = sh.travel_min(km)
            kg = 0.0 if b == 0 else float(level.get(b, 0.0))
            service = 0.0 if b == 0 else sh.service_time_min
            km_acc += km
            kg_acc += kg
            min_acc += drive + service
            rows.append({
                'Rota': nr,
                'Ordem': step,
                'De_id': a,
                'Para_id': b,
                'De_Lat': round(inst.latlon[a]['Latitude'], 6),
                'De_Lon': round(inst.latlon[a]['Longitude'], 6),
                'Para_Lat': round(inst.latlon[b]['Latitude'], 6),
                'Para_Lon': round(inst.latlon[b]['Longitude'], 6),
                'Dist_troco_km': round(km, 4),
                'Dist_acum_km': round(km_acc, 4),
                'Peso_contentor_kg': round(kg, 2),
                'Peso_acum_kg': round(kg_acc, 2),
                'Conducao_troco_min': round(drive, 2),
                'Servico_min': round(service, 2),
                'Tempo_acum_min': round(min_acc, 2),
                'Tempo_acum_h': round(min_acc / 60.0, 3),
                'Nota': 'saida do deposito' if a == 0 else
                        ('regresso ao deposito' if b == 0 else ''),
            })
        totals.append({
            'Rota': nr,
            'N_contentores': len(routes[nr]),
            'N_trocos': len(seq) - 1,
            'Distancia_total_km': round(km_acc, 4),
            'Peso_total_kg': round(kg_acc, 2),
            'Conducao_min': round(sh.travel_min(km_acc), 2),
            'Servico_min': round(len(routes[nr]) * sh.service_time_min, 2),
            'Jornada_min': round(min_acc, 2),
            'Jornada_h': round(min_acc / 60.0, 3),
        })
    return pd.DataFrame(rows), pd.DataFrame(totals)


def validate(totals: pd.DataFrame, solution_path) -> pd.DataFrame:
    """Re-derive the headline KPI from the legs and confront the workbook."""
    checks = []
    km_legs = float(totals['Distancia_total_km'].sum())
    kg_legs = float(totals['Peso_total_kg'].sum())
    try:
        kpi = pd.read_excel(solution_path, sheet_name='2_KPI_General').set_index('KPI')['Value']

        def val(name):
            v = kpi[name]
            return float(v.iloc[0] if hasattr(v, 'iloc') else v)

        pairs = [('Distancia total (km)', km_legs, val('Total distance')),
                 ('Peso total (kg)', kg_legs, val('Total waste collected'))]
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        pairs = [('Distancia total (km)', km_legs, None),
                 ('Peso total (kg)', kg_legs, None)]
        checks.append({'Verificacao': 'Leitura do workbook', 'Somatorio_dos_trocos': '',
                       'KPI_do_modelo': '', 'Diferenca': '', 'Estado': f'falhou: {exc}'})

    for name, from_legs, from_kpi in pairs:
        diff = None if from_kpi is None else round(from_legs - from_kpi, 6)
        checks.append({
            'Verificacao': name,
            'Somatorio_dos_trocos': round(from_legs, 4),
            'KPI_do_modelo': from_kpi,
            'Diferenca': diff,
            'Estado': 'n/d' if diff is None else ('CONFERE' if abs(diff) < 0.01 else 'DIVERGE'),
        })
    return pd.DataFrame(checks)


def main() -> None:
    p = base_parser(__doc__)
    p.add_argument('--solution', default=None,
                   help='result workbook (default: <results>/Day_01/result_Day_01.xlsx)')
    p.add_argument('--out', default=None,
                   help='output Excel (default: <results>/route_manifest_<label>.xlsx)')
    args = p.parse_args()

    cfg = Config.from_yaml(args.config)
    inst = load_instance(cfg)
    results = cfg.path('results', create_dir=True)
    solution = args.solution or (results / 'Day_01' / 'result_Day_01.xlsx')

    print(f'=== STEP 7 — MANIFESTO DA ROTA ({cfg.label}) ===')
    print(f'Solucao : {solution}')
    print(f'Matriz  : {cfg.path("ors_matrix").name}  (perfil {cfg.ors.transport_mode})')
    print(f'Hipoteses: {cfg.shift.speed_kmh:g} km/h | {cfg.shift.service_time_min:g} min/contentor\n')

    legs, totals = manifest(routes_from_solution_workbook(solution), inst, cfg)
    checks = validate(totals, solution)

    print(totals.to_string(index=False))
    print('\n--- Validacao ---')
    print(checks.to_string(index=False))

    out = args.out or (results / f'route_manifest_{cfg.label}.xlsx')
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        legs.to_excel(w, sheet_name='Trocos', index=False)
        totals.to_excel(w, sheet_name='Totais', index=False)
        checks.to_excel(w, sheet_name='Validacao', index=False)
        ws = w.sheets['Trocos']
        ws.freeze_panes = 'C2'
        for col, width in zip('ABCDEFGHIJKLMNOPQR',
                              (6, 7, 8, 9, 11, 11, 11, 11, 13, 13, 15, 13, 16, 11, 14, 13, 22)):
            ws.column_dimensions[col].width = width
    print(f'\nEscrito em: {out}')
    print(f'  Folha "Trocos"   : {len(legs)} linhas (uma por troco, deposito incluido)')
    print(f'  Folha "Totais"   : {len(totals)} rota(s)')
    print(f'  Folha "Validacao": somatorio dos trocos vs KPI do modelo')


if __name__ == '__main__':
    main()
