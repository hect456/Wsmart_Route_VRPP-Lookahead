"""Step 1 — build the distance/time matrix with OpenRouteService.

    python scripts/01_build_ors_matrix.py --config config/instance_491_C7.yaml

Requires the ORS key in `.env` (ORS_API_KEY=...). Writes the Excel file named in
`paths.ors_matrix`. It takes ~1 request per block of sources (see the on-screen
estimate).
"""
from _common import base_parser, load_dotenv

load_dotenv()

from vrpp_lookahead import Config, build_ors_matrix  # noqa: E402


def main() -> None:
    p = base_parser(__doc__)
    p.add_argument('--mode', help='ORS mode: driving-hgv, driving-car, cycling-regular, ...')
    p.add_argument('--route-limit', type=int, dest='limit',
                   help='max routes per request accepted by the ORS server')
    args = p.parse_args()

    cfg = Config.from_yaml(args.config)
    if args.mode:
        cfg.ors.transport_mode = args.mode
    if args.limit:
        cfg.ors.max_routes_per_request = args.limit

    print(f'=== STEP 1 — ORS MATRIX ({cfg.label}) ===')
    build_ors_matrix(cfg)


if __name__ == '__main__':
    main()
