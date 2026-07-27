"""Configuracao do projecto — UNICO ponto de parametrizacao.

Os parametros do modelo (B, Q, R, C, OMEGA, MIP_GAP, TIME_LIMIT, MAX_ROTAS)
sao editados no ficheiro YAML de `config/` ou por linha de comandos:

    python scripts/03_correr_vrpp.py --config config/instancia_491_C7.yaml --Q 4000 --max-rotas 3
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Parametros do modelo editaveis pelo utilizador (YAML ou CLI).
PARAMETROS_MODELO = ('B', 'Q', 'R', 'C', 'OMEGA', 'MIP_GAP', 'TIME_LIMIT', 'MAX_ROTAS')


@dataclass
class Modelo:
    """Parametros fisicos e economicos do VRPP."""

    B: float = 16.0          # densidade dos residuos (kg/m3)
    Q: float = 3500.0        # capacidade do veiculo (kg)
    R: float = 0.1625        # receita (euro/kg)
    C: float = 1.0           # custo de deslocamento (euro/km)
    OMEGA: float = 0.1       # custo fixo por veiculo (euro)
    MAX_ROTAS: int = 2       # k <= MAX_ROTAS
    MIP_GAP: float = 0.05    # tolerancia do solver
    TIME_LIMIT: int = 21600  # tempo maximo do solver (s)

    @property
    def cap_frota_kg(self) -> float:
        return self.MAX_ROTAS * self.Q

    def validar(self) -> None:
        assert self.B > 0, 'B deve ser > 0'
        assert self.Q > 0, 'Q deve ser > 0'
        assert self.MAX_ROTAS >= 1, 'MAX_ROTAS deve ser >= 1'
        assert 0 <= self.MIP_GAP < 1, 'MIP_GAP deve estar em [0, 1)'
        assert self.TIME_LIMIT > 0, 'TIME_LIMIT deve ser > 0'


@dataclass
class Lookahead:
    """Horizonte e limiares de classificacao MustGo / MustGoLA."""

    dias: int = 1                       # dias simulados
    janela: int = 2                     # dias do horizonte de antecipacao
    threshold_mg: float = 100.0         # % : nivel + ai       >= thr  -> MustGo
    threshold_overflow: float = 100.0   # % : nivel + ai*k     >= ovf  -> MustGoLA
    nivel_bloqueio_pct: float = 5.0     # so diagnostico: pontos quase vazios

    def validar(self) -> None:
        assert self.dias >= 1, 'lookahead.dias deve ser >= 1'
        assert self.janela >= 1, 'lookahead.janela deve ser >= 1'


@dataclass
class Solver:
    """Afinacao do Gurobi e da pre-filtragem de arcos."""

    knn: int = 25                        # vizinhos mais proximos por no
    seed: int | None = None              # None = default do Gurobi
    output_flag: int = 1
    mip_focus: int = 1
    heuristics: float = 0.5
    threads: int = 0
    cuts: int = 3
    presolve: int = 1
    node_method: int = 2
    preservar_arcos_mustgo: bool = False  # True = nunca filtrar arcos MustGo-MustGo
    gerar_mapas: bool = True


@dataclass
class ORS:
    """Parametros da Matrix API do OpenRouteService."""

    modo_transporte: str = 'driving-hgv'
    max_routes_per_request: int = 3500
    pausa_s: float = 1.6
    bloque_manual: int | None = None
    hoja_excel: Any = 0
    api_key_env: str = 'ORS_API_KEY'

    def api_key(self) -> str:
        chave = os.environ.get(self.api_key_env, '').strip()
        if not chave:
            raise RuntimeError(
                f'Variavel de ambiente {self.api_key_env} nao definida.\n'
                f'Copie .env.example para .env e coloque la a sua chave ORS '
                f'(registo gratuito em https://openrouteservice.org/dev/#/signup).'
            )
        return chave


@dataclass
class Rutas:
    """Caminhos (relativos a raiz do projecto ou absolutos)."""

    coordenadas: str = ''   # entrada: ID_bin | Latitude | Longitude
    atributos: str = ''     # entrada: id_contentor | Si | ai | Vol_cont | Vol_kg | Ncont
    matriz_ors: str = ''    # saida do passo 1 / entrada do passo 2
    instancia: str = ''     # saida do passo 2 / entrada do passo 3 (livro de 4 folhas)
    resultados: str = ''    # saida do passo 3


@dataclass
class Config:
    etiqueta: str = 'instancia'
    descricao: str = ''
    raiz: Path = field(default_factory=Path.cwd)
    rutas: Rutas = field(default_factory=Rutas)
    modelo: Modelo = field(default_factory=Modelo)
    lookahead: Lookahead = field(default_factory=Lookahead)
    solver: Solver = field(default_factory=Solver)
    ors: ORS = field(default_factory=ORS)

    # ── construcao ────────────────────────────────────────────────
    @classmethod
    def desde_yaml(cls, caminho: str | Path) -> 'Config':
        caminho = Path(caminho).resolve()
        assert caminho.exists(), f'Config nao encontrada: {caminho}'
        dados = yaml.safe_load(caminho.read_text(encoding='utf-8')) or {}
        raiz = Path(dados.get('raiz') or caminho.parent.parent).resolve()
        cfg = cls(
            etiqueta=str(dados.get('etiqueta', 'instancia')),
            descricao=str(dados.get('descricao', '')),
            raiz=raiz,
            rutas=Rutas(**(dados.get('rutas') or {})),
            modelo=Modelo(**(dados.get('modelo') or {})),
            lookahead=Lookahead(**(dados.get('lookahead') or {})),
            solver=Solver(**(dados.get('solver') or {})),
            ors=ORS(**(dados.get('ors') or {})),
        )
        cfg.validar()
        return cfg

    def validar(self) -> None:
        self.modelo.validar()
        self.lookahead.validar()

    # ── caminhos ──────────────────────────────────────────────────
    def ruta(self, nome: str, criar_pasta: bool = False) -> Path:
        valor = getattr(self.rutas, nome, '')
        assert valor, f'rutas.{nome} nao definido no YAML'
        p = Path(valor)
        p = p if p.is_absolute() else (self.raiz / p)
        p = p.resolve()
        if criar_pasta:
            (p if p.suffix == '' else p.parent).mkdir(parents=True, exist_ok=True)
        return p

    def aplicar_overrides(self, overrides: dict[str, Any]) -> 'Config':
        """Sobrepoe parametros do modelo vindos da linha de comandos."""
        for chave, valor in (overrides or {}).items():
            if valor is None:
                continue
            if chave in PARAMETROS_MODELO:
                setattr(self.modelo, chave, valor)
            elif hasattr(self.lookahead, chave):
                setattr(self.lookahead, chave, valor)
            elif hasattr(self.solver, chave):
                setattr(self.solver, chave, valor)
            else:
                raise KeyError(f'Parametro desconhecido: {chave}')
        self.validar()
        return self

    def como_dict(self) -> dict:
        d = asdict(self)
        d['raiz'] = str(self.raiz)
        return d

    # ── apresentacao ──────────────────────────────────────────────
    def resumo(self) -> None:
        m, la, sv = self.modelo, self.lookahead, self.solver
        print(f'Instancia   : {self.etiqueta}   {self.descricao}')
        print(f'Raiz        : {self.raiz}')
        print(f'B={m.B:g} kg/m3 | Q={m.Q:g} kg | MAX_ROTAS={m.MAX_ROTAS} '
              f'-> frota {m.cap_frota_kg:g} kg')
        print(f'R={m.R:g} eur/kg | C={m.C:g} eur/km | OMEGA={m.OMEGA:g} eur/veiculo')
        print(f'MIP_GAP={m.MIP_GAP*100:g}% | TIME_LIMIT={m.TIME_LIMIT}s ({m.TIME_LIMIT/3600:g}h)')
        print(f'Dias={la.dias} | Janela={la.janela} | THR_MG={la.threshold_mg:g}% '
              f'| THR_OVF={la.threshold_overflow:g}% | KNN={sv.knn}')
