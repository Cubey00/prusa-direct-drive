"""python -m focusfield simulate|analyze|chart. Hardware movement is only exposed in the app."""
import argparse
from dataclasses import replace
from pathlib import Path

from machine_settings import SETTINGS
from .chart import load_chart
from .config import DEFAULT_RECIPE, load_recipe, simulation_fixture
from .controller import ScanController
from .hardware import SimCamera, SimStage
from .reports import analyze_run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sim = sub.add_parser("simulate")
    sim.add_argument("--recipe", type=Path, default=DEFAULT_RECIPE)
    sim.add_argument("--output", type=Path, default=Path("logs/runs/focusfield"))
    sim.add_argument("--quick", action="store_true", help="One frame per position, one sweep")
    sim.add_argument("--no-analysis", action="store_true")
    analyze = sub.add_parser("analyze")
    analyze.add_argument("run", type=Path)
    analyze.add_argument("--allow-partial", action="store_true")
    sub.add_parser("chart", add_help=False, help="Generate printable SVG and layout; pass --help for chart options")
    args, rest = parser.parse_known_args(argv)
    if args.command == "chart":
        from .generate_chart import build_parser, generate_chart
        chart_parser = build_parser()
        try:
            generate_chart(chart_parser.parse_args(rest))
        except ValueError as exc:
            chart_parser.error(str(exc))
        return 0
    if rest:
        parser.error("unrecognized arguments: " + " ".join(rest))
    if args.command == "analyze":
        print(analyze_run(args.run, allow_partial=args.allow_partial))
        return 0
    recipe = load_recipe(args.recipe)
    if args.quick:
        recipe = replace(recipe, repetitions=1, frames_per_position=1, settle_s=0)
    fixture = simulation_fixture()
    stage = SimStage(fixture)
    camera = SimCamera(stage, recipe, load_chart(recipe.chart_path))
    controller = ScanController(recipe, SETTINGS, fixture, stage, camera, args.output,
                                on_event=lambda kind, *values: print(kind, *values) if kind == "state" else None)
    result = controller.run(analyze=not args.no_analysis)
    print(f"{result.state}: {result.path} {result.message}")
    return 0 if result.state == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
