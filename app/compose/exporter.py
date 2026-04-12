"""
Compose exporter — walks section source files and produces bundled output.

Supports:
  - markdown: concatenates all .md files from sections into a single document
  - zip: creates a zip archive of all section source files
"""

import io
import logging
import zipfile
from pathlib import Path
from typing import Optional

from .models import project_dir, get_sections, _sanitize_folder_name

logger = logging.getLogger(__name__)


def export_markdown(project_id: str) -> Optional[str]:
    """Walk sections in order, concatenate .md files, return combined text."""
    sections = get_sections(project_id)
    if not sections:
        return None

    # Sort by order
    sections.sort(key=lambda s: (s.order, s.name))

    pdir = project_dir(project_id)
    parts = []

    for section in sections:
        section_dir = pdir / "sections" / _sanitize_folder_name(section.name) / "content"
        if not section_dir.is_dir():
            continue
        # Collect .md files in this section
        md_files = sorted(section_dir.glob("*.md"))
        if md_files:
            # Add section heading
            depth = "#" if not section.parent_id else "##"
            parts.append(f"{depth} {section.name}\n")
            for md_file in md_files:
                try:
                    content = md_file.read_text(encoding="utf-8").strip()
                    if content:
                        parts.append(content)
                except Exception:
                    logger.warning("Failed to read %s", md_file)
            parts.append("")  # blank line separator

    if not parts:
        return "# (No content yet)\n"

    return "\n\n".join(parts)


def export_zip(project_id: str) -> Optional[bytes]:
    """Create a zip archive of all section source files. Returns bytes."""
    sections = get_sections(project_id)
    if not sections:
        return None

    sections.sort(key=lambda s: (s.order, s.name))
    pdir = project_dir(project_id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        file_count = 0
        for section in sections:
            folder_name = _sanitize_folder_name(section.name)
            section_dir = pdir / "sections" / folder_name / "content"
            if not section_dir.is_dir():
                continue
            for fp in sorted(section_dir.iterdir()):
                if fp.is_file():
                    try:
                        arcname = f"{folder_name}/{fp.name}"
                        zf.write(fp, arcname)
                        file_count += 1
                    except Exception:
                        logger.warning("Failed to add %s to zip", fp)

        # Also include the combined markdown as master.md
        combined = export_markdown(project_id)
        if combined:
            zf.writestr("master.md", combined.encode("utf-8"))
            file_count += 1

    if file_count == 0:
        return None

    return buf.getvalue()
