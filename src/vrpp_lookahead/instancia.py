"""Passo 2 — Instancia do VRPP: construcao e carregamento.

O livro de instancia tem sempre 4 folhas (formato usado por todos os cenarios):

    contentores       id_contentor | Si | ai | Vol_cont | Vol_kg | Ncont
    LatLong           id_contentor | Latitude | Longitude          (inclui o deposito, id=0)
    matrizdistancias  matriz N x N em km   (indice/colunas = ids, deposito na 1a posicao)
    matrizmin         matriz N x N em min

Grandezas derivadas (definidas UMA unica vez em todo o projecto):

    CAP_CONT_i = B * Ncont_i * Vol_cont_i     capacidade do ponto (kg)
    Si_kg_i    = Vol_kg_i                     nivel actual (kg, lido do Excel)
    ai_kg_i    = ai_i / 100 * CAP_CONT_i      acumulacao diaria (kg)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config

FOLHAS_INSTANCIA = ('contentores', 'LatLong', 'matrizdistancias', 'matrizmin')
COLS_CONTENTORES = ('id_contentor', 'ai', 'Vol_cont', 'Vol_kg', 'Ncont')


# ══════════════════════════════════════════════════════════════════
# 2.1 — Construcao do livro de instancia
# ══════════════════════════════════════════════════════════════════
def construir_instancia(cfg: Config, folha_dist: str = 'distancia_km',
                        folha_dur: str = 'duracion_min') -> Path:
    """Combina atributos + coordenadas + matriz ORS num livro de 4 folhas.

    A ordem dos nos e a da matriz ORS (deposito na primeira posicao), o que
    garante `matrizdistancias.index[1:] == contentores.id_contentor`.
    """
    p_attr = cfg.ruta('atributos')
    p_coord = cfg.ruta('coordenadas')
    p_ors = cfg.ruta('matriz_ors')
    destino = cfg.ruta('instancia', criar_pasta=True)

    attr = pd.read_excel(p_attr)
    attr.columns = attr.columns.astype(str).str.strip()
    if 'ID_bin' in attr.columns and 'id_contentor' not in attr.columns:
        attr = attr.rename(columns={'ID_bin': 'id_contentor'})
    for col in COLS_CONTENTORES:
        assert col in attr.columns, f'Coluna "{col}" ausente em {p_attr.name}'
    attr['id_contentor'] = attr['id_contentor'].astype(int)

    coord = pd.read_excel(p_coord)
    coord.columns = coord.columns.astype(str).str.strip()
    if 'ID_bin' in coord.columns:
        coord = coord.rename(columns={'ID_bin': 'id_contentor'})
    coord = coord[['id_contentor', 'Latitude', 'Longitude']].copy()
    coord['id_contentor'] = coord['id_contentor'].astype(int)

    dist = pd.read_excel(p_ors, sheet_name=folha_dist, index_col=0)
    dur = pd.read_excel(p_ors, sheet_name=folha_dur, index_col=0)
    for df in (dist, dur):
        df.index = df.index.astype(int)
        df.columns = df.columns.astype(int)

    ids = list(dist.index)
    assert ids[0] == 0, 'O deposito (id=0) deve ser o primeiro no da matriz ORS'
    assert list(dist.columns) == ids, 'matriz ORS: indice e colunas com ordens diferentes'
    assert list(dur.index) == ids and list(dur.columns) == ids, \
        'duracion_min com ordem diferente de distancia_km'

    ids_cont = ids[1:]
    faltam_attr = set(ids_cont) - set(attr['id_contentor'])
    assert not faltam_attr, f'{len(faltam_attr)} ids sem atributos: {sorted(faltam_attr)[:10]}'
    faltam_coord = set(ids) - set(coord['id_contentor'])
    assert not faltam_coord, f'{len(faltam_coord)} ids sem coordenadas: {sorted(faltam_coord)[:10]}'

    contentores = (attr.set_index('id_contentor').loc[ids_cont].reset_index())
    latlong = (coord.drop_duplicates('id_contentor').set_index('id_contentor')
               .loc[ids].reset_index())

    nan_dist = int(np.sum(~np.isfinite(dist.to_numpy(dtype=float))))
    if nan_dist:
        print(f'  AVISO: {nan_dist} celulas NaN na matriz de distancias — '
              f'o passo 3 vai recusar a instancia.')

    with pd.ExcelWriter(destino, engine='openpyxl') as w:
        contentores.to_excel(w, sheet_name='contentores', index=False)
        latlong.to_excel(w, sheet_name='LatLong', index=False)
        dist.to_excel(w, sheet_name='matrizdistancias')
        dur.to_excel(w, sheet_name='matrizmin')

    print(f'Instancia criada: {destino}')
    print(f'  Nos        : {len(ids)} (deposito + {len(ids_cont)} contentores)')
    print(f'  Atributos  : {p_attr.name}')
    print(f'  Coordenadas: {p_coord.name}')
    print(f'  Matriz ORS : {p_ors.name}  (folhas {folha_dist} / {folha_dur})')
    return destino


# ══════════════════════════════════════════════════════════════════
# 2.2 — Carregamento + validacao
# ══════════════════════════════════════════════════════════════════
@dataclass
class Instancia:
    cont: pd.DataFrame          # uma linha por ponto de recolha (sem o deposito)
    ids: list                   # ordem dos nos; ids[0] == 0 == deposito
    dist: np.ndarray            # km
    tmin: np.ndarray            # min
    latlon: dict
    cap: dict                   # id -> CAP_CONT (kg)
    ai_kg: dict                 # id -> acumulacao diaria (kg)
    ai_pct: dict                # id -> taxa diaria (%)
    etiqueta: str = ''
    _pos: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._pos = {c: p for p, c in enumerate(self.ids)}

    @property
    def n(self) -> int:
        return len(self.cont)

    def sub(self, ids_sel: list):
        """Submatrizes (dist, tmin) na ordem de `ids_sel`."""
        idx = [self._pos[i] for i in ids_sel]
        bloco = np.ix_(idx, idx)
        return self.dist[bloco], self.tmin[bloco]

    def estado_inicial(self) -> pd.DataFrame:
        return self.cont[['id_contentor', 'Si_kg']].rename(columns={'Si_kg': 'nivel_kg'}).copy()


def carregar_instancia(cfg: Config) -> Instancia:
    caminho = cfg.ruta('instancia')
    assert caminho.exists(), (f'Instancia nao encontrada: {caminho}\n'
                             f'Corra primeiro scripts/02_construir_instancia.py')

    disponiveis = pd.ExcelFile(caminho).sheet_names
    for folha in FOLHAS_INSTANCIA:
        assert folha in disponiveis, f'Folha "{folha}" ausente em {caminho.name} ({disponiveis})'

    cont = pd.read_excel(caminho, sheet_name='contentores')
    latlong = pd.read_excel(caminho, sheet_name='LatLong')
    dists = pd.read_excel(caminho, sheet_name='matrizdistancias', index_col=0)
    mins = pd.read_excel(caminho, sheet_name='matrizmin', index_col=0)

    for col in COLS_CONTENTORES:
        assert col in cont.columns, f'Coluna "{col}" ausente na folha contentores'

    cont['id_contentor'] = cont['id_contentor'].astype(int)
    latlong['id_contentor'] = latlong['id_contentor'].astype(int)
    for df in (dists, mins):
        df.index = df.index.astype(int)
        df.columns = df.columns.astype(int)

    dup = int(cont['id_contentor'].duplicated().sum())
    assert dup == 0, f'{dup} id_contentor duplicados na folha contentores'

    ids = list(dists.index)
    assert ids[0] == 0, 'O deposito (id=0) deve ser a primeira linha da matrizdistancias'
    assert list(dists.columns) == ids, 'matrizdistancias: indice e colunas com ordens diferentes'
    assert list(mins.index) == ids and list(mins.columns) == ids, \
        'matrizmin com indices diferentes de matrizdistancias'

    faltam_ll = set(ids) - set(latlong['id_contentor'])
    assert not faltam_ll, f'{len(faltam_ll)} nos sem coordenadas: {sorted(faltam_ll)[:5]}'
    inconsistentes = (set(ids) - {0}) ^ set(cont['id_contentor'])
    assert not inconsistentes, \
        f'{len(inconsistentes)} ids inconsistentes entre matriz e contentores'

    dist = dists.to_numpy(dtype=float)
    n_nan = int(np.sum(~np.isfinite(dist)))
    assert n_nan == 0, f'matrizdistancias com {n_nan} entradas NaN/Inf'

    bruto = mins.to_numpy(dtype=float)
    tmin = np.where(np.isfinite(bruto), bruto, 0.0)

    B = cfg.modelo.B
    cont['CAP_CONT'] = B * cont['Ncont'] * cont['Vol_cont'].astype(float)
    cont['Si_kg'] = cont['Vol_kg'].astype(float)
    cont['ai_kg'] = (cont['ai'] / 100.0) * cont['CAP_CONT']
    assert (cont['CAP_CONT'] > 0).all(), 'CAP_CONT <= 0 (Ncont ou Vol_cont nulos)'

    # alinhar a ordem dos contentores a das matrizes -> indexacao previsivel
    cont = cont.set_index('id_contentor').loc[ids[1:]].reset_index()

    return Instancia(
        cont=cont,
        ids=ids,
        dist=dist,
        tmin=tmin,
        latlon=latlong.set_index('id_contentor')[['Latitude', 'Longitude']].to_dict('index'),
        cap=cont.set_index('id_contentor')['CAP_CONT'].to_dict(),
        ai_kg=cont.set_index('id_contentor')['ai_kg'].to_dict(),
        ai_pct=cont.set_index('id_contentor')['ai'].to_dict(),
        etiqueta=cfg.etiqueta,
    )


# ══════════════════════════════════════════════════════════════════
# 2.3 — Diagnostico (antes de optimizar)
# ══════════════════════════════════════════════════════════════════
def resumo_instancia(inst: Instancia) -> None:
    c = inst.cont
    print(f'Contentores : {inst.n}   (nos totais: {len(inst.ids)} = deposito + {inst.n})')
    print(f'Deposito    : Lat={inst.latlon[0]["Latitude"]:.6f}  Lon={inst.latlon[0]["Longitude"]:.6f}')
    print(f'Distancias  : media {inst.dist[inst.dist > 0].mean():.2f} km | max {inst.dist.max():.2f} km')
    print(f'CAP_CONT    : min {c["CAP_CONT"].min():.1f} | media {c["CAP_CONT"].mean():.1f} '
          f'| max {c["CAP_CONT"].max():.1f} kg | TOTAL {c["CAP_CONT"].sum():.2f} kg')
    print(f'Si_kg       : min {c["Si_kg"].min():.2f} | media {c["Si_kg"].mean():.2f} '
          f'| max {c["Si_kg"].max():.2f} kg | TOTAL {c["Si_kg"].sum():.2f} kg')
    print(f'ai_kg       : min {c["ai_kg"].min():.2f} | media {c["ai_kg"].mean():.2f} '
          f'| max {c["ai_kg"].max():.2f} kg/dia | TOTAL {c["ai_kg"].sum():.2f} kg/dia')


def diagnosticar(inst: Instancia, cfg: Config) -> pd.DataFrame:
    """Classifica os pontos no dia 1 e verifica se a frota chega para os MustGo."""
    la, mod = cfg.lookahead, cfg.modelo
    d = inst.cont.copy()
    thr = la.threshold_mg / 100.0 * d['CAP_CONT']
    ovf = la.threshold_overflow / 100.0 * d['CAP_CONT']

    d['mg'] = (d['Si_kg'] + d['ai_kg']) >= thr
    if la.janela >= 2:
        futuros = np.any([(d['Si_kg'] + d['ai_kg'] * k) >= ovf
                          for k in range(2, la.janela + 1)], axis=0)
    else:
        futuros = np.zeros(len(d), dtype=bool)
    d['mgla'] = ~d['mg'] & futuros
    d['vazio'] = ~d['mg'] & ~d['mgla'] & (d['Si_kg'] / d['CAP_CONT'] * 100 < la.nivel_bloqueio_pct)
    d['opc'] = ~d['mg'] & ~d['mgla'] & ~d['vazio']

    total = float(d['Si_kg'].sum())
    resumo = pd.DataFrame([{
        'Grupo': nome,
        'N_pontos': int(mask.sum()),
        'Si_kg': round(float(d.loc[mask, 'Si_kg'].sum()), 2),
        'Pct_Si_total': round(float(d.loc[mask, 'Si_kg'].sum()) / total * 100, 2) if total else 0.0,
    } for nome, mask in (('MustGo', d['mg']), ('MustGoLA', d['mgla']),
                         ('Opcional', d['opc']), ('Quase vazio', d['vazio']))])

    peso_mg = float(d.loc[d['mg'], 'Si_kg'].sum())
    rotas_min = math.ceil(peso_mg / mod.Q) if peso_mg > 0 else 0

    print(resumo.to_string(index=False))
    print(f'\nSi_kg total    : {total:.2f} kg  '
          f'({total / d["CAP_CONT"].sum() * 100:.1f}% da CAP_CONT total)')
    print(f'CAP_CONT total : {d["CAP_CONT"].sum():.2f} kg')
    print(f'Peso MustGo    : {peso_mg:.2f} kg  ->  minimo de {rotas_min} rota(s) de {mod.Q:g} kg')
    print(f'MAX_ROTAS      : {mod.MAX_ROTAS}  (capacidade {mod.cap_frota_kg:g} kg)')
    if rotas_min > mod.MAX_ROTAS:
        print(f'  !!! MAX_ROTAS insuficiente: havera MustGo rebaixados p/Opcional. '
              f'Sugestao: MAX_ROTAS={rotas_min}')
    else:
        print(f'  OK — folga de {mod.cap_frota_kg - peso_mg:.2f} kg sobre os MustGo')
    return resumo
