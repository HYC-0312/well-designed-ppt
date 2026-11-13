#!/usr/bin/env python3
"""Read-only checks for explicit PPTX text sizes and marked bullet counts.

This is a triage aid. Theme/layout inheritance, visual overflow, image text,
formula correctness, and projection readability require opening the deck.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml$")


def explicit_size(run: ET.Element, paragraph: ET.Element) -> float | None:
    """Return a locally declared font size in pt, without guessing inheritance."""
    run_properties = run.find("a:rPr", NS)
    if run_properties is not None and run_properties.get("sz"):
        return int(run_properties.get("sz")) / 100
    paragraph_properties = paragraph.find("a:pPr/a:defRPr", NS)
    if paragraph_properties is not None and paragraph_properties.get("sz"):
        return int(paragraph_properties.get("sz")) / 100
    return None


def check_slide(xml_bytes: bytes, number: int) -> dict:
    root = ET.fromstring(xml_bytes)
    all_text = [node.text or "" for node in root.findall(".//a:t", NS)]
    slide = {
        "slide": number,
        "characters": sum(len(value) for value in all_text),
        "marked_bullets": 0,
        "explicit_font_min_pt": None,
        "warnings": [],
    }
    sizes: list[float] = []
    for shape in root.findall(".//p:sp", NS):
        placeholder = shape.find("p:nvSpPr/p:nvPr/p:ph", NS)
        is_title = placeholder is not None and placeholder.get("type") in {
            "title", "ctrTitle"
        }
        threshold = 32 if is_title else 24
        for paragraph in shape.findall("p:txBody/a:p", NS):
            properties = paragraph.find("a:pPr", NS)
            if properties is not None and (
                properties.find("a:buChar", NS) is not None
                or properties.find("a:buAutoNum", NS) is not None
            ):
                slide["marked_bullets"] += 1
            for run in [*paragraph.findall("a:r", NS), *paragraph.findall("a:fld", NS)]:
                size = explicit_size(run, paragraph)
                if size is None:
                    continue
                sizes.append(size)
                if size < threshold:
                    text = "".join(node.text or "" for node in run.findall(".//a:t", NS))
                    slide["warnings"].append(
                        f"{('title' if is_title else 'text')} {size:g} pt < {threshold} pt: {text[:35]}"
                    )
    if sizes:
        slide["explicit_font_min_pt"] = min(sizes)
    if slide["marked_bullets"] > 5:
        slide["warnings"].append(
            f"{slide['marked_bullets']} marked bullets; review the 5-point limit"
        )
    return slide


def inspect(path: Path) -> dict:
    with zipfile.ZipFile(path) as package:
        slide_members = []
        for name in package.namelist():
            match = SLIDE_RE.fullmatch(name)
            if match:
                slide_members.append((int(match.group(1)), name))
        slide_members.sort()
        if not slide_members:
            raise ValueError("No PPTX slide XML files found")
        slides = [check_slide(package.read(name), number) for number, name in slide_members]
    return {
        "file": str(path),
        "slide_count": len(slides),
        "slides": slides,
        "limitations": (
            "Only explicit font sizes and explicitly marked bullets are checked. "
            "Theme/layout fonts, tables, image text, clipping, formulas and visual "
            "quality require inspection of the rendered presentation."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pptx", type=Path, help="PPTX file to inspect; never modified")
    parser.add_argument("--json", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    try:
        report = inspect(args.pptx)
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        parser.error(str(exc))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    warnings = [(s["slide"], w) for s in report["slides"] for w in s["warnings"]]
    print(f"Slides: {report['slide_count']} | candidate warnings: {len(warnings)}")
    for number, warning in warnings[:20]:
        print(f"  Slide {number}: {warning}")
    if len(warnings) > 20:
        print(f"  ... {len(warnings) - 20} more; use --json for the full report")
    print(report["limitations"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

