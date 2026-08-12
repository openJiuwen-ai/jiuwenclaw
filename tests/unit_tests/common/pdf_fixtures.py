# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Hand-built PDF fixtures for layout/table extraction tests.

There is no PDF *writer* in the dependency set (no reportlab), so these assemble
minimal well-formed PDFs straight from the 1.4 spec, the same approach as
``tests/unit_tests/agents/test_pdf_tools.py``. Ruling lines matter: pdfplumber's
default table strategy detects tables from stroked lines, so a table fixture has
to draw them rather than merely align text.
"""

from __future__ import annotations


def text_at(x: float, y: float, text: str, size: int = 10) -> str:
    """A ``BT … Tj ET`` text-showing operator at a PDF (bottom-left origin) point."""
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return f"BT /F1 {size} Tf {x} {y} Td ({escaped}) Tj ET\n"


def build_pdf(page_streams: list[bytes]) -> bytes:
    """Assemble a valid single- or multi-page PDF from raw content streams."""
    font_ref = 3 + 2 * len(page_streams)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(page_streams)))

    bodies: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_streams)} >>".encode("latin-1"),
    ]
    for index, payload in enumerate(page_streams):
        bodies.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
                f" /Resources << /Font << /F1 {font_ref} 0 R >> >>"
                f" /Contents {4 + 2 * index} 0 R >>"
            ).encode("latin-1")
        )
        bodies.append(
            f"<< /Length {len(payload)} >>\nstream\n".encode("latin-1") + payload + b"\nendstream"
        )
    bodies.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    return _assemble(bodies)


def build_pdf_with_image(*, width: int = 400, height: int = 300) -> bytes:
    """A one-page PDF with prose plus one embedded image XObject.

    The size is what matters to the assertions: ``count_page_images`` ignores
    anything under a threshold, so ``width``/``height`` (in PDF units, on a
    612x792 page) is how a test picks between a figure and a logo.
    """
    pixels = bytes(range(8)) * 8  # 8x8 greyscale, contents irrelevant
    content = (
        text_at(72, 700, "Figure 1 shows the architecture.")
        + f"q {width} 0 0 {height} 100 300 cm /Im1 Do Q\n"
    ).encode("latin-1")

    bodies: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            b" /Resources << /Font << /F1 5 0 R >> /XObject << /Im1 6 0 R >> >>"
            b" /Contents 4 0 R >>"
        ),
        f"<< /Length {len(content)} >>\nstream\n".encode("latin-1") + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Type /XObject /Subtype /Image /Width 8 /Height 8"
            b" /ColorSpace /DeviceGray /BitsPerComponent 8"
            + f" /Length {len(pixels)} >>\nstream\n".encode("latin-1")
            + pixels
            + b"\nendstream"
        ),
    ]
    return _assemble(bodies)


def _assemble(bodies: list[bytes]) -> bytes:
    """Wrap numbered object bodies in a header, xref table and trailer."""
    chunks = [b"%PDF-1.4\n"]
    positions: list[int] = []
    cursor = len(chunks[0])
    for number, body in enumerate(bodies, start=1):
        positions.append(cursor)
        piece = f"{number} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
        chunks.append(piece)
        cursor += len(piece)

    rows = ["0000000000 65535 f "]
    rows.extend(f"{position:010d} 00000 n " for position in positions)
    chunks.append(f"xref\n0 {len(rows)}\n".encode("latin-1"))
    chunks.append(("\n".join(rows) + "\n").encode("latin-1"))
    chunks.append(
        f"trailer\n<< /Size {len(rows)} /Root 1 0 R >>\n"
        f"startxref\n{cursor}\n%%EOF\n".encode("latin-1")
    )
    return b"".join(chunks)


def table_page_stream() -> bytes:
    """A page with prose, a fully ruled 3x3 table, then more prose."""
    xs = [72, 172, 272, 372]
    ys = [640, 660, 680, 700]

    stream = "0.5 w\n"
    for x in xs:
        stream += f"{x} {ys[0]} m {x} {ys[-1]} l S\n"
    for y in ys:
        stream += f"{xs[0]} {y} m {xs[-1]} {y} l S\n"

    cells = [
        ["Name", "Qty", "Price"],
        ["Widget", "3", "9.99"],
        ["Gadget", "7", "4.50"],
    ]
    for row_index, row in enumerate(cells):
        baseline = ys[2 - row_index] + 6
        for col_index, value in enumerate(row):
            stream += text_at(xs[col_index] + 5, baseline, value)

    stream += text_at(72, 740, "Quarterly inventory summary follows.")
    stream += text_at(72, 610, "All prices are in USD.")
    return stream.encode("latin-1")


def two_column_page_stream() -> bytes:
    """A page whose text sits in two columns separated by a wide gutter."""
    left = [
        "The quick brown fox jumps",
        "over the lazy dog while the",
        "committee reviews the annual",
        "budget proposal in detail and",
        "records every objection raised",
    ]
    right = [
        "Meanwhile the second column",
        "continues its own separate",
        "argument about scheduling and",
        "resource allocation across the",
        "three regional offices listed",
    ]

    stream = ""
    for index, line in enumerate(left):
        stream += text_at(72, 700 - 16 * index, line)
    for index, line in enumerate(right):
        stream += text_at(330, 700 - 16 * index, line)
    return stream.encode("latin-1")
