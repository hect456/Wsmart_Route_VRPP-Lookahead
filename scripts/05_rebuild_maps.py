"""Step 5 — redraw the Folium maps of a run that is already solved.

    python scripts/05_rebuild_maps.py --config config/instance_491_C7.yaml

Reads the result workbook of each simulated day and re-renders `route_N.html`
from it. Useful when the map rendering changes and re-solving would cost hours:
the routes are read back from the workbook, so the maps are redrawn from the
exact same solution.

Nothing but the HTML files is touched — the workbooks are read, never written.
"""
from __future__ import annotations

from _common import base_parser

from vrpp_lookahead import Config, load_instance, plot_routes
from vrpp_lookahead.fixed_routes import solution_from_workbook


def main() -> None:
    p = base_parser(__doc__)
    p.add_argument('--no-grey', dest='show_not_collected', action='store_false',
                   help='omit the grey layer of bins that were not collected')
    args = p.parse_args()

    cfg = Config.from_yaml(args.config)
    inst = load_instance(cfg)
    results = cfg.path('results')
    assert results.exists(), f'No results folder: {results}'

    print(f'=== STEP 5 — REBUILD MAPS ({cfg.label}) ===')
    days = sorted(d for d in results.iterdir() if d.is_dir() and d.name.startswith('Day_'))
    assert days, f'No Day_XX folder inside {results}'

    for folder in days:
        workbook = folder / f'result_{folder.name}.xlsx'
        if not workbook.exists():
            print(f'  {folder.name}: no result workbook, skipped')
            continue
        print(f'\n  {folder.name} — {workbook.name}')
        sol = solution_from_workbook(workbook, inst)
        plot_routes(sol, inst, folder, show_not_collected=args.show_not_collected)

    print('\nMaps rebuilt.')


if __name__ == '__main__':
    main()
