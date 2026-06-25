#!/usr/bin/env python3
"""Generate a WiX .wxs file from a dist manifest.

Usage:
    python3 bin/build_wxs.py \
        --version 0.4.0 \
        --recorder-exe dist/OysterRecorder.exe \
        --mods-dir dist/mods \
        --template installer/oyster-recorder.wxs.template \
        --output installer/oyster-recorder.wxs
"""

import argparse
import os
import sys
import uuid
import xml.etree.ElementTree as ET

# Fixed upgrade code — never change once shipped
UPGRADE_CODE = "7E5C1A20-3D8F-4F4E-AC91-F9B6E1C8D2B4"

# WiX 4 namespace
WIX_NS = "http://wixtoolset.org/schemas/v4/wxs"


def component_guid(relative_path: str) -> str:
    """Deterministic GUID for a component based on its relative path."""
    key = f"oyster-recorder:{relative_path}"
    return str(uuid.uuid5(uuid.NAMESPACE_OID, key)).upper()


def component_id(relative_path: str) -> str:
    """Generate a valid WiX Component Id from a file path.

    WiX Ids must match [A-Za-z_][A-Za-z0-9_.]* — we strip non-alnum
    characters and prefix with 'Cmp'.
    """
    # Replace path separators and dots with underscores
    safe = relative_path.replace(os.sep, "_").replace(".", "_")
    # Remove any remaining non-alnum chars (except underscore)
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in safe)
    # Ensure starts with letter or underscore
    if safe and not safe[0].isalpha() and safe[0] != "_":
        safe = "_" + safe
    return f"Cmp_{safe}"


def file_id(relative_path: str) -> str:
    """Generate a valid WiX File Id from a file path."""
    safe = relative_path.replace(os.sep, "_").replace(".", "_")
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in safe)
    if safe and not safe[0].isalpha() and safe[0] != "_":
        safe = "_" + safe
    return f"File_{safe}"


def build_components_xml(recorder_exe: str, mods_dir: str) -> tuple[str, int, int]:
    """Build the <Component> XML blocks for all files.

    Returns (xml_string, mod_count, exe_count).
    """
    components: list[str] = []
    mod_count = 0
    exe_count = 0

    # --- Recorder EXE ---
    if recorder_exe and os.path.isfile(recorder_exe):
        rel = os.path.basename(recorder_exe)
        cid = component_id(rel)
        fid = file_id(rel)
        guid = component_guid(rel)
        comp_xml = (
            f'      <Component Id="{cid}" Directory="INSTALLDIR" Guid="{guid}">\n'
            f'        <File Id="{fid}" Source="{recorder_exe}" KeyPath="yes" />\n'
            f"      </Component>"
        )
        components.append(comp_xml)
        exe_count += 1

    # --- Mod jars ---
    if mods_dir and os.path.isdir(mods_dir):
        jars = sorted(f for f in os.listdir(mods_dir) if f.lower().endswith(".jar"))
        for jar_name in jars:
            jar_path = os.path.join(mods_dir, jar_name)
            rel = os.path.join("mods", jar_name)
            cid = component_id(rel)
            fid = file_id(rel)
            guid = component_guid(rel)
            comp_xml = (
                f'      <Component Id="{cid}" Directory="INSTALLDIR" Guid="{guid}">\n'
                f'        <File Id="{fid}" Source="{jar_path}" KeyPath="yes" />\n'
                f"      </Component>"
            )
            components.append(comp_xml)
            mod_count += 1

    return "\n".join(components), mod_count, exe_count


def substitute_template(template_path: str, version: str, components_xml: str) -> str:
    """Read template and substitute placeholders."""
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()

    content = content.replace("{{VERSION}}", version)
    content = content.replace("{{UPGRADE_CODE}}", UPGRADE_CODE)
    content = content.replace("{{COMPONENTS}}", components_xml)

    return content


def validate_xml(xml_string: str) -> None:
    """Parse XML to ensure it is well-formed. Raises on error."""
    ET.fromstring(xml_string)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate WiX .wxs from dist manifest")
    parser.add_argument("--version", required=True, help="Product version (semver)")
    parser.add_argument("--recorder-exe", required=True, help="Path to OysterRecorder.exe")
    parser.add_argument("--mods-dir", required=True, help="Directory containing mod .jar files")
    parser.add_argument("--template", required=True, help="Path to .wxs.template file")
    parser.add_argument("--output", required=True, help="Output .wxs file path")
    args = parser.parse_args()

    # Validate inputs
    if not os.path.isfile(args.recorder_exe):
        print(
            f"Error: recorder exe not found: {args.recorder_exe}",
            file=sys.stderr,
        )
        return 1

    if not os.path.isfile(args.template):
        print(
            f"Error: template not found: {args.template}",
            file=sys.stderr,
        )
        return 1

    if not os.path.isdir(args.mods_dir):
        print(
            f"Error: mods directory not found: {args.mods_dir}",
            file=sys.stderr,
        )
        return 1

    # Build components XML
    components_xml, mod_count, exe_count = build_components_xml(args.recorder_exe, args.mods_dir)

    # Substitute template
    wxs_content = substitute_template(args.template, args.version, components_xml)

    # Validate XML
    try:
        validate_xml(wxs_content)
    except ET.ParseError as e:
        print(f"Error: generated XML is not well-formed: {e}", file=sys.stderr)
        return 1

    # Write output
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(wxs_content)

    # Summary
    print(
        f"Generated wxs with {mod_count} mod jars + {exe_count} exe, ProductVersion={args.version}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
