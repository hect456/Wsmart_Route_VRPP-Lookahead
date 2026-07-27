"""Passo 3 — Ciclo de simulacao (lookahead -> VRPP -> actualizacao do estado).

Estado do dia seguinte:
    recolhidos -> 0 kg
    restantes  -> min(CAP_CONT_i, nivel_i + ai_kg_i)
"""
from __future__ import annotations

import pandas as pd

from .config import Config
from .instancia import Instancia
from .lookahead import lookahead
from .reporting import exportar_excel, exportar_resumo, plotar_rotas
from .vrpp import resolver_vrpp


def simular(inst: Instancia, cfg: Config) -> list:
    pasta_base = cfg.ruta('resultados', criar_pasta=True)
    pasta_base.mkdir(parents=True, exist_ok=True)
    estado = inst.estado_inicial()
    kpis = []

    for dia in range(1, cfg.lookahead.dias + 1):
        rotulo = f'Dia_{dia:02d}'
        pasta = pasta_base / rotulo

        print(f'\n{"="*64}\n  {rotulo} — LOOKAHEAD (janela={cfg.lookahead.janela} dias)\n{"="*64}')
        la = lookahead(estado, inst, cfg)

        print(f'\n{"="*64}\n  {rotulo} — VRPP\n{"="*64}')
        recolhidos: set = set()
        if la.todos:
            sol = resolver_vrpp(estado, la, inst, cfg, rotulo)
            if sol is not None:
                if cfg.solver.gerar_mapas:
                    plotar_rotas(sol, inst, pasta)
                exportar_excel(sol, inst, cfg, pasta)
                kpis.append(sol.kpi)
                recolhidos = set(sol.recolhidos)
        else:
            print('  Nenhum contentor MG — sem recolha hoje.')

        niveis = estado['nivel_kg'].to_numpy(dtype=float).copy()
        for pos, cid in enumerate(estado['id_contentor']):
            if cid in recolhidos:
                niveis[pos] = 0.0
            else:
                niveis[pos] = min(inst.cap.get(cid, float('inf')),
                                  niveis[pos] + inst.ai_kg.get(cid, 0.0))
        estado['nivel_kg'] = niveis

    print('\nSimulacao concluida.')
    return kpis


def correr(cfg: Config, diagnostico: pd.DataFrame | None = None) -> list:
    """Carrega a instancia, simula e escreve o resumo consolidado."""
    from .instancia import carregar_instancia, diagnosticar, resumo_instancia

    cfg.resumo()
    print()
    inst = carregar_instancia(cfg)
    resumo_instancia(inst)
    print()
    diag = diagnostico if diagnostico is not None else diagnosticar(inst, cfg)
    kpis = simular(inst, cfg)
    exportar_resumo(kpis, cfg, diag)
    return kpis
