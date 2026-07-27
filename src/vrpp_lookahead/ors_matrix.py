"""Step 1 — Distance/time matrix from OpenRouteService (OSM).

Direct port of `calcular_matriz_ORS.py`: **the logic was not changed**.
The only differences are structural:
  * the global constants became parameters (`ORS` in `config.py`);
  * the API key is no longer in the code and comes from the ORS_API_KEY env var;
  * `main()` was replaced by `build_ors_matrix(cfg)`, called from
    `scripts/01_build_ors_matrix.py`.

Input : Excel with columns ID_bin | Latitude | Longitude (depot with ID_bin = 0).
Output: Excel with 6 sheets — ordered_nodes, distance_km, duration_min,
        distance_model, duration_model, long_format.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import List, Tuple

import pandas as pd
import requests

from .config import ORS, Config

REQUIRED_COLUMNS = {'ID_bin', 'Latitude', 'Longitude'}


# ══════════════════════════════════════════════════════════════════
# Node preparation
# ══════════════════════════════════════════════════════════════════
def normalize_id_bin(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    try:
        number = float(text)
        return str(int(number)) if number.is_integer() else str(number)
    except ValueError:
        return text


def split_into_blocks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def validate_columns(df: pd.DataFrame) -> None:
    df.columns = df.columns.astype(str).str.strip()
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f'Missing columns: {missing}. Columns found: {list(df.columns)}')


def prepare_nodes(df: pd.DataFrame) -> pd.DataFrame:
    """Depot (ID_bin=0) in the first row; the remaining bins after it."""
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()
    df = df[['ID_bin', 'Latitude', 'Longitude']].dropna(subset=['ID_bin', 'Latitude', 'Longitude'])
    df['ID_bin'] = df['ID_bin'].apply(normalize_id_bin)
    df['Latitude'] = pd.to_numeric(df['Latitude'], errors='coerce')
    df['Longitude'] = pd.to_numeric(df['Longitude'], errors='coerce')

    if df[['Latitude', 'Longitude']].isna().any().any():
        raise ValueError('There are empty or non-numeric coordinates.')

    depots = df[df['ID_bin'] == '0'].copy()
    bins = df[df['ID_bin'] != '0'].copy()
    if depots.empty:
        raise ValueError('No depot found with ID_bin = 0.')
    if len(depots) > 1:
        print('Warning: several ID_bin=0 rows; only the first one is used.')
    if bins['ID_bin'].duplicated().any():
        dups = bins.loc[bins['ID_bin'].duplicated(), 'ID_bin'].tolist()
        raise ValueError(f'Duplicated ID_bin among the bins: {dups}')

    df_nodes = pd.concat([depots.iloc[[0]], bins], ignore_index=True)
    df_nodes['Matrix_node'] = df_nodes['ID_bin'].astype(str)
    df_nodes['Model_node'] = range(len(df_nodes))
    df_nodes['Node_type'] = 'bin'
    df_nodes.loc[0, 'Node_type'] = 'depot'
    return df_nodes


def build_ors_coordinates(df: pd.DataFrame) -> List[List[float]]:
    """ORS uses the order [longitude, latitude] (the reverse of Google)."""
    return [[float(r['Longitude']), float(r['Latitude'])] for _, r in df.iterrows()]


# ══════════════════════════════════════════════════════════════════
# Matrix API call
# ══════════════════════════════════════════════════════════════════
def query_ors_matrix(coordinates, source_indices, destination_indices,
                     api_key: str, mode: str = 'driving-car') -> dict:
    """Returns {'durations': [[s]], 'distances': [[km]]} with exponential backoff on 429."""
    url = f'https://api.openrouteservice.org/v2/matrix/{mode}'
    headers = {'Content-Type': 'application/json', 'Authorization': api_key}
    body = {
        'locations': coordinates,
        'sources': source_indices,
        'destinations': destination_indices,
        'metrics': ['distance', 'duration'],
        'units': 'km',
    }

    initial_wait, max_attempts = 3, 6
    for attempt in range(max_attempts):
        resp = requests.post(url, headers=headers, json=body, timeout=120)
        if resp.status_code == 429:
            wait = initial_wait * (2 ** attempt)
            print(f'  Rate limit (429). Waiting {wait}s... ({attempt+1}/{max_attempts})')
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            raise RuntimeError(f'HTTP error {resp.status_code} in the ORS Matrix API:\n{resp.text}')
        break
    else:
        raise RuntimeError('Retries exhausted after error 429.')

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f'Response is not valid JSON:\n{resp.text}')
    if 'error' in data:
        raise RuntimeError(f'ORS error: {data["error"]}')
    return data


def build_distance_matrix(df_nodes: pd.DataFrame, ors: ORS) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """N x N matrix, one request per block of sources.

    Block size = floor(max_routes_per_request / n), which guarantees
    block x n <= the server limit for any number of nodes.
    """
    ids = df_nodes['Matrix_node'].astype(str).tolist()
    n = len(ids)
    coords = build_ors_coordinates(df_nodes)

    if ors.manual_block is not None:
        block = int(ors.manual_block)
        if block * n > ors.max_routes_per_request:
            raise ValueError(
                f'manual_block={block} x n={n} = {block*n} exceeds the ORS limit of '
                f'{ors.max_routes_per_request} routes/request. Reduce it to '
                f'{ors.max_routes_per_request // n} or less.')
    else:
        block = max(1, ors.max_routes_per_request // n)

    all_destinations = list(range(n))
    total_blocks = math.ceil(n / block)

    print(f'Nodes in matrix: {n}  |  Depot: {ids[0]}  |  Mode: {ors.transport_mode}')
    print(f'ORS limit: {ors.max_routes_per_request} routes/request')
    print(f'Source block: {block}  ->  {block}x{n}={block*n} routes/request')
    print(f'Requests needed: {total_blocks}')
    print(f'Estimated time: ~{total_blocks * ors.pause_s / 60:.0f} min\n')

    mat_dist = pd.DataFrame(index=ids, columns=ids, dtype=float)
    mat_dur = pd.DataFrame(index=ids, columns=ids, dtype=float)
    nulls = []

    api_key = ors.api_key()
    for block_no, source_block in enumerate(split_into_blocks(all_destinations, block), start=1):
        print(f'  Block {block_no}/{total_blocks}: sources '
              f'{source_block[0]}..{source_block[-1]} x {n} destinations '
              f'({len(source_block)*n} routes)...')

        data = query_ors_matrix(coords, source_block, all_destinations,
                                api_key, ors.transport_mode)
        distances, durations = data.get('distances'), data.get('durations')
        if distances is None or durations is None:
            raise RuntimeError(f"ORS response without 'distances'/'durations': {data}")
        if len(distances) != len(source_block):
            raise RuntimeError(f'Block {block_no}: ORS returned {len(distances)} rows, '
                               f'{len(source_block)} expected.')
        for i_local, row in enumerate(distances):
            if len(row) != n:
                raise RuntimeError(f'Block {block_no}, row {i_local}: {len(row)} columns, '
                                   f'{n} expected.')

        for i_local, i_global in enumerate(source_block):
            id_src = ids[i_global]
            for j_global in all_destinations:
                id_dst = ids[j_global]
                dist_km = distances[i_local][j_global]
                dur_s = durations[i_local][j_global]
                if i_global == j_global:
                    mat_dist.loc[id_src, id_dst] = 0.0
                    mat_dur.loc[id_src, id_dst] = 0.0
                elif dist_km is None or dur_s is None:
                    nulls.append((id_src, id_dst))
                    mat_dist.loc[id_src, id_dst] = float('nan')
                    mat_dur.loc[id_src, id_dst] = float('nan')
                else:
                    mat_dist.loc[id_src, id_dst] = round(float(dist_km), 4)
                    mat_dur.loc[id_src, id_dst] = round(float(dur_s) / 60.0, 4)

        time.sleep(ors.pause_s)

    nan_total = int(mat_dist.isna().sum().sum())
    if nan_total == 0:
        print('\nMatrix complete: 0 NaN values.')
    else:
        affected = sorted({o for o, _ in nulls} | {d for _, d in nulls})
        print(f'\nWARNING: {nan_total} cells without a route (NaN).')
        print(f'  Affected nodes ({len(affected)}): {affected[:20]}'
              f'{"..." if len(affected) > 20 else ""}')
        print('  Possible cause: coordinates off the road network or isolated nodes.')

    return mat_dist, mat_dur


# ══════════════════════════════════════════════════════════════════
# Formatting and writing
# ══════════════════════════════════════════════════════════════════
def to_long_format(mat_dist: pd.DataFrame, mat_dur: pd.DataFrame) -> pd.DataFrame:
    records = [{'source': o, 'destination': d,
                'distance_km': mat_dist.loc[o, d], 'duration_min': mat_dur.loc[o, d]}
               for o in mat_dist.index for d in mat_dist.columns]
    return pd.DataFrame(records)


def build_model_matrices(mat_dist, mat_dur, df_nodes) -> Tuple[pd.DataFrame, pd.DataFrame]:
    mapping = dict(zip(df_nodes['Matrix_node'], df_nodes['Model_node']))

    def reindex(df):
        df = df.copy()
        df.index = [mapping[x] for x in df.index]
        df.columns = [mapping[x] for x in df.columns]
        return df

    return reindex(mat_dist), reindex(mat_dur)


def write_ors_workbook(destination: Path, df_nodes, mat_dist, mat_dur,
                       mat_dist_model, mat_dur_model, long_format) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(destination, engine='openpyxl') as w:
        df_nodes.to_excel(w, sheet_name='ordered_nodes', index=False)
        mat_dist.to_excel(w, sheet_name='distance_km')
        mat_dur.to_excel(w, sheet_name='duration_min')
        mat_dist_model.to_excel(w, sheet_name='distance_model')
        mat_dur_model.to_excel(w, sheet_name='duration_model')
        long_format.to_excel(w, sheet_name='long_format', index=False)
        for sn in ('distance_km', 'duration_min', 'distance_model', 'duration_model'):
            ws = w.sheets[sn]
            for row in ws.iter_rows(min_row=2, min_col=2):
                for cell in row:
                    if cell.value is not None:
                        cell.number_format = '0.0000'


def build_ors_matrix(cfg: Config) -> Path:
    """Full step-1 pipeline. Returns the path of the generated Excel file."""
    source = cfg.path('coordinates')
    destination = cfg.path('ors_matrix', create_dir=True)

    print(f'Reading coordinates: {source}')
    df = pd.read_excel(source, sheet_name=cfg.ors.excel_sheet)
    df.columns = df.columns.astype(str).str.strip()
    validate_columns(df)
    print(df.head().to_string(), '\n')

    print('Preparing nodes...')
    df_nodes = prepare_nodes(df)
    print(f'  {len(df_nodes)} nodes (1 depot + {len(df_nodes)-1} bins)\n')

    print('Generating matrices with OpenRouteService (OpenStreetMap)...')
    mat_dist, mat_dur = build_distance_matrix(df_nodes, cfg.ors)

    print('\nConverting to long format...')
    long_format = to_long_format(mat_dist, mat_dur)
    mat_dist_model, mat_dur_model = build_model_matrices(mat_dist, mat_dur, df_nodes)

    print(f'Saving: {destination}')
    write_ors_workbook(destination, df_nodes, mat_dist, mat_dur,
                       mat_dist_model, mat_dur_model, long_format)
    print('Process finished successfully.')
    return destination
