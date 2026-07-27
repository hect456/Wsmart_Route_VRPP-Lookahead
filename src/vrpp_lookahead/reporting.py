"""Passo 3c — Saidas: mapas Folium + livro Excel de resultados.

O livro mantem as 10 folhas dos notebooks originais, para que os resultados
de instancias diferentes sejam directamente comparaveis:

    1_Lookahead  2_KPI_Geral  3_KPI_Rotas  4_Rota{n}_Seq  5_MustGo
    6_MustGoLA   7_Nao_Visitados  8_Todos_Contentores  9_Parametros  10_Verificacao
"""
from __future__ import annotations

from pathlib import Path

import folium
import pandas as pd

from .config import Config
from .instancia import Instancia
from .vrpp import Solucao

CORES = {'MustGo': '#d9534f', 'MustGoLA': '#f0ad4e', 'Opcional': '#337ab7'}


# ══════════════════════════════════════════════════════════════════
# Mapas
# ══════════════════════════════════════════════════════════════════
def plotar_rota(sol: Solucao, nr: int, inst: Instancia, pasta: Path) -> None:
    rota = sol.rotas[nr - 1]
    ordem = [rota[0][0]] + [j for (_, j) in rota]
    pts = [{'lat': inst.latlon[sol.id_map[n]]['Latitude'],
            'lon': inst.latlon[sol.id_map[n]]['Longitude'],
            'cid': sol.id_map[n], 'no': n, 'pos': p}
           for p, n in enumerate(ordem) if sol.id_map[n] in inst.latlon]
    if not pts:
        return

    m = folium.Map(location=[pts[0]['lat'], pts[0]['lon']], zoom_start=13)
    coords, visita = [], 0
    for p in pts:
        coords.append([p['lat'], p['lon']])
        if p['no'] == sol.dep:
            if p['pos'] == 0:
                folium.Marker(
                    [p['lat'], p['lon']],
                    icon=folium.Icon(color='black', icon='home', prefix='fa'),
                    popup=f'<b>DEPOSITO</b><br>Lat: {p["lat"]:.6f}<br>Lon: {p["lon"]:.6f}',
                    tooltip='Deposito').add_to(m)
            continue
        visita += 1
        rotulo = sol.tipo_orig[p['no']]
        cor = CORES.get(rotulo, CORES['Opcional'])
        folium.Marker(
            [p['lat'], p['lon']],
            icon=folium.DivIcon(html=(
                f'<div style="font-size:10px;font-weight:bold;color:white;background:{cor};'
                f'border-radius:50%;width:24px;height:24px;text-align:center;line-height:24px;'
                f'border:2px solid white;box-shadow:1px 1px 3px rgba(0,0,0,.5)">{visita}</div>'),
                icon_size=(24, 24), icon_anchor=(12, 12)),
            popup=f'<b>#{visita}</b><br>ID: {p["cid"]}<br>Tipo: {rotulo}<br>'
                  f'Nivel: {sol.S[p["no"]]:.2f} kg ({sol.pct[p["no"]]:.1f}%)',
            tooltip=f'#{visita} | ID:{p["cid"]} | {rotulo}').add_to(m)

    folium.PolyLine(coords, color='darkblue', weight=2.5, opacity=0.8).add_to(m)
    m.get_root().html.add_child(folium.Element(
        '<div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:white;'
        'padding:8px 12px;border-radius:6px;border:1px solid #ccc;font-size:12px">'
        f'<b>Legenda — Rota {nr}</b><br>'
        f'<span style="color:{CORES["MustGo"]}">&#11044;</span> MustGo&nbsp;&nbsp;'
        f'<span style="color:{CORES["MustGoLA"]}">&#11044;</span> MustGoLA&nbsp;&nbsp;'
        f'<span style="color:{CORES["Opcional"]}">&#11044;</span> Opcional&nbsp;&nbsp;'
        '<span style="color:black">&#8962;</span> Deposito</div>'))

    pasta.mkdir(parents=True, exist_ok=True)
    m.save(str(pasta / f'rota_{nr}.html'))
    print(f'    Mapa rota {nr}: {visita} paragens')


def plotar_rotas(sol: Solucao, inst: Instancia, pasta: Path) -> None:
    print(f'\n  Gerando mapas ({len(sol.rotas)} rota(s))...')
    for nr in range(1, len(sol.rotas) + 1):
        plotar_rota(sol, nr, inst, pasta)


# ══════════════════════════════════════════════════════════════════
# Excel
# ══════════════════════════════════════════════════════════════════
def kpi_por_rota(sol: Solucao, cfg: Config) -> pd.DataFrame:
    Q = cfg.modelo.Q
    linhas = []
    for nr, rt in enumerate(sol.rotas, 1):
        d_km = t_min = peso = 0.0
        n_c = n_mg = n_la = n_op = 0
        seq = []
        for (i, j) in rt:
            d_km += sol.D[i][j]
            t_min += sol.TM[i][j]
            if j != sol.dep and sol.g_val.get(j):
                n_c += 1
                peso += sol.S[j]
                seq.append(sol.id_map[j])
                n_mg += sol.tipo[j] == 'MustGo'
                n_la += sol.tipo[j] == 'MustGoLA'
                n_op += sol.tipo[j] == 'Opcional'
        linhas.append({
            'Rota': nr, 'N_Contentores': n_c, 'N_MustGo': n_mg, 'N_MustGoLA': n_la,
            'N_Opcionais': n_op, 'Total_Residuos_kg': round(peso, 2),
            'Distancia_km': round(d_km, 4), 'Distancia_m': round(d_km * 1000, 1),
            'Tempo_Viagem_min': round(t_min, 2), 'Cap_Usada_pct': round(peso / Q * 100, 2),
            'Excede_Q': 'SIM !!!' if peso > Q + 1e-3 else 'nao',
            'Sequencia': ('0 > ' + ' > '.join(map(str, seq)) + ' > 0') if seq else '0 > 0',
        })
    return pd.DataFrame(linhas)


def exportar_excel(sol: Solucao, inst: Instancia, cfg: Config, pasta: Path) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f'resultado_{sol.dia}.xlsx'
    mod, sv, la = cfg.modelo, cfg.solver, cfg.lookahead
    NR = [i for i in sol.id_map if i != sol.dep]
    kpi, dg = sol.kpi, sol.diag
    df_rotas = kpi_por_rota(sol, cfg)

    def pontos(filtro) -> pd.DataFrame:
        return pd.DataFrame([{
            'id_contentor': sol.id_map[i],
            'Rota': sol.cont_rota.get(sol.id_map[i], '-'),
            'Forcado_solver': sol.forcado[i],
            'Recolhido': 'Sim' if sol.g_val[i] else 'Nao',
            'Nivel_pct': round(sol.pct[i], 2),
            'Nivel_kg': round(sol.S[i], 3),
            'CAP_CONT_kg': round(inst.cap.get(sol.id_map[i], 0.0), 2),
            'Taxa_dia_pct': round(inst.ai_pct.get(sol.id_map[i], 0.0), 2),
        } for i in NR if filtro(i)])

    with pd.ExcelWriter(caminho, engine='openpyxl') as w:
        sol.df_lookahead.to_excel(w, sheet_name='1_Lookahead', index=False)

        pd.DataFrame([
            {'KPI': 'FUNCAO OBJECTIVO', 'Valor': '', 'Unidade': ''},
            {'KPI': 'Lucro liquido', 'Valor': kpi['Lucro_Liquido_euro'], 'Unidade': 'euro'},
            {'KPI': 'Receita (R x residuos)', 'Valor': kpi['Receita_euro'], 'Unidade': 'euro'},
            {'KPI': 'Custo distancia (C x km)', 'Valor': kpi['Custo_Distancia_euro'], 'Unidade': 'euro'},
            {'KPI': 'Custo veiculos (OMEGA x k)', 'Valor': kpi['Custo_Veiculos_euro'], 'Unidade': 'euro'},
            {'KPI': '', 'Valor': '', 'Unidade': ''},
            {'KPI': 'RESIDUOS', 'Valor': '', 'Unidade': ''},
            {'KPI': 'Total residuos recolhidos', 'Valor': kpi['Total_Residuos_kg'], 'Unidade': 'kg'},
            {'KPI': 'Capacidade total veiculos', 'Valor': kpi['Veiculos'] * mod.Q, 'Unidade': 'kg'},
            {'KPI': 'Capacidade usada', 'Valor': kpi['Capacidade_Usada_pct'], 'Unidade': '%'},
            {'KPI': '', 'Valor': '', 'Unidade': ''},
            {'KPI': 'CONTENTORES', 'Valor': '', 'Unidade': ''},
            {'KPI': 'Total visitados', 'Valor': kpi['N_Visitados'], 'Unidade': ''},
            {'KPI': 'MustGo forcados (solver)', 'Valor': kpi['N_MustGo'], 'Unidade': ''},
            {'KPI': 'MustGo rebaixados p/Opcional', 'Valor': kpi['N_MG_rebaixados'], 'Unidade': ''},
            {'KPI': 'MustGoLA', 'Valor': kpi['N_MustGoLA'], 'Unidade': ''},
            {'KPI': 'Opcionais visitados', 'Valor': kpi['N_Opcionais'], 'Unidade': ''},
            {'KPI': 'Nao visitados', 'Valor': kpi['N_Nao_Visitados'], 'Unidade': ''},
            {'KPI': '', 'Valor': '', 'Unidade': ''},
            {'KPI': 'ROTAS', 'Valor': '', 'Unidade': ''},
            {'KPI': 'Num veiculos utilizados', 'Valor': kpi['Veiculos'], 'Unidade': ''},
            {'KPI': 'MAX_ROTAS (restricao)', 'Valor': mod.MAX_ROTAS, 'Unidade': ''},
            {'KPI': 'Distancia total', 'Valor': kpi['Distancia_Total_km'], 'Unidade': 'km'},
            {'KPI': 'Distancia total', 'Valor': kpi['Distancia_Total_m'], 'Unidade': 'm'},
            {'KPI': 'km por kg', 'Valor': kpi['km_por_kg'], 'Unidade': 'km/kg'},
            {'KPI': 'Tempo viagem total', 'Valor': kpi['Tempo_Viagem_min'], 'Unidade': 'min'},
            {'KPI': '', 'Valor': '', 'Unidade': ''},
            {'KPI': 'SOLVER', 'Valor': '', 'Unidade': ''},
            {'KPI': 'MIP Gap', 'Valor': kpi['MIP_Gap_pct'], 'Unidade': '%'},
            {'KPI': 'Tempo solver', 'Valor': kpi['Tempo_Solver_s'], 'Unidade': 's'},
            {'KPI': 'Tempo solver', 'Valor': kpi['Tempo_Solver_h'], 'Unidade': 'h'},
            {'KPI': 'Status (2=Optimal,9=TimeLimit)', 'Valor': kpi['Status_Solver'], 'Unidade': ''},
        ]).to_excel(w, sheet_name='2_KPI_Geral', index=False)

        df_rotas.to_excel(w, sheet_name='3_KPI_Rotas', index=False)

        for nr, rt in enumerate(sol.rotas, 1):
            linhas = [{'Ordem': 0, 'id_contentor': 0, 'Tipo': 'Deposito', 'Nivel_kg': 0,
                       'Nivel_pct': 0, 'CAP_CONT_kg': 0, 'Recolhido': '-'}]
            ordem = 0
            for (_, j) in rt:
                if j == sol.dep:
                    linhas.append({'Ordem': ordem + 1, 'id_contentor': 0,
                                   'Tipo': 'Retorno_Deposito', 'Nivel_kg': 0, 'Nivel_pct': 0,
                                   'CAP_CONT_kg': 0, 'Recolhido': '-'})
                else:
                    ordem += 1
                    linhas.append({
                        'Ordem': ordem, 'id_contentor': sol.id_map[j], 'Tipo': sol.tipo[j],
                        'Nivel_kg': round(sol.S[j], 3), 'Nivel_pct': round(sol.pct[j], 2),
                        'CAP_CONT_kg': round(inst.cap.get(sol.id_map[j], 0.0), 2),
                        'Recolhido': 'Sim' if sol.g_val[j] else 'Nao'})
            pd.DataFrame(linhas).to_excel(w, sheet_name=f'4_Rota{nr}_Seq', index=False)

        pontos(lambda i: sol.tipo_orig[i] == 'MustGo').to_excel(w, sheet_name='5_MustGo', index=False)
        pontos(lambda i: sol.tipo_orig[i] == 'MustGoLA').to_excel(w, sheet_name='6_MustGoLA', index=False)
        sol.df_nao_visitados.to_excel(w, sheet_name='7_Nao_Visitados', index=False)
        pontos(lambda i: True).to_excel(w, sheet_name='8_Todos_Contentores', index=False)

        pd.DataFrame([
            {'Parametro': 'INSTANCIA', 'Valor': cfg.etiqueta, 'Descricao': f'{inst.n} contentores + deposito'},
            {'Parametro': 'B', 'Valor': mod.B, 'Descricao': 'densidade dos residuos (kg/m3)'},
            {'Parametro': 'Q', 'Valor': mod.Q, 'Descricao': 'capacidade do veiculo (kg)'},
            {'Parametro': 'R', 'Valor': mod.R, 'Descricao': 'receita (euro/kg)'},
            {'Parametro': 'C', 'Valor': mod.C, 'Descricao': 'custo de deslocamento (euro/km)'},
            {'Parametro': 'OMEGA', 'Valor': mod.OMEGA, 'Descricao': 'custo fixo por veiculo (euro)'},
            {'Parametro': 'MAX_ROTAS', 'Valor': mod.MAX_ROTAS, 'Descricao': 'k <= MAX_ROTAS'},
            {'Parametro': 'MIP_GAP', 'Valor': mod.MIP_GAP, 'Descricao': 'tolerancia do solver'},
            {'Parametro': 'TIME_LIMIT', 'Valor': mod.TIME_LIMIT, 'Descricao': 'tempo maximo do solver (s)'},
            {'Parametro': 'THRESHOLD_MG', 'Valor': la.threshold_mg, 'Descricao': '% nivel+ai>=thr_i -> MustGo'},
            {'Parametro': 'THRESHOLD_OVERFLOW', 'Valor': la.threshold_overflow, 'Descricao': '% nivel+ai*k>=ovf_i -> MustGoLA'},
            {'Parametro': 'LOOKAHEAD_JANELA', 'Valor': la.janela, 'Descricao': 'dias do horizonte'},
            {'Parametro': 'DIAS_SIMULACAO', 'Valor': la.dias, 'Descricao': 'dias simulados'},
            {'Parametro': 'Ajuste_MG_auto', 'Valor': 'nivel_pct desc', 'Descricao': 'MustGo excedente rebaixado p/Opcional'},
            {'Parametro': 'KNN_arcos', 'Valor': dg['knn'], 'Descricao': 'vizinhos mais proximos por no'},
            {'Parametro': 'Preservar_arcos_MG', 'Valor': sv.preservar_arcos_mustgo, 'Descricao': 'manter arcos MustGo-MustGo'},
            {'Parametro': 'SEED', 'Valor': sv.seed if sv.seed is not None else 'default', 'Descricao': 'semente Gurobi'},
            {'Parametro': 'LB_y', 'Valor': 'y>=S[i]*x[i,j]', 'Descricao': 'limite inferior nos arcos'},
            {'Parametro': 'NodeMethod', 'Valor': sv.node_method, 'Descricao': 'metodo nos nos do B&B'},
        ]).to_excel(w, sheet_name='9_Parametros', index=False)

        s_all = sum(sol.S[i] for i in NR)
        cap_calc = sum(inst.cap.get(sol.id_map[i], 0.0) for i in NR)
        verificacao = [
            {'Item': 'RESTRICAO MAX_ROTAS', 'Valor': mod.MAX_ROTAS, 'Unidade': 'veiculos'},
            {'Item': 'MustGo original (lookahead)', 'Valor': dg['n_mg_forcados'] + dg['n_rebaixados'], 'Unidade': ''},
            {'Item': 'MustGo forcados no solver', 'Valor': dg['n_mg_forcados'], 'Unidade': ''},
            {'Item': 'MustGo rebaixados p/Opcional', 'Valor': dg['n_rebaixados'], 'Unidade': ''},
            {'Item': 'Peso MG forcado', 'Valor': round(dg['mg_peso'], 2), 'Unidade': 'kg'},
            {'Item': 'Capacidade MAX_ROTAS x Q', 'Valor': mod.cap_frota_kg, 'Unidade': 'kg'},
            {'Item': 'Folga', 'Valor': round(mod.cap_frota_kg - dg['mg_peso'], 2), 'Unidade': 'kg'},
            {'Item': '', 'Valor': '', 'Unidade': ''},
            {'Item': 'CAP_CONT total = B*Ncont*Vcont', 'Valor': round(cap_calc, 2), 'Unidade': 'kg'},
            {'Item': 'Si_kg total (inicio do dia)', 'Valor': round(s_all, 2), 'Unidade': 'kg'},
            {'Item': 'Enchimento medio da rede', 'Valor': round(s_all / cap_calc * 100, 2) if cap_calc else 0, 'Unidade': '%'},
            {'Item': 'Arcos no modelo', 'Valor': dg['n_arcos'], 'Unidade': f'de {dg["n_total"]}'},
            {'Item': '% arcos mantidos', 'Valor': dg['pct_mantidos'], 'Unidade': '%'},
            {'Item': 'Arcos orfaos (deve ser 0)', 'Valor': dg['orfaos'], 'Unidade': ''},
            {'Item': '', 'Valor': '', 'Unidade': ''},
            {'Item': 'Veiculos usados', 'Valor': kpi['Veiculos'], 'Unidade': f'(max {mod.MAX_ROTAS})'},
            {'Item': 'Total recolhido', 'Valor': kpi['Total_Residuos_kg'], 'Unidade': 'kg'},
            {'Item': 'MIP Gap provado', 'Valor': kpi['MIP_Gap_pct'], 'Unidade': '%'},
            {'Item': 'Tempo solver', 'Valor': kpi['Tempo_Solver_h'], 'Unidade': 'h'},
            {'Item': '', 'Valor': '', 'Unidade': ''},
            {'Item': 'CAPACIDADE POR ROTA', 'Valor': '', 'Unidade': ''},
        ] + [{'Item': f'Rota {r.Rota} ({r.Total_Residuos_kg} kg)', 'Valor': r.Cap_Usada_pct,
              'Unidade': '%  ' + r.Excede_Q} for r in df_rotas.itertuples()]
        pd.DataFrame(verificacao).to_excel(w, sheet_name='10_Verificacao', index=False)

    print(f'  Excel: {caminho}')
    return caminho


def exportar_resumo(kpis: list, cfg: Config, diagnostico: pd.DataFrame | None = None) -> Path | None:
    """Consolida os KPI de todos os dias num unico livro."""
    if not kpis:
        print('Nenhuma solucao encontrada — nada a consolidar.')
        return None

    pasta = cfg.ruta('resultados', criar_pasta=True)
    df_resumo = pd.DataFrame(kpis)
    colunas = ['Dia', 'Veiculos', 'N_Visitados', 'N_MustGo', 'N_MustGoLA', 'N_Nao_Visitados',
               'Total_Residuos_kg', 'Distancia_Total_km', 'Tempo_Viagem_min',
               'Capacidade_Usada_pct', 'Lucro_Liquido_euro']
    print(df_resumo[[c for c in colunas if c in df_resumo.columns]].to_string(index=False))

    geral, rotas = [], []
    for dia in range(1, cfg.lookahead.dias + 1):
        fp = pasta / f'Dia_{dia:02d}' / f'resultado_Dia_{dia:02d}.xlsx'
        if fp.exists():
            geral.append(pd.read_excel(fp, sheet_name='2_KPI_Geral').assign(Dia=f'Dia_{dia:02d}'))
            dr = pd.read_excel(fp, sheet_name='3_KPI_Rotas')
            dr.insert(0, 'Dia', f'Dia_{dia:02d}')
            rotas.append(dr)

    destino = pasta / f'resumo_{cfg.etiqueta}_todos_dias.xlsx'
    with pd.ExcelWriter(destino, engine='openpyxl') as w:
        df_resumo.to_excel(w, sheet_name='KPI_Por_Dia', index=False)
        if geral:
            pd.concat(geral, ignore_index=True).to_excel(w, sheet_name='KPI_Geral_Todos', index=False)
        if rotas:
            pd.concat(rotas, ignore_index=True).to_excel(w, sheet_name='KPI_Rotas_Todos', index=False)
        if diagnostico is not None:
            diagnostico.to_excel(w, sheet_name='Diagnostico_Instancia', index=False)
    print(f'\nResumo consolidado: {destino}')
    return destino
