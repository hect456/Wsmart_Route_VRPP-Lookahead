"""Step 3 — run the VRPP + Lookahead on the instance.

    python scripts/03_run_vrpp.py --config config/instance_491_C7.yaml
    python scripts/03_run_vrpp.py --Q 5000 --MAX_ROUTES 3 --TIME_LIMIT 3600
    python scripts/03_run_vrpp.py --diagnose-only     (classifies without optimising)

Outputs in `paths.results`: Day_XX/route_N.html, Day_XX/result_Day_XX.xlsx
and summary_<label>_all_days.xlsx.
"""
import json

from _common import add_model_arguments, base_parser, overrides

from vrpp_lookahead import Config, diagnose, instance_summary, load_instance, run


def main() -> None:
    p = add_model_arguments(base_parser(__doc__))
    p.add_argument('--diagnose-only', action='store_true',
                   help='only classify MustGo/MustGoLA and check the fleet')
    args = p.parse_args()

    cfg = Config.from_yaml(args.config).apply_overrides(overrides(args))

    print(f'=== STEP 3 — VRPP + LOOKAHEAD ({cfg.label}) ===')
    if args.diagnose_only:
        cfg.summary()
        print()
        inst = load_instance(cfg)
        instance_summary(inst)
        print()
        diagnose(inst, cfg)
        return

    kpis = run(cfg)

    folder = cfg.path('results', create_dir=True)
    (folder / 'parameters_used.json').write_text(
        json.dumps(cfg.as_dict(), indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    print(f'Parameters used: {folder / "parameters_used.json"}')
    print(f'Days with a solution: {len(kpis)}/{cfg.lookahead.days}')


if __name__ == '__main__':
    main()
