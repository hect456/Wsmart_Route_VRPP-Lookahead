"""Passo 2 — construir o livro de instancia (4 folhas) para o VRPP.

    python scripts/02_construir_instancia.py --config config/instancia_491_C7.yaml

Junta `rutas.atributos` + `rutas.coordenadas` + `rutas.matriz_ors` e escreve
`rutas.instancia` com as folhas contentores / LatLong / matrizdistancias / matrizmin.
"""
from _comum import parser_base

from vrpp_lookahead import Config, carregar_instancia, construir_instancia, resumo_instancia


def main() -> None:
    p = parser_base(__doc__)
    p.add_argument('--folha-dist', default='distancia_km', help='folha de distancias no livro ORS')
    p.add_argument('--folha-dur', default='duracion_min', help='folha de duracoes no livro ORS')
    args = p.parse_args()

    cfg = Config.desde_yaml(args.config)
    print(f'=== PASSO 2 — INSTANCIA ({cfg.etiqueta}) ===')
    construir_instancia(cfg, folha_dist=args.folha_dist, folha_dur=args.folha_dur)

    print('\nValidacao da instancia gerada:')
    resumo_instancia(carregar_instancia(cfg))


if __name__ == '__main__':
    main()
