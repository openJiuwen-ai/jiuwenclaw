#!/usr/bin/env python3
"""Generate logo.ico and logo.png from user-in-team.svg.

This script creates ICO and PNG versions of the logo for:
- Application icon (logo.ico)
- Tray icon
- Floating widget icon

Usage:
    python scripts/generate_logo.py
"""

import os
import sys
from pathlib import Path

def generate_logo_with_svglib():
    """Generate logo using svglib from user-in-team.svg."""
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM
        from PIL import Image
        import io
        
        svg_path = Path(__file__).parent.parent / "jiuwenavatar" / "channels" / "web" / "frontend" / "src" / "assets" / "user-in-team.svg"
        output_dir = Path(__file__).parent.parent / "jiuwenavatar" / "channels" / "web" / "frontend" / "public"
        
        if not svg_path.exists():
            print(f"ERROR: SVG file not found: {svg_path}")
            return False
        
        print(f"Using SVG: {svg_path}")
        
        # Load SVG
        drawing = svg2rlg(str(svg_path))
        if drawing is None:
            print("ERROR: Failed to parse SVG")
            return False
        
        # Get original size
        orig_w = drawing.width
        orig_h = drawing.height
        print(f"Original SVG size: {orig_w}x{orig_h}")
        
        # Standard Windows ICO sizes
        ico_sizes = [16, 32, 48, 256]
        ico_images = []
        
        for size in ico_sizes:
            # Scale drawing
            scale = size / max(orig_w, orig_h)
            drawing.width = size
            drawing.height = size
            drawing.scale(scale, scale)
            
            # Render to PNG
            png_data = renderPM.drawToString(drawing, fmt="PNG")
            img = Image.open(io.BytesIO(png_data))
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            
            # Ensure exact size
            if img.size != (size, size):
                img = img.resize((size, size), Image.Resampling.LANCZOS)
            
            ico_images.append(img)
            print(f"  Generated {size}x{size} PNG from SVG")
            
            # Reload drawing for next size
            drawing = svg2rlg(str(svg_path))
        
        # Save as ICO (largest first for PIL)
        ico_path = output_dir / "logo.ico"
        ico_images[-1].save(
            str(ico_path),
            format="ICO",
            append_images=ico_images[:-1]
        )
        print(f"Saved: {ico_path}")
        
        # Save 256x256 PNG
        png_path = output_dir / "logo.png"
        ico_images[-1].save(str(png_path), format="PNG")
        print(f"Saved: {png_path}")
        
        return True
    except ImportError as e:
        print(f"svglib not available: {e}")
        print("Install with: pip install svglib reportlab pillow")
        return False
    except Exception as e:
        print(f"svglib failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def generate_logo_with_cairosvg():
    """Generate logo using cairosvg from user-in-team.svg (alternative)."""
    try:
        import cairosvg
        from PIL import Image
        import io
        
        svg_path = Path(__file__).parent.parent / "jiuwenavatar" / "channels" / "web" / "frontend" / "src" / "assets" / "user-in-team.svg"
        output_dir = Path(__file__).parent.parent / "jiuwenavatar" / "channels" / "web" / "frontend" / "public"
        
        if not svg_path.exists():
            print(f"ERROR: SVG file not found: {svg_path}")
            return False
        
        print(f"Using SVG (cairosvg): {svg_path}")
        
        # Standard Windows ICO sizes
        ico_sizes = [16, 32, 48, 256]
        ico_images = []
        
        for size in ico_sizes:
            png_data = cairosvg.svg2png(url=str(svg_path), output_width=size, output_height=size)
            img = Image.open(io.BytesIO(png_data))
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            ico_images.append(img)
            print(f"  Generated {size}x{size} PNG from SVG")
        
        # Save as ICO (largest first for PIL)
        ico_path = output_dir / "logo.ico"
        ico_images[-1].save(
            str(ico_path),
            format="ICO",
            append_images=ico_images[:-1]
        )
        print(f"Saved: {ico_path}")
        
        # Save 256x256 PNG
        png_path = output_dir / "logo.png"
        ico_images[-1].save(str(png_path), format="PNG")
        print(f"Saved: {png_path}")
        
        return True
    except ImportError as e:
        print(f"cairosvg not available: {e}")
        return False
    except Exception as e:
        print(f"cairosvg failed: {e}")
        return False


def generate_logo_with_pil():
    """Generate logo using PIL (fallback method).
    
    Creates a blue gradient rounded rectangle with a simplified team icon.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        
        output_dir = Path(__file__).parent.parent / "jiuwenavatar" / "channels" / "web" / "frontend" / "public"
        
        def create_logo(size):
            """Create a logo image at the specified size."""
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            # Calculate dimensions
            padding = max(1, size // 16)
            corner_radius = max(2, size // 4)
            
            # Draw rounded rectangle with gradient-like blue color
            # Using the colors from the SVG: rgb(68,154,255) to rgb(20,118,255)
            base_color = (44, 136, 255, 255)  # Average blue
            
            # Draw the rounded rectangle background
            draw.rounded_rectangle(
                [(padding, padding), (size - padding - 1, size - padding - 1)],
                radius=corner_radius,
                fill=base_color
            )
            
            # Draw simplified team/network icon (white circles connected)
            icon_color = (255, 255, 255, 255)
            center = size // 2
            
            # Scale factors
            s = size / 64.0
            
            # Draw the team network pattern (simplified version)
            # Main center circle
            r_main = int(6 * s)
            draw.ellipse(
                [center - r_main, center - r_main, center + r_main, center + r_main],
                fill=icon_color
            )
            
            # Corner circles (smaller)
            r_small = int(4 * s)
            offset = int(16 * s)
            
            positions = [
                (center - offset, center - offset),  # Top-left
                (center + offset, center - offset),  # Top-right
                (center - offset, center + offset),  # Bottom-left
                (center + offset, center + offset),  # Bottom-right
            ]
            
            for px, py in positions:
                draw.ellipse(
                    [px - r_small, py - r_small, px + r_small, py + r_small],
                    fill=icon_color
                )
            
            # Draw connecting lines
            line_width = max(1, int(2 * s))
            for px, py in positions:
                draw.line([(center, center), (px, py)], fill=icon_color, width=line_width)
            
            return img
        
        # Generate at different sizes for ICO (standard Windows sizes)
        ico_sizes = [16, 32, 48, 256]
        ico_images = [create_logo(size) for size in ico_sizes]
        
        for size in ico_sizes:
            print(f"  Generated {size}x{size} logo")
        
        # Save as ICO with multiple sizes
        ico_path = output_dir / "logo.ico"
        # PIL requires the largest image first for ICO
        ico_images[-1].save(
            str(ico_path),
            format="ICO",
            append_images=ico_images[:-1]
        )
        print(f"Saved: {ico_path}")
        
        # Save 256x256 PNG
        png_path = output_dir / "logo.png"
        ico_images[-1].save(str(png_path), format="PNG")
        print(f"Saved: {png_path}")
        
        return True
    except Exception as e:
        print(f"PIL method failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("Generating logo files from user-in-team.svg...")
    print()
    
    # Try svglib first (good quality, pure Python)
    if generate_logo_with_svglib():
        print("\nSuccess! Logo files generated using svglib.")
        return 0
    
    print()
    
    # Try cairosvg (requires system Cairo library)
    if generate_logo_with_cairosvg():
        print("\nSuccess! Logo files generated using cairosvg.")
        return 0
    
    print()
    
    # Fallback to PIL (hand-drawn icon)
    print("WARNING: Could not convert SVG. Using fallback icon.")
    if generate_logo_with_pil():
        print("\nSuccess! Logo files generated using PIL (fallback).")
        print("Note: For SVG support, install svglib: pip install svglib reportlab pillow")
        return 0
    
    print("\nFailed to generate logo files.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
