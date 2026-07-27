"""Passo 3a — Lookahead: classificacao MustGo / MustGoLA.

    MustGo   : nivel_kg + ai_kg      >= THRESHOLD_MG/100       * CAP_CONT_i
    MustGoLA : nivel_kg + ai_kg * k  >= THRESHOLD_OVERFLOW/100 * CAP_CONT_i,  k = 2..janela

Funcao pura: (estado, instancia, config) -> ResultadoLookahead.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .instancia import Instancia


@dataclass
class ResultadoLookahead:
    mustgo: list
    mustgo_la: list
    detalhe: pd.DataFrame

    @property
    def todos(self) -> set:
        return set(self.mustgo) | set(self.mustgo_la)


def lookahead(estado: pd.DataFrame, inst: Instancia, cfg: Config,
              verboso: bool = True) -> ResultadoLookahead:
    la = cfg.lookahead
    df = estado.merge(inst.cont[['id_contentor', 'ai', 'ai_kg', 'CAP_CONT']],
                      on='id_contentor', how='left')
    assert df['CAP_CONT'].notna().all(), 'estado com ids ausentes na instancia'

    thr = la.threshold_mg / 100.0 * df['CAP_CONT']
    ovf = la.threshold_overflow / 100.0 * df['CAP_CONT']

    df['Nivel_Atual_pct'] = df['nivel_kg'] / df['CAP_CONT'] * 100.0
    df['prev_d1_kg'] = df['nivel_kg'] + df['ai_kg']
    df['prev_d1_pct'] = df['prev_d1_kg'] / df['CAP_CONT'] * 100.0

    e_mg = (df['prev_d1_kg'] >= thr).to_numpy()
    if la.janela >= 2:
        futuros = np.any([(df['nivel_kg'] + df['ai_kg'] * k) >= ovf
                          for k in range(2, la.janela + 1)], axis=0)
    else:
        futuros = np.zeros(len(df), dtype=bool)
    e_la = ~e_mg & futuros

    mustgo = df.loc[e_mg, 'id_contentor'].tolist()
    mustgo_la = df.loc[e_la, 'id_contentor'].tolist()
    df['Grupo'] = np.where(e_mg, 'MustGo', np.where(e_la, 'MustGoLA', 'Nao_MG'))

    if verboso:
        n_overflow = int((df['nivel_kg'] >= df['CAP_CONT']).sum())
        print(f'  Em overflow AGORA (nivel>=CAP_CONT_i): {n_overflow}')
        print(f'  MustGo  (overflow amanha)             : {len(mustgo)}')
        print(f'  MustGoLA (janela {la.janela} dias)              : {len(mustgo_la)}')
        print(f'  Total MG                              : {len(mustgo) + len(mustgo_la)}')

    detalhe = df[['id_contentor', 'CAP_CONT', 'Nivel_Atual_pct', 'nivel_kg',
                  'ai', 'ai_kg', 'prev_d1_pct', 'prev_d1_kg', 'Grupo']].rename(columns={
        'nivel_kg': 'Nivel_Atual_kg',
        'ai': 'Taxa_dia_pct',
        'ai_kg': 'Taxa_dia_kg',
        'prev_d1_pct': 'Previsto_amanha_pct',
        'prev_d1_kg': 'Previsto_amanha_kg',
    }).copy()

    return ResultadoLookahead(mustgo=mustgo, mustgo_la=mustgo_la, detalhe=detalhe)
