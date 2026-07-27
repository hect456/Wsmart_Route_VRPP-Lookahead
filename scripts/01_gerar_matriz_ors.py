"""Passo 1 — gerar a matriz de distancias/tempos com OpenRouteService.

    python scripts/01_gerar_matriz_ors.py --config config/instancia_491_C7.yaml

Requer a chave ORS em `.env` (ORS_API_KEY=...). Escreve o Excel indicado em
`rutas.matriz_ors`. Demora ~1 pedido por bloco de origens (ver estimativa no ecra).
"""
from _comum import carregar_dotenv, parser_base

carregar_dotenv()

from vrpp_lookahead import Config, gerar_matriz_ors  # noqa: E402


def main() -> None:
    p = parser_base(__doc__)
    p.add_argument('--modo', help='modo ORS: driving-hgv, driving-car, cycling-regular, ...')
    p.add_argument('--limite-rotas', type=int, dest='limite',
                   help='max de rotas por pedido do servidor ORS')
    args = p.parse_args()

    cfg = Config.desde_yaml(args.config)
    if args.modo:
        cfg.ors.modo_transporte = args.modo
    if args.limite:
        cfg.ors.max_routes_per_request = args.limite

    print(f'=== PASSO 1 — MATRIZ ORS ({cfg.etiqueta}) ===')
    gerar_matriz_ors(cfg)


if __name__ == '__main__':
    main()
