"""
Adapter for soffice (LibreOffice) skill.
Supports: docx/pptx -> pdf, pdf -> pptx
"""

import os
import subprocess
import sys
import shutil
from pathlib import Path
from typing import Optional

from abc import ABC, abstractmethod


class BaseAdapter(ABC):
    """Base class for all conversion adapters."""

    name: str = ""

    @abstractmethod
    def convert(self, input_path: str, output_path: str, **kwargs) -> bool:
        """Convert a document from one format to another."""
        pass

    @abstractmethod
    def supports(self, source: str, target: str) -> bool:
        """Check if this adapter supports the given conversion."""
        pass

    def check_dependencies(self) -> tuple[bool, str]:
        """Check if required dependencies are available."""
        return True, ""


class SofficeAdapter(BaseAdapter):
    """Adapter for LibreOffice soffice conversions."""

    name = "soffice"

    # Supported conversions
    SUPPORTED = {
        ('docx', 'pdf'),
        ('pptx', 'pdf'),
        ('pdf', 'pptx'),
        # Legacy format upgrades
        ('doc', 'docx'),
        ('ppt', 'pptx'),
        ('doc', 'pdf'),  # 新增
        ('ppt', 'pdf'),  # 新增
    }

    def check_dependencies(self) -> tuple[bool, str]:
        """Check if soffice command is available."""
        soffice_cmd = shutil.which('soffice')
        if not soffice_cmd:
            # Also try 'soffice.bin' which is sometimes used on Linux
            soffice_cmd = shutil.which('soffice.bin')

        if not soffice_cmd:
            return False, "soffice command not found. Please install LibreOffice."

        return True, ""

    def supports(self, source: str, target: str) -> bool:
        """Check if this adapter supports the conversion."""
        source_lower = source.lower().lstrip('.')
        target_lower = target.lower().lstrip('.')

        # Handle aliases
        if source_lower == 'word':
            source_lower = 'docx'
        if source_lower == 'powerpoint':
            source_lower = 'pptx'

        return (source_lower, target_lower) in self.SUPPORTED

    def convert(self, input_path: str, output_path: str, **kwargs) -> bool:
        """
        Convert using LibreOffice soffice.

        Args:
            input_path: Path to input file
            output_path: Path to output file
            **kwargs: Additional options

        Returns:
            True if successful
        """
        is_available, error = self.check_dependencies()
        if not is_available:
            raise RuntimeError(f"soffice adapter not available: {error}")

        input_path = Path(input_path)
        output_path = Path(output_path)

        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        # Determine target format from output extension
        target_format = output_path.suffix.lstrip('.').lower()

        # Build command
        cmd = ['soffice', '--headless']

        # PDF -> PPTX requires special infilter
        if input_path.suffix.lower() == '.pdf' and target_format == 'pptx':
            cmd.append('--infilter=impress_pdf_import')

        cmd.extend([
            '--convert-to', target_format,
            '--outdir', str(output_path.parent),
            str(input_path)
        ])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            # soffice outputs to a file with the same name but different extension
            # We need to check if the expected output file was created
            expected_output = output_path.parent / f"{input_path.stem}.{target_format}"

            if expected_output.exists():
                # Rename to desired output path if different
                if expected_output != output_path:
                    if output_path.exists():
                        output_path.unlink()
                    expected_output.rename(output_path)
                return True
            else:
                raise RuntimeError(f"soffice did not create expected output file: {expected_output}")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"soffice conversion failed: {e.stderr}")
