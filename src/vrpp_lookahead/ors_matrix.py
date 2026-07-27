"""Passo 1 — Matriz de distancias/tempos com OpenRouteService (OSM).

Porte directo de `calcular_matriz_ORS.py`: **a logica nao foi alterada**.
As unicas mudancas sao estruturais:
  * as constantes globais passaram a parametros (`ORS` em `config.py`);
  * a API key deixou de estar no codigo e vem da variavel de ambiente ORS_API_KEY;
  * `main()` foi substituido por `gerar_matriz_ors(cfg)`, chamado por
    `scripts/01_gerar_matriz_ors.py`.

Entrada : Excel com colunas ID_bin | Latitude | Longitude (deposito com ID_bin = 0).
Saida   : Excel com 6 folhas — nodos_ordenados, distancia_km, duracion_min,
          distancia_modelo, duracion_modelo, formato_largo.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import List, Tuple

import pandas as pd
import requests

from .config import ORS, Config

COLUNAS_REQUERIDAS = {'ID_bin', 'Latitude', 'Longitude'}


# ══════════════════════════════════════════════════════════════════
# Preparacao dos nos
# ══════════════════════════════════════════════════════════════════
def normalizar_id_bin(valor):
    if pd.isna(valor):
        return None
    texto = str(valor).strip()
    try:
        numero = float(texto)
        return str(int(numero)) if numero.is_integer() else str(numero)
    except ValueError:
        return texto


def dividir_en_bloques(lista: list, tamano: int):
    for i in range(0, len(lista), tamano):
        yield lista[i:i + tamano]


def validar_columnas(df: pd.DataFrame) -> None:
    df.columns = df.columns.astype(str).str.strip()
    faltantes = COLUNAS_REQUERIDAS - set(df.columns)
    if faltantes:
        raise ValueError(f'Faltan columnas: {faltantes}. Columnas encontradas: {list(df.columns)}')


def preparar_nodos(df: pd.DataFrame) -> pd.DataFrame:
    """Deposito (ID_bin=0) na primeira linha; restantes contentores a seguir."""
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()
    df = df[['ID_bin', 'Latitude', 'Longitude']].dropna(subset=['ID_bin', 'Latitude', 'Longitude'])
    df['ID_bin'] = df['ID_bin'].apply(normalizar_id_bin)
    df['Latitude'] = pd.to_numeric(df['Latitude'], errors='coerce')
    df['Longitude'] = pd.to_numeric(df['Longitude'], errors='coerce')

    if df[['Latitude', 'Longitude']].isna().any().any():
        raise ValueError('Existen coordenadas vacias o no numericas.')

    depositos = df[df['ID_bin'] == '0'].copy()
    contenedores = df[df['ID_bin'] != '0'].copy()
    if depositos.empty:
        raise ValueError('No se encontro deposito con ID_bin = 0.')
    if len(depositos) > 1:
        print('Aviso: varios ID_bin=0; se usa solo el primero.')
    if contenedores['ID_bin'].duplicated().any():
        dups = contenedores.loc[contenedores['ID_bin'].duplicated(), 'ID_bin'].tolist()
        raise ValueError(f'ID_bin duplicados en contenedores: {dups}')

    df_nodos = pd.concat([depositos.iloc[[0]], contenedores], ignore_index=True)
    df_nodos['Nodo_matriz'] = df_nodos['ID_bin'].astype(str)
    df_nodos['Nodo_modelo'] = range(len(df_nodos))
    df_nodos['Tipo_nodo'] = 'contenedor'
    df_nodos.loc[0, 'Tipo_nodo'] = 'deposito'
    return df_nodos


def construir_coordenadas_ors(df: pd.DataFrame) -> List[List[float]]:
    """ORS usa a ordem [longitude, latitude] (inversa da Google)."""
    return [[float(r['Longitude']), float(r['Latitude'])] for _, r in df.iterrows()]


# ══════════════════════════════════════════════════════════════════
# Chamada a Matrix API
# ══════════════════════════════════════════════════════════════════
def consultar_ors_matrix(coordenadas, indices_origenes, indices_destinos,
                         api_key: str, modo: str = 'driving-car') -> dict:
    """Devolve {'durations': [[s]], 'distances': [[km]]} com recuo exponencial em 429."""
    url = f'https://api.openrouteservice.org/v2/matrix/{modo}'
    headers = {'Content-Type': 'application/json', 'Authorization': api_key}
    body = {
        'locations': coordenadas,
        'sources': indices_origenes,
        'destinations': indices_destinos,
        'metrics': ['distance', 'duration'],
        'units': 'km',
    }

    espera_inicial, intentos_maximos = 3, 6
    for intento in range(intentos_maximos):
        resp = requests.post(url, headers=headers, json=body, timeout=120)
        if resp.status_code == 429:
            espera = espera_inicial * (2 ** intento)
            print(f'  Rate-limit (429). Esperando {espera}s... ({intento+1}/{intentos_maximos})')
            time.sleep(espera)
            continue
        if resp.status_code != 200:
            raise RuntimeError(f'Error HTTP {resp.status_code} en ORS Matrix API:\n{resp.text}')
        break
    else:
        raise RuntimeError('Reintentos agotados tras error 429.')

    try:
        datos = resp.json()
    except ValueError:
        raise RuntimeError(f'Respuesta no es JSON valido:\n{resp.text}')
    if 'error' in datos:
        raise RuntimeError(f'Error ORS: {datos["error"]}')
    return datos


def generar_matriz_distancias(df_nodos: pd.DataFrame, ors: ORS) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Matriz N x N por blocos de origens.

    Tamanho do bloco = floor(max_routes_per_request / n), garantindo
    bloco x n <= limite do servidor para qualquer numero de nos.
    """
    ids = df_nodos['Nodo_matriz'].astype(str).tolist()
    n = len(ids)
    coords = construir_coordenadas_ors(df_nodos)

    if ors.bloque_manual is not None:
        bloco = int(ors.bloque_manual)
        if bloco * n > ors.max_routes_per_request:
            raise ValueError(
                f'bloque_manual={bloco} x n={n} = {bloco*n} excede o limite ORS de '
                f'{ors.max_routes_per_request} rotas/pedido. Reduza para '
                f'{ors.max_routes_per_request // n} ou menos.')
    else:
        bloco = max(1, ors.max_routes_per_request // n)

    todos_destinos = list(range(n))
    total_bloques = math.ceil(n / bloco)

    print(f'Nodos en matriz: {n}  |  Deposito: {ids[0]}  |  Modo: {ors.modo_transporte}')
    print(f'Limite ORS: {ors.max_routes_per_request} rutas/peticion')
    print(f'Bloque de origenes: {bloco}  ->  {bloco}x{n}={bloco*n} rutas/peticion')
    print(f'Peticiones necesarias: {total_bloques}')
    print(f'Tiempo estimado: ~{total_bloques * ors.pausa_s / 60:.0f} min\n')

    mat_dist = pd.DataFrame(index=ids, columns=ids, dtype=float)
    mat_dur = pd.DataFrame(index=ids, columns=ids, dtype=float)
    nulos = []

    api_key = ors.api_key()
    for num_bloque, bloque_orig in enumerate(dividir_en_bloques(todos_destinos, bloco), start=1):
        print(f'  Bloque {num_bloque}/{total_bloques}: origenes '
              f'{bloque_orig[0]}..{bloque_orig[-1]} x {n} destinos '
              f'({len(bloque_orig)*n} rutas)...')

        datos = consultar_ors_matrix(coords, bloque_orig, todos_destinos,
                                     api_key, ors.modo_transporte)
        distancias, duraciones = datos.get('distances'), datos.get('durations')
        if distancias is None or duraciones is None:
            raise RuntimeError(f"Respuesta ORS sin 'distances'/'durations': {datos}")
        if len(distancias) != len(bloque_orig):
            raise RuntimeError(f'Bloque {num_bloque}: ORS devolvio {len(distancias)} filas, '
                               f'esperadas {len(bloque_orig)}.')
        for i_local, fila in enumerate(distancias):
            if len(fila) != n:
                raise RuntimeError(f'Bloque {num_bloque}, fila {i_local}: {len(fila)} columnas, '
                                   f'esperadas {n}.')

        for i_local, i_global in enumerate(bloque_orig):
            id_orig = ids[i_global]
            for j_global in todos_destinos:
                id_dest = ids[j_global]
                dist_km = distancias[i_local][j_global]
                dur_seg = duraciones[i_local][j_global]
                if i_global == j_global:
                    mat_dist.loc[id_orig, id_dest] = 0.0
                    mat_dur.loc[id_orig, id_dest] = 0.0
                elif dist_km is None or dur_seg is None:
                    nulos.append((id_orig, id_dest))
                    mat_dist.loc[id_orig, id_dest] = float('nan')
                    mat_dur.loc[id_orig, id_dest] = float('nan')
                else:
                    mat_dist.loc[id_orig, id_dest] = round(float(dist_km), 4)
                    mat_dur.loc[id_orig, id_dest] = round(float(dur_seg) / 60.0, 4)

        time.sleep(ors.pausa_s)

    nan_total = int(mat_dist.isna().sum().sum())
    if nan_total == 0:
        print('\nMatriz completa: 0 valores NaN.')
    else:
        afectados = sorted({o for o, _ in nulos} | {d for _, d in nulos})
        print(f'\nAVISO: {nan_total} celdas sin ruta (NaN).')
        print(f'  Nodos afectados ({len(afectados)}): {afectados[:20]}'
              f'{"..." if len(afectados) > 20 else ""}')
        print('  Posible causa: coordenadas fuera de red vial o nodos aislados.')

    return mat_dist, mat_dur


# ══════════════════════════════════════════════════════════════════
# Formatacao e escrita
# ══════════════════════════════════════════════════════════════════
def convertir_a_formato_largo(mat_dist: pd.DataFrame, mat_dur: pd.DataFrame) -> pd.DataFrame:
    registros = [{'origen': o, 'destino': d,
                  'distancia_km': mat_dist.loc[o, d], 'duracion_min': mat_dur.loc[o, d]}
                 for o in mat_dist.index for d in mat_dist.columns]
    return pd.DataFrame(registros)


def crear_matriz_modelo_numerica(mat_dist, mat_dur, df_nodos) -> Tuple[pd.DataFrame, pd.DataFrame]:
    mapa = dict(zip(df_nodos['Nodo_matriz'], df_nodos['Nodo_modelo']))

    def reindexar(df):
        df = df.copy()
        df.index = [mapa[x] for x in df.index]
        df.columns = [mapa[x] for x in df.columns]
        return df

    return reindexar(mat_dist), reindexar(mat_dur)


def escrever_livro_ors(destino: Path, df_nodos, mat_dist, mat_dur,
                       mat_dist_modelo, mat_dur_modelo, formato_largo) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(destino, engine='openpyxl') as w:
        df_nodos.to_excel(w, sheet_name='nodos_ordenados', index=False)
        mat_dist.to_excel(w, sheet_name='distancia_km')
        mat_dur.to_excel(w, sheet_name='duracion_min')
        mat_dist_modelo.to_excel(w, sheet_name='distancia_modelo')
        mat_dur_modelo.to_excel(w, sheet_name='duracion_modelo')
        formato_largo.to_excel(w, sheet_name='formato_largo', index=False)
        for sn in ('distancia_km', 'duracion_min', 'distancia_modelo', 'duracion_modelo'):
            ws = w.sheets[sn]
            for row in ws.iter_rows(min_row=2, min_col=2):
                for cell in row:
                    if cell.value is not None:
                        cell.number_format = '0.0000'


def gerar_matriz_ors(cfg: Config) -> Path:
    """Pipeline completo do passo 1. Devolve o caminho do Excel gerado."""
    entrada = cfg.ruta('coordenadas')
    saida = cfg.ruta('matriz_ors', criar_pasta=True)

    print(f'Leyendo coordenadas: {entrada}')
    df = pd.read_excel(entrada, sheet_name=cfg.ors.hoja_excel)
    df.columns = df.columns.astype(str).str.strip()
    validar_columnas(df)
    print(df.head().to_string(), '\n')

    print('Preparando nodos...')
    df_nodos = preparar_nodos(df)
    print(f'  {len(df_nodos)} nodos (1 deposito + {len(df_nodos)-1} contenedores)\n')

    print('Generando matrices con OpenRouteService (OpenStreetMap)...')
    mat_dist, mat_dur = generar_matriz_distancias(df_nodos, cfg.ors)

    print('\nConvirtiendo a formato largo...')
    formato_largo = convertir_a_formato_largo(mat_dist, mat_dur)
    mat_dist_modelo, mat_dur_modelo = crear_matriz_modelo_numerica(mat_dist, mat_dur, df_nodos)

    print(f'Guardando: {saida}')
    escrever_livro_ors(saida, df_nodos, mat_dist, mat_dur,
                       mat_dist_modelo, mat_dur_modelo, formato_largo)
    print('Proceso finalizado exitosamente.')
    return saida
