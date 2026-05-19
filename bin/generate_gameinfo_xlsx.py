#!/usr/bin/env python3
"""
R009 · bin/generate_gameinfo_xlsx.py
14-field gameinfo.xlsx generator

Generates an Excel file with game information metadata for video recordings.
"""

import argparse
import sys
import zipfile
import re
import os
from datetime import date, datetime
from io import BytesIO
from typing import Dict, Any, Optional, List


# Field names in order (PRD §3.3)
FIELD_NAMES = [
    "game_name",
    "game_version",
    "platform",
    "scene_name",
    "weather",
    "time_of_day",
    "character_name",
    "character_class",
    "operator_id",
    "recording_date",
    "total_frames",
    "video_duration_sec",
    "route_type",
    "notes",
]


def build_gameinfo_dict(
    game_name: str = "Minecraft",
    game_version: str = "1.20.4",
    platform: str = "Java Edition",
    scene_name: str = "flat-overworld",
    weather: str = "clear",
    time_of_day: str = "day",
    character_name: str = "DataPilot",
    character_class: str = "spectator",
    operator_id: str = "vendor-001-op-A",
    recording_date: Optional[str] = None,
    total_frames: int = 9000,
    video_duration_sec: float = 300.0,
    route_type: int = 1,
    notes: str = "",
) -> Dict[str, Any]:
    """
    Build a dictionary with all 14 gameinfo fields.
    
    Args:
        game_name: Name of the game
        game_version: Version of the game
        platform: Platform/edition (e.g., "Java Edition")
        scene_name: Name of the scene or world
        weather: Weather condition
        time_of_day: Time of day (day/night/etc)
        character_name: Name of the character/player
        character_class: Class of the character
        operator_id: ID of the operator
        recording_date: Date of recording (None = today ISO date)
        total_frames: Total number of frames in the video
        video_duration_sec: Video duration in seconds
        route_type: Route type (1, 2, or 3)
        notes: Additional notes
    
    Returns:
        Dictionary with all 14 fields
    """
    if recording_date is None:
        recording_date = date.today().isoformat()
    
    return {
        "game_name": game_name,
        "game_version": game_version,
        "platform": platform,
        "scene_name": scene_name,
        "weather": weather,
        "time_of_day": time_of_day,
        "character_name": character_name,
        "character_class": character_class,
        "operator_id": operator_id,
        "recording_date": recording_date,
        "total_frames": total_frames,
        "video_duration_sec": video_duration_sec,
        "route_type": route_type,
        "notes": notes,
    }


def _escape_xml(s: str) -> str:
    """Escape special XML characters."""
    return (s
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
            .replace("'", "&apos;"))


def _write_xlsx_fallback(data: Dict[str, Any], out_path: str) -> None:
    """
    Write XLSX file without openpyxl, using stdlib zipfile + XML.
    This is a minimal XLSX writer that creates a valid .xlsx file.
    """
    # Ensure field order matches FIELD_NAMES
    values = [str(data.get(field, "")) for field in FIELD_NAMES]
    
    # Build worksheet XML
    cells_xml = ""
    for col_idx, value in enumerate(values):
        col_letter = chr(65 + col_idx)  # A, B, C, ...
        cell_ref = f"{col_letter}2"
        # Determine cell type
        if value.isdigit() or (value.replace(".", "", 1).isdigit()):
            cell_type = 't="n"'
            cell_value = value
        else:
            cell_type = 't="inlineStr"'
            cell_value = f"<is><t>{_escape_xml(value)}</t></is>"
        cells_xml += f'<cell r="{cell_ref}" {cell_type}>{cell_value}</cell>\n'
    
    # Build sheet XML
    sheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
    <sheetViews>
        <sheetView tabSelected="1" workbookViewId="0"/>
    </sheetViews>
    <cols>
        <col min="1" max="14" width="15" customWidth="1"/>
    </cols>
    <sheetData>
        <row r="1">
'''
    for col_idx, field in enumerate(FIELD_NAMES):
        col_letter = chr(65 + col_idx)
        cell_ref = f"{col_letter}1"
        sheet_xml += f'            <cell r="{cell_ref}" t="inlineStr"><is><t>{_escape_xml(field)}</t></is></cell>\n'
    
    sheet_xml += '''        </row>
        <row r="2">
'''
    sheet_xml += cells_xml
    sheet_xml += '''        </row>
    </sheetData>
</worksheet>'''
    
    # Build workbook.xml
    workbook_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <sheets>
        <sheet name="gameinfo" sheetId="1" r:id="rId1"/>
    </sheets>
</workbook>'''
    
    # Build [Content_Types].xml
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
    <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>'''
    
    # Build _rels/.rels
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    
    # Build xl/_rels/workbook.xml.rels
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>'''
    
    # Write the XLSX file
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', content_types)
        zf.writestr('_rels/.rels', rels)
        zf.writestr('xl/workbook.xml', workbook_xml)
        zf.writestr('xl/_rels/workbook.xml.rels', workbook_rels)
        zf.writestr('xl/worksheets/sheet1.xml', sheet_xml)


def write_xlsx(data: Dict[str, Any], out_path: str) -> None:
    """
    Write data to an XLSX file.
    
    Uses openpyxl if available, otherwise falls back to stdlib zipfile.
    
    Args:
        data: Dictionary with field names as keys
        out_path: Path to output XLSX file
    """
    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
        
        wb = Workbook()
        ws = wb.active
        ws.title = "gameinfo"
        
        # Write header row (field names)
        for col_idx, field in enumerate(FIELD_NAMES, start=1):
            ws.cell(row=1, column=col_idx, value=field)
        
        # Write data row
        for col_idx, field in enumerate(FIELD_NAMES, start=1):
            value = data.get(field, "")
            ws.cell(row=2, column=col_idx, value=value)
        
        wb.save(out_path)
        
    except ImportError:
        # Fallback: use stdlib zipfile to create XLSX
        _write_xlsx_fallback(data, out_path)


def read_xlsx(path: str) -> Dict[str, Any]:
    """
    Read an XLSX file and return a dictionary.
    
    Reads the first sheet: row 1 as keys, row 2 as values.
    
    Args:
        path: Path to the XLSX file
    
    Returns:
        Dictionary with field names as keys
    """
    try:
        from openpyxl import load_workbook
        
        wb = load_workbook(path, data_only=True)
        ws = wb.active
        
        # Read header row
        keys = []
        col = 1
        while True:
            cell_value = ws.cell(row=1, column=col).value
            if cell_value is None:
                break
            keys.append(str(cell_value))
            col += 1
        
        # Read data row
        values = []
        col = 1
        while True:
            cell_value = ws.cell(row=2, column=col).value
            if cell_value is None:
                break
            values.append(cell_value)
            col += 1
        
        # Combine keys and values
        result = {}
        for key, value in zip(keys, values):
            result[key] = value
        
        return result
        
    except ImportError:
        # Fallback: parse XML manually
        with zipfile.ZipFile(path, 'r') as zf:
            with zf.open('xl/worksheets/sheet1.xml') as f:
                content = f.read().decode('utf-8')
        
        # Extract cell values using regex
        # Find row 1 (header)
        keys = []
        values = []
        
        # Simple XML parsing with regex
        cell_pattern = re.compile(r'<cell r="([A-Z]+)(\d+)"[^>]*>(.*?)</cell>')
        for match in cell_pattern.finditer(content):
            col_letter = match.group(1)
            row_num = int(match.group(2))
            cell_content = match.group(3)
            
            # Extract text from <is><t>...</t></is> or direct value
            text_match = re.search(r'<t>(.*?)</t>', cell_content, re.DOTALL)
            if text_match:
                cell_value = text_match.group(1)
            else:
                # Numeric value
                cell_value = cell_content.strip()
            
            if row_num == 1:
                keys.append(cell_value)
            elif row_num == 2:
                values.append(cell_value)
        
        result = {}
        for key, value in zip(keys, values):
            result[key] = value
        
        return result


def validate_route_type(route_type: int) -> bool:
    """Validate that route_type is one of 1, 2, or 3."""
    return route_type in (1, 2, 3)


def main(argv: Optional[List[str]] = None) -> int:
    """
    CLI entry point.
    
    Args:
        argv: Command line arguments (defaults to sys.argv)
    
    Returns:
        Exit code (0 for success, non-zero for error)
    """
    parser = argparse.ArgumentParser(
        description="Generate a gameinfo.xlsx file with 14 fields"
    )
    
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output XLSX file path"
    )
    
    parser.add_argument("--game-name", default="Minecraft")
    parser.add_argument("--game-version", default="1.20.4")
    parser.add_argument("--platform", default="Java Edition")
    parser.add_argument("--scene-name", default="flat-overworld")
    parser.add_argument("--weather", default="clear")
    parser.add_argument("--time-of-day", default="day")
    parser.add_argument("--character-name", default="DataPilot")
    parser.add_argument("--character-class", default="spectator")
    parser.add_argument("--operator-id", default="vendor-001-op-A")
    parser.add_argument("--recording-date", default=None)
    parser.add_argument("--total-frames", type=int, default=9000)
    parser.add_argument("--video-duration-sec", type=float, default=300.0)
    parser.add_argument("--route-type", type=int, default=1)
    parser.add_argument("--notes", default="")
    
    args = parser.parse_args(argv)
    
    # Validate route_type
    if not validate_route_type(args.route_type):
        print(f"Error: route_type must be 1, 2, or 3, got {args.route_type}", 
              file=sys.stderr)
        return 1
    
    # Build the data dictionary
    data = build_gameinfo_dict(
        game_name=args.game_name,
        game_version=args.game_version,
        platform=args.platform,
        scene_name=args.scene_name,
        weather=args.weather,
        time_of_day=args.time_of_day,
        character_name=args.character_name,
        character_class=args.character_class,
        operator_id=args.operator_id,
        recording_date=args.recording_date,
        total_frames=args.total_frames,
        video_duration_sec=args.video_duration_sec,
        route_type=args.route_type,
        notes=args.notes,
    )
    
    # Write the XLSX file
    try:
        write_xlsx(data, args.output)
        print(f"Successfully wrote {args.output}")
        return 0
    except Exception as e:
        print(f"Error writing XLSX file: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
