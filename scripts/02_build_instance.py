"""Step 2 — build the instance workbook (4 sheets) for the VRPP.

    python scripts/02_build_instance.py --config config/instance_491_C7.yaml

Joins `paths.attributes` + `paths.coordinates` + `paths.ors_matrix` and writes
`paths.instance` with the sheets bins / LatLong / distance_matrix / time_matrix.
"""
from _common import base_parser

from vrpp_lookahead import Config, build_instance, instance_summary, load_instance


def main() -> None:
    p = base_parser(__doc__)
    p.add_argument('--dist-sheet', default=None,
                   help='distance sheet in the ORS workbook (default: auto-detected)')
    p.add_argument('--dur-sheet', default=None,
                   help='duration sheet in the ORS workbook (default: auto-detected)')
    args = p.parse_args()

    cfg = Config.from_yaml(args.config)
    print(f'=== STEP 2 — INSTANCE ({cfg.label}) ===')
    build_instance(cfg, dist_sheet=args.dist_sheet, dur_sheet=args.dur_sheet)

    print('\nValidation of the generated instance:')
    instance_summary(load_instance(cfg))


if __name__ == '__main__':
    main()
