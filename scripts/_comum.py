"""Utilitarios partilhados pelos scripts 01 / 02 / 03.

  * poe `src/` no sys.path (nao e preciso instalar o pacote);
  * carrega o ficheiro `.env` (chave ORS) sem dependencias externas;
  * define os argumentos de linha de comandos que sobrepoem o YAML.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / 'src'))

CONFIG_POR_OMISSAO = RAIZ / 'config' / 'instancia_491_C7.yaml'


def carregar_dotenv(caminho: Path | None = None) -> None:
    """Le KEY=VALUE de `.env` para as variaveis de ambiente (sem sobrepor as existentes)."""
    caminho = caminho or (RAIZ / '.env')
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding='utf-8').splitlines():
        linha = linha.strip()
        if not linha or linha.startswith('#') or '=' not in linha:
            continue
        chave, valor = linha.split('=', 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def parser_base(descricao: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=descricao,
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--config', default=str(CONFIG_POR_OMISSAO),
                   help='ficheiro YAML de configuracao da instancia')
    return p


def adicionar_parametros_modelo(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Sobreposicoes pontuais dos parametros do modelo (o YAML continua a ser a fonte)."""
    g = p.add_argument_group('parametros do modelo (sobrepoem o YAML)')
    g.add_argument('--B', type=float, help='densidade dos residuos (kg/m3)')
    g.add_argument('--Q', type=float, help='capacidade do veiculo (kg)')
    g.add_argument('--R', type=float, help='receita (euro/kg)')
    g.add_argument('--C', type=float, help='custo de deslocamento (euro/km)')
    g.add_argument('--OMEGA', type=float, help='custo fixo por veiculo (euro)')
    g.add_argument('--MAX_ROTAS', '--max-rotas', dest='MAX_ROTAS', type=int,
                   help='numero maximo de rotas (k <= MAX_ROTAS)')
    g.add_argument('--MIP_GAP', '--mip-gap', dest='MIP_GAP', type=float,
                   help='tolerancia do solver (0.05 = 5%%)')
    g.add_argument('--TIME_LIMIT', '--time-limit', dest='TIME_LIMIT', type=int,
                   help='tempo maximo do solver (segundos)')

    h = p.add_argument_group('lookahead e solver')
    h.add_argument('--dias', type=int, help='dias simulados')
    h.add_argument('--janela', type=int, help='dias do horizonte de lookahead')
    h.add_argument('--threshold_mg', '--threshold-mg', dest='threshold_mg', type=float,
                   help='%% para MustGo')
    h.add_argument('--threshold_overflow', '--threshold-overflow', dest='threshold_overflow',
                   type=float, help='%% para MustGoLA')
    h.add_argument('--knn', type=int, help='vizinhos mais proximos por no')
    h.add_argument('--seed', type=int, help='semente do Gurobi')
    h.add_argument('--sem-mapas', dest='gerar_mapas', action='store_false', default=None,
                   help='nao gerar os mapas Folium')
    return p


CHAVES_OVERRIDE = ('B', 'Q', 'R', 'C', 'OMEGA', 'MAX_ROTAS', 'MIP_GAP', 'TIME_LIMIT',
                   'dias', 'janela', 'threshold_mg', 'threshold_overflow', 'knn', 'seed',
                   'gerar_mapas')


def overrides(args: argparse.Namespace) -> dict:
    return {k: getattr(args, k) for k in CHAVES_OVERRIDE
            if getattr(args, k, None) is not None}
