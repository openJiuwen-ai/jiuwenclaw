#!/usr/bin/env python3
"""
docconv - Unified Document Format Converter

A unified skill for converting documents between various formats.
Integrates: html2pdf, html2ppt, soffice, md2doc

Usage:
    python main.py <input_file> <target_format> [options]
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from converter import DocumentConverter
from routing import get_conversion_matrix, normalize_format


def print_supported_conversions():
    """Print table of supported conversions."""
    matrix = get_conversion_matrix()

    print("\nSupported Conversions:")
    print("=" * 50)
    print(f"{'Source':<12} -> {'Target':<12} | Path")
    print("-" * 50)

    for source in sorted(matrix.keys()):
        for target in sorted(matrix[source]):
            from routing import find_conversion_path
            path = find_conversion_path(source, target)
            if path:
                adapters = " -> ".join(step['adapter'] for step in path)
                print(f"{source:<12} -> {target:<12} | {adapters}")

    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(
        description='Unified Document Format Converter',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # HTML to PDF
  python main.py document.html pdf

  # HTML to PPTX (via PDF intermediate)
  python main.py slides.html pptx

  # DOCX to PDF
  python main.py report.docx pdf

  # Markdown to DOCX
  python main.py notes.md docx

  # Markdown to PDF
  python main.py notes.md pdf

  # HTML to Markdown
  python main.py page.html md

  # Specify output path
  python main.py input.html pdf -o /path/to/output.pdf

  # Custom page size for HTML conversions
  python main.py slides.html pdf --width 21cm --height 29.7cm

See --list for all supported conversions.
        """
    )

    parser.add_argument('input', nargs='?', help='Input file path')
    parser.add_argument('target', nargs='?', help='Target format (pdf, docx, pptx, xlsx)')
    parser.add_argument('-o', '--output', help='Output file path (optional)')
    parser.add_argument('-l', '--list', action='store_true', help='List supported conversions')
    parser.add_argument('--width', help='Page width for HTML conversions (e.g., 21cm, 1920px)')
    parser.add_argument('--height', help='Page height for HTML conversions (e.g., 29.7cm, 1080px)')

    args = parser.parse_args()

    # List supported conversions
    if args.list:
        print_supported_conversions()
        return 0

    # Validate arguments
    if not args.input or not args.target:
        parser.print_help()
        print("\nError: input file and target format are required (unless using --list)")
        return 1

    input_path = args.input
    target_format = args.target

    # Check if input file exists
    if not Path(input_path).exists():
        print(f"[docconv] Error: Input file not found: {input_path}")
        return 1

    # Create converter and execute
    converter = DocumentConverter()

    try:
        # Build kwargs from arguments
        kwargs = {}
        if args.width:
            kwargs['width'] = args.width
        if args.height:
            kwargs['height'] = args.height

        print(f"[docconv] Converting: {Path(input_path).name} -> {target_format}")

        output = converter.convert(
            input_path=input_path,
            target_format=target_format,
            output_path=args.output,
            **kwargs
        )

        output_path = Path(output)
        if output_path.exists():
            size = output_path.stat().st_size
            print(f"[docconv] Success: {output}")
            print(f"[docconv] File size: {size:,} bytes")
            return 0
        else:
            print(f"[docconv] Error: Output file was not created")
            return 1

    except ValueError as e:
        print(f"[docconv] Error: {e}")
        return 1
    except FileNotFoundError as e:
        print(f"[docconv] Error: {e}")
        return 1
    except RuntimeError as e:
        print(f"[docconv] Error: {e}")
        return 1
    except Exception as e:
        print(f"[docconv] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
