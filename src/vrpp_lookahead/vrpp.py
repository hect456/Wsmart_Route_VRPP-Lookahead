"""Passo 3b — VRPP (Vehicle Routing Problem with Profits) em Gurobi.

Modelo (identico ao dos notebooks originais):

    max  R * SUM_i S_i g_i  -  C * SUM_ij D_ij x_ij  -  OMEGA * k
    s.a. k <= MAX_ROTAS

    x_ij  binaria   arco (i,j) usado
    g_i   binaria   ponto i recolhido            (g_i = 1 forcado para MustGo)
    y_ij  continua  carga transportada (kg)      -> capacidade Q
    f_ij  continua  fluxo unitario               -> eliminacao de sub-rotas

Pre-processamento:
  * filtragem de arcos por KNN bidireccional + capacidade par-a-par;
  * ajuste automatico dos MustGo quando o peso excede MAX_ROTAS x Q
    (os menos cheios sao rebaixados a Opcional — sem isto o modelo seria inviavel);
  * warm start por vizinho-mais-proximo sobre os MustGo.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import gurobipy as gp
import numpy as np
import pandas as pd
from gurobipy import GRB, quicksum

from .config import Config
from .instancia import Instancia
from .lookahead import ResultadoLookahead


@dataclass
class Solucao:
    dia: str
    rotas: list
    id_map: dict
    S: dict
    pct: dict
    tipo: dict
    tipo_orig: dict
    forcado: dict
    g_val: dict
    cont_rota: dict
    recolhidos: list
    kpi: dict
    diag: dict
    df_nao_visitados: pd.DataFrame
    df_lookahead: pd.DataFrame
    D: np.ndarray
    TM: np.ndarray
    dep: int = 0


# ══════════════════════════════════════════════════════════════════
# Sub-passos
# ══════════════════════════════════════════════════════════════════
def _arcos(N, dep, D, S, cfg: Config, forcados: set):
    """KNN bidireccional + eliminacao de pares que excedem Q.

    Os arcos de/para o deposito sao sempre mantidos (garantem viabilidade).
    Com `solver.preservar_arcos_mustgo=True` mantem tambem todos os arcos
    entre pontos MustGo (opcional; desligado por omissao para reproduzir
    exactamente o comportamento dos notebooks originais).
    """
    NR = N[1:]
    knn = max(1, min(cfg.solver.knn, len(NR) - 1))
    vizinhos = {i: {j for _, j in sorted((D[i][j], j) for j in NR if j != i)[:knn]} for i in NR}

    arcos, n_cap_elim = [], 0
    for i in N:
        for j in N:
            if i == j:
                continue
            if i == dep or j == dep:
                arcos.append((i, j))
                continue
            perto = (j in vizinhos[i]) or (i in vizinhos[j])
            if cfg.solver.preservar_arcos_mustgo and i in forcados and j in forcados:
                perto = True
            if not perto:
                continue
            if S[i] + S[j] > cfg.modelo.Q:      # par impossivel num veiculo de capacidade Q
                n_cap_elim += 1
                continue
            arcos.append((i, j))

    n_total = len(N) * (len(N) - 1)
    stats = {'knn': knn, 'n_total': n_total, 'n_arcos': len(arcos),
             'n_cap_elim': n_cap_elim, 'pct_mantidos': round(len(arcos) / n_total * 100, 1)}
    return arcos, stats


def _ajustar_mustgo(NR, S, crit, ids_rota, inst: Instancia, cfg: Config):
    """Se o peso MustGo excede MAX_ROTAS x Q, mantem os mais cheios (%) e rebaixa o resto."""
    mod = cfg.modelo
    mg = [i for i in NR if crit[i]]
    peso = sum(S[i] for i in mg)
    cap = mod.cap_frota_kg

    print('\n  Verificacao de capacidade MustGo:')
    print(f'    MustGo original: {len(mg)} contentores  |  {peso:.1f} kg')
    print(f'    Capacidade {mod.MAX_ROTAS} x {mod.Q:g} kg = {cap:g} kg')

    n_rebaixados = 0
    if peso > cap:
        ordenados = sorted(mg, key=lambda i: S[i] / max(inst.cap.get(ids_rota[i], 1.0), 1e-9),
                           reverse=True)
        mantidos, acc = [], 0.0
        for i in ordenados:
            if acc + S[i] <= cap:
                mantidos.append(i)
                acc += S[i]
        n_rebaixados = len(mg) - len(mantidos)
        mantidos_ids = {ids_rota[i] for i in mantidos}
        for i in NR:
            crit[i] = ids_rota[i] in mantidos_ids
        mg, peso = mantidos, acc
        print('    !!! Excede capacidade — ajuste automatico por nivel de enchimento:')
        print(f'    -> Mantidos como MustGo  : {len(mg):3d} ({peso:.1f} kg)')
        print(f'    -> Rebaixados p/Opcional : {n_rebaixados:3d} '
              f'(o solver ainda pode recolhe-los se lucrativo)')

    print(f'    OK — MG ajustado: {len(mg)} cont ({peso:.1f} kg)  folga: {cap - peso:.1f} kg')
    return mg, peso, n_rebaixados


def _warm_start(NR, dep, S, D, cfg: Config, obrigatorios, arcos_s):
    """Vizinho-mais-proximo sobre os pontos obrigatorios, limitado a MAX_ROTAS."""
    pendentes = set(obrigatorios)
    rotas = []
    while pendentes and len(rotas) < cfg.modelo.MAX_ROTAS:
        rota, cur, carga = [], dep, 0.0
        while True:
            melhor, melhor_d = None, float('inf')
            for j in pendentes:
                if carga + S[j] <= cfg.modelo.Q and D[cur][j] < melhor_d and (cur, j) in arcos_s:
                    melhor, melhor_d = j, D[cur][j]
            if melhor is None:
                break
            rota.append((cur, melhor))
            carga += S[melhor]
            pendentes.discard(melhor)
            cur = melhor
        if not rota:
            break
        rota.append((cur, dep))
        rotas.append(rota)
    return rotas


def _extrair_rotas(ativos, dep):
    """Reconstroi as rotas a partir dos arcos activos; devolve tambem arcos orfaos."""
    saidas: dict = {}
    for (i, j) in ativos:
        saidas.setdefault(i, []).append(j)

    usados, rotas = set(), []
    for j0 in list(saidas.get(dep, [])):
        if (dep, j0) in usados:
            continue
        rota, cur, prox = [], dep, j0
        while True:
            rota.append((cur, prox))
            usados.add((cur, prox))
            cur = prox
            if cur == dep:
                break
            seguintes = [t for t in saidas.get(cur, []) if (cur, t) not in usados]
            if not seguintes:
                break
            prox = seguintes[0]
        rotas.append(rota)

    return rotas, [a for a in ativos if a not in usados]


# ══════════════════════════════════════════════════════════════════
# Modelo completo
# ══════════════════════════════════════════════════════════════════
def resolver_vrpp(estado: pd.DataFrame, la: ResultadoLookahead, inst: Instancia,
                  cfg: Config, dia_label: str) -> Solucao | None:
    mod, sv = cfg.modelo, cfg.solver
    mg_ini_s, mg_la_s = set(la.mustgo), set(la.mustgo_la)
    mg_all = mg_ini_s | mg_la_s

    ids_rota = [0] + estado['id_contentor'].tolist()
    niveis = [0.0] + estado['nivel_kg'].tolist()
    D, TM = inst.sub(ids_rota)

    N = list(range(len(ids_rota)))
    dep, NR = 0, N[1:]
    id_map = {i: ids_rota[i] for i in N}
    S = {i: float(niveis[i]) for i in N}
    pct = {i: (S[i] / inst.cap.get(ids_rota[i], 1.0) * 100.0) if i > 0 else 0.0 for i in N}
    crit = {i: (ids_rota[i] in mg_all) for i in N}
    tipo_orig = {i: ('MustGo' if ids_rota[i] in mg_ini_s
                     else 'MustGoLA' if ids_rota[i] in mg_la_s else 'Opcional') for i in N}

    # ── 1. ajuste dos MustGo a frota disponivel ────────────────────
    mg_ids, mg_peso, n_rebaixados = _ajustar_mustgo(NR, S, crit, ids_rota, inst, cfg)
    tipo = {i: ('MustGo' if crit[i]
                else 'MustGoLA' if ids_rota[i] in mg_la_s else 'Opcional') for i in N}

    # ── 2. filtragem de arcos ──────────────────────────────────────
    arcos, st = _arcos(N, dep, D, S, cfg, forcados=set(mg_ids))
    arcos_s = set(arcos)
    print(f'\n  Nos totais   : {len(N)}  (deposito + {len(NR)} contentores)')
    print(f'  Arcos totais : {st["n_total"]}  ->  {st["n_arcos"]} apos filtragem  '
          f'({st["pct_mantidos"]}% mantidos)')
    print(f'  KNN={st["knn"]} (bidireccional) | Eliminados por capacidade: {st["n_cap_elim"]}')
    print(f'  MG forcados  : {len(mg_ids)}  |  S total: {sum(S[i] for i in NR):.1f} kg '
          f'(media {sum(S[i] for i in NR)/len(NR):.2f} kg/ponto)')
    print(f'  *** RESTRICAO ACTIVA: k <= {mod.MAX_ROTAS} rotas ***')

    # ── 3. modelo ──────────────────────────────────────────────────
    mdl = gp.Model(f'VRPP_{cfg.etiqueta}')
    mdl.setParam('OutputFlag', sv.output_flag)
    mdl.setParam('MIPGap', mod.MIP_GAP)
    mdl.setParam('TimeLimit', mod.TIME_LIMIT)
    if sv.seed is not None:
        mdl.setParam('Seed', int(sv.seed))
    mdl.Params.MIPFocus = sv.mip_focus
    mdl.Params.Heuristics = sv.heuristics
    mdl.Params.Threads = sv.threads
    mdl.Params.Cuts = sv.cuts
    mdl.Params.Presolve = sv.presolve
    mdl.Params.NodeMethod = sv.node_method

    x = mdl.addVars(arcos, vtype=GRB.BINARY, name='x')
    y = mdl.addVars(arcos, vtype=GRB.CONTINUOUS, lb=0, name='y')   # carga (kg)
    f = mdl.addVars(arcos, vtype=GRB.CONTINUOUS, lb=0, name='f')   # fluxo unitario
    g = mdl.addVars(NR, vtype=GRB.BINARY, name='g')
    k = mdl.addVar(lb=0, ub=mod.MAX_ROTAS, vtype=GRB.INTEGER, name='k')

    mdl.addConstr(k <= mod.MAX_ROTAS, name='max_rotas')

    for i, j in arcos:                                    # ligacao fluxo-arco
        mdl.addConstr(y[i, j] <= mod.Q * x[i, j])
        mdl.addConstr(f[i, j] <= len(NR) * x[i, j])
    for i, j in arcos:                                    # limite inferior (reforco)
        if i != dep:
            mdl.addConstr(y[i, j] >= S[i] * x[i, j])
    for i in NR:                                          # carga recolhida em i
        mdl.addConstr(quicksum(y[i, j] for j in N if (i, j) in arcos_s)
                      - quicksum(y[j, i] for j in N if (j, i) in arcos_s) == S[i] * g[i])
    for j in NR:                                          # veiculos saem vazios
        if (dep, j) in arcos_s:
            mdl.addConstr(y[dep, j] == 0)

    mdl.addConstr(k == quicksum(x[dep, j] for j in NR if (dep, j) in arcos_s), name='k_saidas')
    mdl.addConstr(quicksum(x[j, dep] for j in NR if (j, dep) in arcos_s) == k, name='k_chegadas')

    for i in NR:                                          # MustGo obrigatorios
        if crit[i]:
            mdl.addConstr(g[i] == 1)
    for j in NR:                                          # grau de entrada/saida = g_j
        mdl.addConstr(quicksum(x[i, j] for i in N if (i, j) in arcos_s) == g[j])
        mdl.addConstr(quicksum(x[j, t] for t in N if (j, t) in arcos_s) == g[j])

    mdl.addConstr(quicksum(f[dep, j] for j in NR if (dep, j) in arcos_s)
                  == quicksum(g[j] for j in NR))          # eliminacao de sub-rotas
    for j in NR:
        mdl.addConstr(quicksum(f[i, j] for i in N if (i, j) in arcos_s)
                      - quicksum(f[j, t] for t in N if (j, t) in arcos_s) == g[j])

    mdl.setObjective(mod.R * quicksum(S[i] * g[i] for i in NR)
                     - mod.C * quicksum(x[i, j] * D[i][j] for i, j in arcos)
                     - mod.OMEGA * k, GRB.MAXIMIZE)

    # ── 4. warm start ──────────────────────────────────────────────
    rotas_ws = _warm_start(NR, dep, S, D, cfg, mg_ids, arcos_s)
    ws_arcos = {a for rt in rotas_ws for a in rt}
    ws_nos = {j for rt in rotas_ws for (_, j) in rt if j != dep}
    for i, j in arcos:
        x[i, j].Start = 1.0 if (i, j) in ws_arcos else 0.0
    for i in NR:
        g[i].Start = 1.0 if i in ws_nos else 0.0
    k.Start = float(len(rotas_ws))
    print(f'  Warm start   : {len(rotas_ws)}/{mod.MAX_ROTAS} rotas  |  '
          f'{sum(1 for i in ws_nos if crit[i])}/{len(mg_ids)} MG cobertos')

    # ── 5. optimizacao ─────────────────────────────────────────────
    t0 = time.time()
    mdl.optimize()
    ts = time.time() - t0

    if mdl.SolCount == 0:
        nome = {1: 'LOADED', 2: 'OPTIMAL', 3: 'INVIAVEL', 4: 'INF_OR_UNBD', 5: 'UNBOUNDED',
                9: 'TIME_LIMIT', 11: 'INTERRUPTED'}.get(mdl.status, str(mdl.status))
        print(f'  Sem solucao (status={mdl.status} [{nome}], SolCount=0)')
        if mdl.status == GRB.INFEASIBLE:
            print('  Sugestao: aumentar MAX_ROTAS ou Q, ou relaxar threshold_mg.')
        return None

    # ── 6. extraccao ───────────────────────────────────────────────
    g_val = {i: int(round(g[i].X)) for i in NR}
    ativos = [(i, j) for i, j in arcos if x[i, j].X > 0.5]
    rotas, orfaos = _extrair_rotas(ativos, dep)
    if orfaos:
        print(f'  AVISO: {len(orfaos)} arcos activos nao ligados ao deposito: {orfaos[:5]}')

    kv = int(round(k.X))
    recolhidos = [id_map[j] for rt in rotas for (_, j) in rt if j != dep]
    cont_rota = {id_map[j]: nr for nr, rt in enumerate(rotas, 1) for (_, j) in rt if j != dep}

    print(f'\n  Verificacao de capacidade por rota (Q={mod.Q:g} kg):')
    for nr, rt in enumerate(rotas, 1):
        kg_r = sum(S[j] for (_, j) in rt if j != dep)
        aviso = '  *** EXCEDE Q ***' if kg_r > mod.Q + 1e-3 else ''
        print(f'    Rota {nr}: {kg_r:.1f} kg  ({kg_r/mod.Q*100:.1f}%){aviso}')

    dist_km = sum(D[i][j] * x[i, j].X for i, j in arcos)
    tmin_tot = sum(TM[i][j] * x[i, j].X for i, j in arcos)
    res_tot = sum(S[i] for i in NR if g_val[i])
    n_visit = sum(g_val.values())
    n_ini = sum(1 for i in NR if g_val[i] and tipo[i] == 'MustGo')
    n_la_ = sum(1 for i in NR if g_val[i] and tipo[i] == 'MustGoLA')
    n_opc = sum(1 for i in NR if g_val[i] and tipo[i] == 'Opcional')
    cap_pct = (res_tot / (kv * mod.Q) * 100) if kv > 0 else 0.0

    linhas_nv = [{
        'id_contentor': id_map[i],
        'Tipo_orig': tipo_orig[i],
        'Tipo_solver': tipo[i],
        'Nivel_pct': round(pct[i], 2),
        'Nivel_kg': round(S[i], 3),
        'CAP_CONT_kg': round(inst.cap.get(id_map[i], 0.0), 2),
        'Taxa_dia_pct': round(inst.ai_pct.get(id_map[i], 0.0), 2),
        'Motivo': ('MG_rebaixado_frota_cheia' if (tipo_orig[i] == 'MustGo' and not crit[i])
                   else 'Nao_lucrativo'),
    } for i in NR if not g_val[i]]
    df_nv = (pd.DataFrame(linhas_nv, columns=['id_contentor', 'Tipo_orig', 'Tipo_solver',
                                              'Nivel_pct', 'Nivel_kg', 'CAP_CONT_kg',
                                              'Taxa_dia_pct', 'Motivo'])
             .sort_values(['Motivo', 'Nivel_pct'], ascending=[True, False])
             .reset_index(drop=True))

    kpi = {
        'Instancia': cfg.etiqueta,
        'Dia': dia_label,
        'Veiculos': kv,
        'MAX_ROTAS': mod.MAX_ROTAS,
        'N_Visitados': n_visit,
        'N_MustGo': n_ini,
        'N_MG_rebaixados': n_rebaixados,
        'N_MustGoLA': n_la_,
        'N_Opcionais': n_opc,
        'N_Nao_Visitados': len(df_nv),
        'Total_Residuos_kg': round(res_tot, 2),
        'Distancia_Total_m': round(dist_km * 1000, 1),
        'Distancia_Total_km': round(dist_km, 4),
        'km_por_kg': round(dist_km / res_tot, 6) if res_tot > 0 else 0.0,
        'Tempo_Viagem_min': round(tmin_tot, 2),
        'Capacidade_Usada_pct': round(cap_pct, 2),
        'Receita_euro': round(mod.R * res_tot, 4),
        'Custo_Distancia_euro': round(mod.C * dist_km, 4),
        'Custo_Veiculos_euro': round(mod.OMEGA * kv, 4),
        'Lucro_Liquido_euro': round(mdl.objVal, 4),
        'MIP_Gap_pct': round(mdl.MIPGap * 100, 4),
        'Tempo_Solver_s': round(ts, 2),
        'Tempo_Solver_h': round(ts / 3600, 4),
        'Status_Solver': mdl.status,
    }

    print(f'\n  [{dia_label}] Veiculos:{kv}/{mod.MAX_ROTAS} | Visitados:{n_visit} '
          f'(MG={n_ini} LA={n_la_} Opc={n_opc})')
    if n_rebaixados:
        print(f'  Atencao: {n_rebaixados} MustGo rebaixados p/Opcional (frota insuficiente)')
    print(f'  Residuos:{res_tot:.1f}kg | Dist:{dist_km:.3f}km | Tempo:{tmin_tot:.1f}min '
          f'| Cap:{cap_pct:.1f}%')
    print(f'  Receita:{kpi["Receita_euro"]:.2f}EUR | CustoDist:{kpi["Custo_Distancia_euro"]:.2f}EUR '
          f'| CustoVeic:{kpi["Custo_Veiculos_euro"]:.2f}EUR | Lucro:{kpi["Lucro_Liquido_euro"]:.2f}EUR')
    print(f'  Solver:{ts:.1f}s ({ts/3600:.2f}h)  Gap:{mdl.MIPGap*100:.2f}%')
    print(f'\n  Contentores NAO visitados : {len(df_nv)}')
    if not df_nv.empty:
        print(df_nv.head(20)[['id_contentor', 'Tipo_orig', 'Nivel_pct', 'CAP_CONT_kg', 'Motivo']]
              .to_string(index=False))
        if len(df_nv) > 20:
            print(f'  ... (+{len(df_nv)-20} linhas na folha 7_Nao_Visitados)')

    return Solucao(
        dia=dia_label, rotas=rotas, id_map=id_map, S=S, pct=pct, tipo=tipo, tipo_orig=tipo_orig,
        forcado=crit, g_val=g_val, cont_rota=cont_rota, recolhidos=recolhidos, kpi=kpi,
        diag={**st, 'mg_peso': mg_peso, 'n_mg_forcados': len(mg_ids),
              'n_rebaixados': n_rebaixados, 'orfaos': len(orfaos)},
        df_nao_visitados=df_nv, df_lookahead=la.detalhe, D=D, TM=TM, dep=dep,
    )
