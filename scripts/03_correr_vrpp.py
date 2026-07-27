"""Passo 3 — correr o VRPP + Lookahead sobre a instancia.

    python scripts/03_correr_vrpp.py --config config/instancia_491_C7.yaml
    python scripts/03_correr_vrpp.py --Q 5000 --MAX_ROTAS 3 --TIME_LIMIT 3600
    python scripts/03_correr_vrpp.py --so-diagnostico     (classifica sem optimizar)

Saidas em `rutas.resultados`: Dia_XX/rota_N.html, Dia_XX/resultado_Dia_XX.xlsx
e resumo_<etiqueta>_todos_dias.xlsx.
"""
import json

from _comum import adicionar_parametros_modelo, overrides, parser_base

from vrpp_lookahead import (Config, carregar_instancia, correr, diagnosticar,
                            resumo_instancia)


def main() -> None:
    p = adicionar_parametros_modelo(parser_base(__doc__))
    p.add_argument('--so-diagnostico', action='store_true',
                   help='apenas classifica MustGo/MustGoLA e verifica a frota')
    args = p.parse_args()

    cfg = Config.desde_yaml(args.config).aplicar_overrides(overrides(args))

    print(f'=== PASSO 3 — VRPP + LOOKAHEAD ({cfg.etiqueta}) ===')
    if args.so_diagnostico:
        cfg.resumo()
        print()
        inst = carregar_instancia(cfg)
        resumo_instancia(inst)
        print()
        diagnosticar(inst, cfg)
        return

    kpis = correr(cfg)

    pasta = cfg.ruta('resultados', criar_pasta=True)
    (pasta / 'parametros_usados.json').write_text(
        json.dumps(cfg.como_dict(), indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    print(f'Parametros usados: {pasta / "parametros_usados.json"}')
    print(f'Dias com solucao: {len(kpis)}/{cfg.lookahead.dias}')


if __name__ == '__main__':
    main()
