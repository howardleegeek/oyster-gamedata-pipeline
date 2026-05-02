#!/usr/bin/env python3
"""Generate gameinfo.xlsx with full per-spec field set for buyer-spec v1."""

import argparse
from datetime import date

from openpyxl import Workbook


def write_gameinfo_xlsx(
    output_path: str, vendor: str = "oyster-internal", game: str = "Minecraft"
) -> None:
    """Write a gameinfo workbook with metadata, scene_table, and asset_ramp sheets."""
    wb = Workbook()

    # ── Sheet 1: metadata ──────────────────────────────────────────────
    ws_meta = wb.active
    ws_meta.title = "metadata"
    meta_headers = [
        "vendor_name",
        "game_name",
        "total_clips",
        "complexity_class",
        "scene_class",
        "element_density",
        "dynamic_logic",
        "interaction_class",
        "visual_class",
        "record_date",
    ]
    ws_meta.append(meta_headers)

    meta_values = [
        vendor,
        game,
        120,
        "high",
        "large",
        "dense",
        True,
        "complex",
        "photorealistic",
        date.today().isoformat(),
    ]
    ws_meta.append(meta_values)

    # ── Sheet 2: scene_table ───────────────────────────────────────────
    ws_scene = wb.create_sheet("scene_table")
    scene_headers = [
        "scene_id",
        "scene_name",
        "route_type",
        "expected_min_duration",
        "notes",
    ]
    ws_scene.append(scene_headers)

    scenes = [
        ("S001", "Overworld Spawn", 1, 300, "Default spawn area with basic terrain"),
        ("S002", "Nether Fortress", 2, 180, "Special route through fortress corridors"),
        ("S003", "Village Trade Loop", 3, 120, "Looping trade route between villagers"),
        ("S004", "End City Ascent", 2, 240, "Vertical climb through end city towers"),
        ("S005", "Ocean Monument Dive", 1, 200, "Underwater exploration route"),
        ("S006", "Desert Temple Run", 1, 150, "Speed-run style temple traversal"),
        ("S007", "Mountain Peak Climb", 2, 180, "Special scenic route to highest peak"),
        ("S008", "Cave System Loop", 3, 300, "Looping cave exploration path"),
    ]
    for sid, sname, rtype, dur, notes in scenes:
        ws_scene.append([sid, sname, rtype, dur, notes])

    # ── Sheet 3: asset_ramp ────────────────────────────────────────────
    ws_ramp = wb.create_sheet("asset_ramp")
    ramp_headers = ["week", "expected_clip_count", "ready_maps"]
    ws_ramp.append(ramp_headers)

    ramp_data = [
        (1, 10, 3),
        (2, 20, 5),
        (3, 35, 8),
        (4, 50, 12),
        (5, 70, 15),
        (6, 90, 18),
        (7, 105, 20),
        (8, 120, 22),
    ]
    for week, clips, maps in ramp_data:
        ws_ramp.append([week, clips, maps])

    # ── Save ───────────────────────────────────────────────────────────
    wb.save(output_path)
    print(f"Written {output_path}  (vendor={vendor}, game={game})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate gameinfo.xlsx per buyer-spec v1"
    )
    parser.add_argument("--vendor", default="oyster-internal", help="Vendor name")
    parser.add_argument("--game", default="Minecraft", help="Game name")
    parser.add_argument("--output", default="gameinfo.xlsx", help="Output xlsx path")
    args = parser.parse_args()
    write_gameinfo_xlsx(args.output, args.vendor, args.game)


if __name__ == "__main__":
    main()
