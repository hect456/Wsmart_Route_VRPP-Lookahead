"""Step 3c — Outputs: Folium maps + Excel results workbook.

The workbook keeps the 10 sheets of the original notebooks so that results from
different instances stay directly comparable:

    1_Lookahead  2_KPI_General  3_KPI_Routes  4_Route{n}_Seq  5_MustGo
    6_MustGoLA   7_Not_Visited  8_All_Bins    9_Parameters    10_Verification
"""
from __future__ import annotations

from pathlib import Path

import folium
import pandas as pd

from .config import Config
from .instance import Instance
from .vrpp import Solution

COLORS = {'MustGo': '#d9534f', 'MustGoLA': '#f0ad4e', 'Optional': '#337ab7'}
COLOR_NOT_COLLECTED = '#9e9e9e'


def _add_not_collected_layer(m, sol: Solution, inst: Instance) -> int:
    """Grey dots for every bin no route collects.

    Added before the route so the served stops always draw on top of them, and
    kept small and semi-transparent: the point is to show the coverage the
    solution leaves behind without competing with the route itself.
    """
    n = 0
    for i, bin_id in sol.id_map.items():
        if i == sol.dep or sol.g_val.get(i):
            continue
        pos = inst.latlon.get(bin_id)
        if not pos:
            continue
        folium.CircleMarker(
            [pos['Latitude'], pos['Longitude']],
            radius=3.5, color=COLOR_NOT_COLLECTED, weight=1, opacity=0.75,
            fill=True, fill_color=COLOR_NOT_COLLECTED, fill_opacity=0.55,
            popup=(f'<b>NOT COLLECTED</b><br>ID: {bin_id}<br>'
                   f'Type: {sol.kind_orig.get(i, "-")}<br>'
                   f'Level: {sol.S.get(i, 0.0):.2f} kg ({sol.pct.get(i, 0.0):.1f}%)'),
            tooltip=f'ID:{bin_id} | not collected').add_to(m)
        n += 1
    return n


# ══════════════════════════════════════════════════════════════════
# Maps
# ══════════════════════════════════════════════════════════════════
def plot_route(sol: Solution, nr: int, inst: Instance, folder: Path,
               show_not_collected: bool = True) -> None:
    route = sol.routes[nr - 1]
    order = [route[0][0]] + [j for (_, j) in route]
    pts = [{'lat': inst.latlon[sol.id_map[n]]['Latitude'],
            'lon': inst.latlon[sol.id_map[n]]['Longitude'],
            'bin': sol.id_map[n], 'node': n, 'pos': p}
           for p, n in enumerate(order) if sol.id_map[n] in inst.latlon]
    if not pts:
        return

    m = folium.Map(location=[pts[0]['lat'], pts[0]['lon']], zoom_start=13)
    n_grey = _add_not_collected_layer(m, sol, inst) if show_not_collected else 0
    coords, visit = [], 0
    for p in pts:
        coords.append([p['lat'], p['lon']])
        if p['node'] == sol.dep:
            if p['pos'] == 0:
                folium.Marker(
                    [p['lat'], p['lon']],
                    icon=folium.Icon(color='black', icon='home', prefix='fa'),
                    popup=f'<b>DEPOT</b><br>Lat: {p["lat"]:.6f}<br>Lon: {p["lon"]:.6f}',
                    tooltip='Depot').add_to(m)
            continue
        visit += 1
        label = sol.kind_orig[p['node']]
        color = COLORS.get(label, COLORS['Optional'])
        folium.Marker(
            [p['lat'], p['lon']],
            icon=folium.DivIcon(html=(
                f'<div style="font-size:10px;font-weight:bold;color:white;background:{color};'
                f'border-radius:50%;width:24px;height:24px;text-align:center;line-height:24px;'
                f'border:2px solid white;box-shadow:1px 1px 3px rgba(0,0,0,.5)">{visit}</div>'),
                icon_size=(24, 24), icon_anchor=(12, 12)),
            popup=f'<b>#{visit}</b><br>ID: {p["bin"]}<br>Type: {label}<br>'
                  f'Level: {sol.S[p["node"]]:.2f} kg ({sol.pct[p["node"]]:.1f}%)',
            tooltip=f'#{visit} | ID:{p["bin"]} | {label}').add_to(m)

    folium.PolyLine(coords, color='darkblue', weight=2.5, opacity=0.8).add_to(m)
    m.get_root().html.add_child(folium.Element(
        '<div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:white;'
        'padding:8px 12px;border-radius:6px;border:1px solid #ccc;font-size:12px">'
        f'<b>Legend — Route {nr}</b><br>'
        f'<span style="color:{COLORS["MustGo"]}">&#11044;</span> MustGo&nbsp;&nbsp;'
        f'<span style="color:{COLORS["MustGoLA"]}">&#11044;</span> MustGoLA&nbsp;&nbsp;'
        f'<span style="color:{COLORS["Optional"]}">&#11044;</span> Optional&nbsp;&nbsp;'
        '<span style="color:black">&#8962;</span> Depot'
        + (f'<br><span style="color:{COLOR_NOT_COLLECTED}">&#11044;</span> '
           f'Not collected ({n_grey})' if n_grey else '')
        + '</div>'))

    folder.mkdir(parents=True, exist_ok=True)
    m.save(str(folder / f'route_{nr}.html'))
    print(f'    Map route {nr}: {visit} stops'
          + (f' | {n_grey} bins not collected shown in grey' if n_grey else ''))


def plot_routes(sol: Solution, inst: Instance, folder: Path,
                show_not_collected: bool = True) -> None:
    print(f'\n  Generating maps ({len(sol.routes)} route(s))...')
    for nr in range(1, len(sol.routes) + 1):
        plot_route(sol, nr, inst, folder, show_not_collected=show_not_collected)


# ══════════════════════════════════════════════════════════════════
# Excel
# ══════════════════════════════════════════════════════════════════
def kpi_per_route(sol: Solution, cfg: Config) -> pd.DataFrame:
    Q = cfg.model.Q
    rows = []
    for nr, rt in enumerate(sol.routes, 1):
        d_km = t_min = weight = 0.0
        n_b = n_mg = n_la = n_op = 0
        seq = []
        for (i, j) in rt:
            d_km += sol.D[i][j]
            t_min += sol.TM[i][j]
            if j != sol.dep and sol.g_val.get(j):
                n_b += 1
                weight += sol.S[j]
                seq.append(sol.id_map[j])
                n_mg += sol.kind[j] == 'MustGo'
                n_la += sol.kind[j] == 'MustGoLA'
                n_op += sol.kind[j] == 'Optional'
        rows.append({
            'Route': nr, 'N_Bins': n_b, 'N_MustGo': n_mg, 'N_MustGoLA': n_la,
            'N_Optional': n_op, 'Total_Waste_kg': round(weight, 2),
            'Distance_km': round(d_km, 4), 'Distance_m': round(d_km * 1000, 1),
            'Travel_Time_min': round(t_min, 2), 'Cap_Used_pct': round(weight / Q * 100, 2),
            'Exceeds_Q': 'YES !!!' if weight > Q + 1e-3 else 'no',
            'Sequence': ('0 > ' + ' > '.join(map(str, seq)) + ' > 0') if seq else '0 > 0',
        })
    return pd.DataFrame(rows)


def export_excel(sol: Solution, inst: Instance, cfg: Config, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'result_{sol.day}.xlsx'
    mod, sv, la = cfg.model, cfg.solver, cfg.lookahead
    NR = [i for i in sol.id_map if i != sol.dep]
    kpi, dg = sol.kpi, sol.diag
    df_routes = kpi_per_route(sol, cfg)

    def points(keep) -> pd.DataFrame:
        return pd.DataFrame([{
            'id_contentor': sol.id_map[i],
            'Route': sol.bin_route.get(sol.id_map[i], '-'),
            'Forced_solver': sol.forced[i],
            'Collected': 'Yes' if sol.g_val[i] else 'No',
            'Level_pct': round(sol.pct[i], 2),
            'Level_kg': round(sol.S[i], 3),
            'CAP_CONT_kg': round(inst.cap.get(sol.id_map[i], 0.0), 2),
            'Daily_Rate_pct': round(inst.ai_pct.get(sol.id_map[i], 0.0), 2),
        } for i in NR if keep(i)])

    with pd.ExcelWriter(path, engine='openpyxl') as w:
        sol.df_lookahead.to_excel(w, sheet_name='1_Lookahead', index=False)

        pd.DataFrame([
            {'KPI': 'OBJECTIVE FUNCTION', 'Value': '', 'Unit': ''},
            {'KPI': 'Net profit', 'Value': kpi['Net_Profit_euro'], 'Unit': 'euro'},
            {'KPI': 'Revenue (R x waste)', 'Value': kpi['Revenue_euro'], 'Unit': 'euro'},
            {'KPI': 'Distance cost (C x km)', 'Value': kpi['Distance_Cost_euro'], 'Unit': 'euro'},
            {'KPI': 'Vehicle cost (OMEGA x k)', 'Value': kpi['Vehicle_Cost_euro'], 'Unit': 'euro'},
            {'KPI': '', 'Value': '', 'Unit': ''},
            {'KPI': 'WASTE', 'Value': '', 'Unit': ''},
            {'KPI': 'Total waste collected', 'Value': kpi['Total_Waste_kg'], 'Unit': 'kg'},
            {'KPI': 'Total vehicle capacity', 'Value': kpi['Vehicles'] * mod.Q, 'Unit': 'kg'},
            {'KPI': 'Capacity used', 'Value': kpi['Capacity_Used_pct'], 'Unit': '%'},
            {'KPI': '', 'Value': '', 'Unit': ''},
            {'KPI': 'BINS', 'Value': '', 'Unit': ''},
            {'KPI': 'Total visited', 'Value': kpi['N_Visited'], 'Unit': ''},
            {'KPI': 'MustGo forced (solver)', 'Value': kpi['N_MustGo'], 'Unit': ''},
            {'KPI': 'MustGo downgraded to Optional', 'Value': kpi['N_MG_downgraded'], 'Unit': ''},
            {'KPI': 'MustGoLA', 'Value': kpi['N_MustGoLA'], 'Unit': ''},
            {'KPI': 'Optional visited', 'Value': kpi['N_Optional'], 'Unit': ''},
            {'KPI': 'Not visited', 'Value': kpi['N_Not_Visited'], 'Unit': ''},
            {'KPI': '', 'Value': '', 'Unit': ''},
            {'KPI': 'ROUTES', 'Value': '', 'Unit': ''},
            {'KPI': 'Number of vehicles used', 'Value': kpi['Vehicles'], 'Unit': ''},
            {'KPI': 'MAX_ROUTES (constraint)', 'Value': mod.MAX_ROUTES, 'Unit': ''},
            {'KPI': 'Total distance', 'Value': kpi['Total_Distance_km'], 'Unit': 'km'},
            {'KPI': 'Total distance', 'Value': kpi['Total_Distance_m'], 'Unit': 'm'},
            {'KPI': 'km per kg', 'Value': kpi['km_per_kg'], 'Unit': 'km/kg'},
            {'KPI': 'Total travel time', 'Value': kpi['Travel_Time_min'], 'Unit': 'min'},
            {'KPI': '', 'Value': '', 'Unit': ''},
            {'KPI': 'SOLVER', 'Value': '', 'Unit': ''},
            {'KPI': 'MIP Gap', 'Value': kpi['MIP_Gap_pct'], 'Unit': '%'},
            {'KPI': 'Solver time', 'Value': kpi['Solver_Time_s'], 'Unit': 's'},
            {'KPI': 'Solver time', 'Value': kpi['Solver_Time_h'], 'Unit': 'h'},
            {'KPI': 'Status (2=Optimal,9=TimeLimit)', 'Value': kpi['Solver_Status'], 'Unit': ''},
        ]).to_excel(w, sheet_name='2_KPI_General', index=False)

        df_routes.to_excel(w, sheet_name='3_KPI_Routes', index=False)

        for nr, rt in enumerate(sol.routes, 1):
            rows = [{'Order': 0, 'id_contentor': 0, 'Type': 'Depot', 'Level_kg': 0,
                     'Level_pct': 0, 'CAP_CONT_kg': 0, 'Collected': '-'}]
            order = 0
            for (_, j) in rt:
                if j == sol.dep:
                    rows.append({'Order': order + 1, 'id_contentor': 0,
                                 'Type': 'Return_Depot', 'Level_kg': 0, 'Level_pct': 0,
                                 'CAP_CONT_kg': 0, 'Collected': '-'})
                else:
                    order += 1
                    rows.append({
                        'Order': order, 'id_contentor': sol.id_map[j], 'Type': sol.kind[j],
                        'Level_kg': round(sol.S[j], 3), 'Level_pct': round(sol.pct[j], 2),
                        'CAP_CONT_kg': round(inst.cap.get(sol.id_map[j], 0.0), 2),
                        'Collected': 'Yes' if sol.g_val[j] else 'No'})
            pd.DataFrame(rows).to_excel(w, sheet_name=f'4_Route{nr}_Seq', index=False)

        points(lambda i: sol.kind_orig[i] == 'MustGo').to_excel(w, sheet_name='5_MustGo', index=False)
        points(lambda i: sol.kind_orig[i] == 'MustGoLA').to_excel(w, sheet_name='6_MustGoLA', index=False)
        sol.df_not_visited.to_excel(w, sheet_name='7_Not_Visited', index=False)
        points(lambda i: True).to_excel(w, sheet_name='8_All_Bins', index=False)

        pd.DataFrame([
            {'Parameter': 'INSTANCE', 'Value': cfg.label, 'Description': f'{inst.n} bins + depot'},
            {'Parameter': 'B', 'Value': mod.B, 'Description': 'waste density (kg/m3)'},
            {'Parameter': 'Q', 'Value': mod.Q, 'Description': 'vehicle capacity (kg)'},
            {'Parameter': 'R', 'Value': mod.R, 'Description': 'revenue (euro/kg)'},
            {'Parameter': 'C', 'Value': mod.C, 'Description': 'travel cost (euro/km)'},
            {'Parameter': 'OMEGA', 'Value': mod.OMEGA, 'Description': 'fixed cost per vehicle (euro)'},
            {'Parameter': 'MAX_ROUTES', 'Value': mod.MAX_ROUTES, 'Description': 'k <= MAX_ROUTES'},
            {'Parameter': 'MIP_GAP', 'Value': mod.MIP_GAP, 'Description': 'solver tolerance'},
            {'Parameter': 'TIME_LIMIT', 'Value': mod.TIME_LIMIT, 'Description': 'solver time limit (s)'},
            {'Parameter': 'THRESHOLD_MG', 'Value': la.threshold_mg, 'Description': '% level+ai>=thr_i -> MustGo'},
            {'Parameter': 'THRESHOLD_OVERFLOW', 'Value': la.threshold_overflow, 'Description': '% level+ai*k>=ovf_i -> MustGoLA'},
            {'Parameter': 'LOOKAHEAD_WINDOW', 'Value': la.window, 'Description': 'days of the horizon'},
            {'Parameter': 'SIMULATION_DAYS', 'Value': la.days, 'Description': 'simulated days'},
            {'Parameter': 'Auto_MG_adjust', 'Value': 'level_pct desc', 'Description': 'surplus MustGo downgraded to Optional'},
            {'Parameter': 'KNN_arcs', 'Value': dg['knn'], 'Description': 'nearest neighbours per node'},
            {'Parameter': 'Keep_MG_arcs', 'Value': sv.keep_mustgo_arcs, 'Description': 'keep MustGo-MustGo arcs'},
            {'Parameter': 'SEED', 'Value': sv.seed if sv.seed is not None else 'default', 'Description': 'Gurobi seed'},
            {'Parameter': 'LB_y', 'Value': 'y>=S[i]*x[i,j]', 'Description': 'lower bound on the arcs'},
            {'Parameter': 'NodeMethod', 'Value': sv.node_method, 'Description': 'method at the B&B nodes'},
        ]).to_excel(w, sheet_name='9_Parameters', index=False)

        s_all = sum(sol.S[i] for i in NR)
        cap_calc = sum(inst.cap.get(sol.id_map[i], 0.0) for i in NR)
        verification = [
            {'Item': 'MAX_ROUTES CONSTRAINT', 'Value': mod.MAX_ROUTES, 'Unit': 'vehicles'},
            {'Item': 'Original MustGo (lookahead)', 'Value': dg['n_mg_forced'] + dg['n_downgraded'], 'Unit': ''},
            {'Item': 'MustGo forced in the solver', 'Value': dg['n_mg_forced'], 'Unit': ''},
            {'Item': 'MustGo downgraded to Optional', 'Value': dg['n_downgraded'], 'Unit': ''},
            {'Item': 'Forced MG weight', 'Value': round(dg['mg_weight'], 2), 'Unit': 'kg'},
            {'Item': 'Capacity MAX_ROUTES x Q', 'Value': mod.fleet_capacity_kg, 'Unit': 'kg'},
            {'Item': 'Slack', 'Value': round(mod.fleet_capacity_kg - dg['mg_weight'], 2), 'Unit': 'kg'},
            {'Item': '', 'Value': '', 'Unit': ''},
            {'Item': 'Total CAP_CONT = B*Ncont*Vcont', 'Value': round(cap_calc, 2), 'Unit': 'kg'},
            {'Item': 'Total Si_kg (start of the day)', 'Value': round(s_all, 2), 'Unit': 'kg'},
            {'Item': 'Mean fill level of the network', 'Value': round(s_all / cap_calc * 100, 2) if cap_calc else 0, 'Unit': '%'},
            {'Item': 'Arcs in the model', 'Value': dg['n_arcs'], 'Unit': f'of {dg["n_total"]}'},
            {'Item': '% arcs kept', 'Value': dg['pct_kept'], 'Unit': '%'},
            {'Item': 'Orphan arcs (must be 0)', 'Value': dg['orphans'], 'Unit': ''},
            {'Item': '', 'Value': '', 'Unit': ''},
            {'Item': 'Vehicles used', 'Value': kpi['Vehicles'], 'Unit': f'(max {mod.MAX_ROUTES})'},
            {'Item': 'Total collected', 'Value': kpi['Total_Waste_kg'], 'Unit': 'kg'},
            {'Item': 'Proven MIP Gap', 'Value': kpi['MIP_Gap_pct'], 'Unit': '%'},
            {'Item': 'Solver time', 'Value': kpi['Solver_Time_h'], 'Unit': 'h'},
            {'Item': '', 'Value': '', 'Unit': ''},
            {'Item': 'CAPACITY PER ROUTE', 'Value': '', 'Unit': ''},
        ] + [{'Item': f'Route {r.Route} ({r.Total_Waste_kg} kg)', 'Value': r.Cap_Used_pct,
              'Unit': '%  ' + r.Exceeds_Q} for r in df_routes.itertuples()]
        pd.DataFrame(verification).to_excel(w, sheet_name='10_Verification', index=False)

    print(f'  Excel: {path}')
    return path


def export_summary(kpis: list, cfg: Config, diagnostics: pd.DataFrame | None = None) -> Path | None:
    """Consolidates the KPI of every day into a single workbook."""
    if not kpis:
        print('No solution found — nothing to consolidate.')
        return None

    folder = cfg.path('results', create_dir=True)
    df_summary = pd.DataFrame(kpis)
    columns = ['Day', 'Vehicles', 'N_Visited', 'N_MustGo', 'N_MustGoLA', 'N_Not_Visited',
               'Total_Waste_kg', 'Total_Distance_km', 'Travel_Time_min',
               'Capacity_Used_pct', 'Net_Profit_euro']
    print(df_summary[[c for c in columns if c in df_summary.columns]].to_string(index=False))

    general, routes = [], []
    for day in range(1, cfg.lookahead.days + 1):
        fp = folder / f'Day_{day:02d}' / f'result_Day_{day:02d}.xlsx'
        if fp.exists():
            general.append(pd.read_excel(fp, sheet_name='2_KPI_General').assign(Day=f'Day_{day:02d}'))
            dr = pd.read_excel(fp, sheet_name='3_KPI_Routes')
            dr.insert(0, 'Day', f'Day_{day:02d}')
            routes.append(dr)

    destination = folder / f'summary_{cfg.label}_all_days.xlsx'
    with pd.ExcelWriter(destination, engine='openpyxl') as w:
        df_summary.to_excel(w, sheet_name='KPI_Per_Day', index=False)
        if general:
            pd.concat(general, ignore_index=True).to_excel(w, sheet_name='KPI_General_All', index=False)
        if routes:
            pd.concat(routes, ignore_index=True).to_excel(w, sheet_name='KPI_Routes_All', index=False)
        if diagnostics is not None:
            diagnostics.to_excel(w, sheet_name='Instance_Diagnostics', index=False)
    print(f'\nConsolidated summary: {destination}')
    return destination
