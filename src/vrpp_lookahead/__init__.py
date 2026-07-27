"""VRPP + Lookahead — recolha selectiva de residuos com matriz de distancias ORS.

Pipeline:
    1. ors_matrix.gerar_matriz_ors(cfg)      coordenadas   -> matriz ORS (km / min)
    2. instancia.construir_instancia(cfg)    atributos + coordenadas + matriz -> instancia
    3. simulacao.correr(cfg)                 instancia -> rotas, KPI, mapas

Todos os parametros vivem em `config.Config` (ficheiro YAML em config/).
"""
from .config import Config, Lookahead, Modelo, ORS, Rutas, Solver
from .instancia import Instancia, carregar_instancia, construir_instancia, diagnosticar, resumo_instancia
from .lookahead import ResultadoLookahead, lookahead
from .ors_matrix import gerar_matriz_ors
from .reporting import exportar_excel, exportar_resumo, plotar_rotas
from .simulacao import correr, simular
from .vrpp import Solucao, resolver_vrpp

__version__ = '1.0.0'

__all__ = [
    'Config', 'Modelo', 'Lookahead', 'Solver', 'ORS', 'Rutas',
    'Instancia', 'construir_instancia', 'carregar_instancia', 'resumo_instancia', 'diagnosticar',
    'lookahead', 'ResultadoLookahead',
    'resolver_vrpp', 'Solucao',
    'plotar_rotas', 'exportar_excel', 'exportar_resumo',
    'simular', 'correr',
    'gerar_matriz_ors',
]
