"""Step 3a — Lookahead: MustGo / MustGoLA classification.

    MustGo   : level_kg + ai_kg      >= THRESHOLD_MG/100       * CAP_CONT_i
    MustGoLA : level_kg + ai_kg * k  >= THRESHOLD_OVERFLOW/100 * CAP_CONT_i,  k = 2..window

Pure function: (state, instance, config) -> LookaheadResult.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .instance import Instance


@dataclass
class LookaheadResult:
    mustgo: list
    mustgo_la: list
    detail: pd.DataFrame

    @property
    def all_ids(self) -> set:
        return set(self.mustgo) | set(self.mustgo_la)


def lookahead(state: pd.DataFrame, inst: Instance, cfg: Config,
              verbose: bool = True) -> LookaheadResult:
    la = cfg.lookahead
    df = state.merge(inst.bins[['id_contentor', 'ai', 'ai_kg', 'CAP_CONT']],
                     on='id_contentor', how='left')
    assert df['CAP_CONT'].notna().all(), 'state contains ids missing from the instance'

    thr = la.threshold_mg / 100.0 * df['CAP_CONT']
    ovf = la.threshold_overflow / 100.0 * df['CAP_CONT']

    df['Current_Level_pct'] = df['level_kg'] / df['CAP_CONT'] * 100.0
    df['fc_d1_kg'] = df['level_kg'] + df['ai_kg']
    df['fc_d1_pct'] = df['fc_d1_kg'] / df['CAP_CONT'] * 100.0

    is_mg = (df['fc_d1_kg'] >= thr).to_numpy()
    if la.window >= 2:
        future = np.any([(df['level_kg'] + df['ai_kg'] * k) >= ovf
                         for k in range(2, la.window + 1)], axis=0)
    else:
        future = np.zeros(len(df), dtype=bool)
    is_la = ~is_mg & future

    mustgo = df.loc[is_mg, 'id_contentor'].tolist()
    mustgo_la = df.loc[is_la, 'id_contentor'].tolist()
    df['Group'] = np.where(is_mg, 'MustGo', np.where(is_la, 'MustGoLA', 'Not_MG'))

    if verbose:
        n_overflow = int((df['level_kg'] >= df['CAP_CONT']).sum())
        print(f'  Overflowing NOW (level>=CAP_CONT_i)  : {n_overflow}')
        print(f'  MustGo  (overflowing tomorrow)       : {len(mustgo)}')
        print(f'  MustGoLA ({la.window}-day window)              : {len(mustgo_la)}')
        print(f'  Total MG                             : {len(mustgo) + len(mustgo_la)}')

    detail = df[['id_contentor', 'CAP_CONT', 'Current_Level_pct', 'level_kg',
                 'ai', 'ai_kg', 'fc_d1_pct', 'fc_d1_kg', 'Group']].rename(columns={
        'level_kg': 'Current_Level_kg',
        'ai': 'Daily_Rate_pct',
        'ai_kg': 'Daily_Rate_kg',
        'fc_d1_pct': 'Forecast_tomorrow_pct',
        'fc_d1_kg': 'Forecast_tomorrow_kg',
    }).copy()

    return LookaheadResult(mustgo=mustgo, mustgo_la=mustgo_la, detail=detail)
