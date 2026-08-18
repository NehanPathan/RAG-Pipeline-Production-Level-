"""Print what a drawing's annotations were found to refer to.

    python scripts/inspect_cad_associations.py path/to/drawing.dxf

Association is a judgement about a drawing, and a judgement nobody can inspect
is one nobody can correct. This prints every conclusion with the evidence
behind it -- which leader, how far from the text, how far from the target --
alongside every annotation the drawing did not make determinable, so a wrong
tolerance shows up as a list of near-misses rather than as a quietly wrong
answer weeks later.

Reads the file only. Nothing is indexed, uploaded or written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingestion.cad.blocks import describe_blocks
from src.ingestion.cad.dxf_reader import DxfReader
from src.ingestion.cad.spatial import associate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drawing", type=Path)
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        help="hide associations below this confidence",
    )
    args = parser.parse_args()

    if not args.drawing.exists():
        print(f"no such file: {args.drawing}", file=sys.stderr)
        return 2

    cad = DxfReader().read(args.drawing)
    report = associate(cad)

    print(f"\n{args.drawing.name}")
    print(f"  {cad.entity_count} entities, {len(cad.layouts)} layout(s)")
    print(f"  {len(cad.texts)} text entities, {len(cad.geometry)} located primitives")
    print(f"  {len(report.chains)} leader chain(s) assembled from connected segments")
    print(f"  {report.resolved} annotation(s) resolved, {report.unresolved} unresolved")
    print(f"  {len(report.drawn_dimensions)} drawn dimension(s)")

    native = [d for d in cad.dimensions if d.has_exact_value]
    print(f"  {len(native)} native DIMENSION entit(ies) with exact measured values")

    resolved = sorted(
        (a for a in report.annotations if a.relation != "unresolved"),
        key=lambda a: -a.confidence,
    )
    shown = [a for a in resolved if a.confidence >= args.min_confidence]
    print(f"\n{'=' * 72}\nRESOLVED ({len(shown)} shown of {len(resolved)})\n{'=' * 72}")
    for found in shown:
        point = found.target_point or (0.0, 0.0)
        print(f"\n  [{found.confidence:.2f}] {found.text.strip()}")
        print(
            f"        -> {found.target_layer} entity {found.target_entity_id or '?'} "
            f"at ({point[0]:.1f}, {point[1]:.1f}) model space"
        )
        print(f"        {found.evidence}")

    if report.drawn_dimensions:
        print(f"\n{'=' * 72}\nDRAWN DIMENSIONS\n{'=' * 72}")
        print("  Text typed beside dimension geometry. The file does not assert")
        print("  that the geometry measures these values -- a drafter typed them.\n")
        for record in sorted(report.drawn_dimensions, key=lambda d: -d.confidence):
            print(
                f"  [{record.confidence:.2f}] {record.text_override:16s}"
                f" = {record.measurement:g} {record.unit or '(unitless)'}"
                f"   layer {record.layer}, beside entity {record.target_entity_id}"
            )

    unresolved = [a for a in report.annotations if a.relation == "unresolved"]
    print(f"\n{'=' * 72}\nUNRESOLVED ({len(unresolved)})\n{'=' * 72}")
    print("  Present on the sheet; what they refer to is not determinable.\n")
    for found in unresolved:
        print(f"  {found.text.strip()[:56]:58s} {found.evidence}")

    blocks = describe_blocks(cad)
    if blocks.groups:
        print(f"\n{'=' * 72}\nSYMBOLS\n{'=' * 72}")
        print(
            f"  {blocks.definition_count} block definition(s) -> "
            f"{blocks.distinct_shapes} distinct shape(s)\n"
        )
        for group in blocks.groups:
            placed = f"x{group.placements}"
            if not group.placements and group.nested_placements:
                placed = f"x0 (+{group.nested_placements} nested)"
            layers = ", ".join(group.layers) or "-"
            print(f"  {placed:16s} {layers:16s} {group.summary()[:70]}")

        print("\n  Placements per layer:")
        for layer, (placements, shapes) in sorted(
            blocks.per_layer.items(), key=lambda kv: (-kv[1][0], kv[0])
        ):
            print(f"    {layer:20s} {placements:3d} placement(s) of {shapes} shape(s)")
        print(
            "\n  A block placement is not a part count. What each shape "
            "represents\n  is not stated by the file."
        )

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
