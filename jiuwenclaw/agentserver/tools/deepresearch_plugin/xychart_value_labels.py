from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
import re
import xml.etree.ElementTree as ET

from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_preprocess import XyChartMetadata


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

POINT_RE = re.compile(r"([ML])\s*([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)", re.IGNORECASE)

try:
    from PIL import Image, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False


@dataclass(slots=True)
class ChartPoint:
    x: float
    y: float
    bottom_y: float
    left_x: float
    right_x: float
    center_y: float
    value: float
    label: str
    color: str
    positive: bool
    orientation: str


@dataclass(slots=True)
class ChartLabel:
    x: float
    y: float
    text: str
    color: str
    text_anchor: str = "middle"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _element_class(element: ET.Element) -> str:
    return (element.attrib.get("class") or "").strip()


def _find_first(element: ET.Element, predicate) -> ET.Element | None:
    for child in element.iter():
        if predicate(child):
            return child
    return None


def _find_group_by_class(root: ET.Element, class_name: str) -> ET.Element | None:
    return _find_first(
        root,
        lambda element: _local_name(element.tag) == "g" and _element_class(element) == class_name,
    )


def _parse_float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_viewbox(root: ET.Element) -> tuple[float, float, float, float]:
    raw = root.attrib.get("viewBox")
    if raw:
        parts = raw.replace(",", " ").split()
        if len(parts) == 4:
            return tuple(_parse_float(part) for part in parts)

    width = _parse_float(root.attrib.get("width"), 0.0)
    height = _parse_float(root.attrib.get("height"), 0.0)
    return (0.0, 0.0, width or 700.0, height or 500.0)


def _parse_path_points(path_data: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for _, x_text, y_text in POINT_RE.findall(path_data or ""):
        points.append((float(x_text), float(y_text)))
    return points


def _select_monotonic_points(
    points: list[tuple[float, float]],
    expected_count: int,
) -> list[tuple[float, float]]:
    if expected_count <= 0:
        return []

    selected: list[tuple[float, float]] = []
    for x, y in points:
        if not selected:
            selected.append((x, y))
            if len(selected) >= expected_count:
                break
            continue

        prev_x, _ = selected[-1]
        if x + 1e-6 < prev_x:
            break
        if math.isclose(x, prev_x, abs_tol=1e-6) and math.isclose(y, selected[-1][1], abs_tol=1e-6):
            continue

        selected.append((x, y))
        if len(selected) >= expected_count:
            break

    return selected


def _series_points_from_group(
    group: ET.Element | None,
    kind: str,
    values: list[float],
    labels: list[str],
    chart_orientation: str,
) -> list[ChartPoint]:
    if group is None:
        return []

    color = group.attrib.get("stroke") or group.attrib.get("fill") or "#374151"
    points: list[ChartPoint] = []

    if kind == "bar":
        rects = [element for element in group if _local_name(element.tag) == "rect"]
        if not rects:
            return []
        for index, rect in enumerate(rects[: len(values)]):
            x = _parse_float(rect.attrib.get("x"))
            y = _parse_float(rect.attrib.get("y"))
            width = _parse_float(rect.attrib.get("width"))
            height = _parse_float(rect.attrib.get("height"))
            fill = rect.attrib.get("fill") or color
            value = values[index]
            points.append(
                ChartPoint(
                    x=x + width / 2,
                    y=y,
                    bottom_y=y + height,
                    left_x=x,
                    right_x=x + width,
                    center_y=y + height / 2,
                    value=value,
                    label=labels[index],
                    color=fill,
                    positive=value >= 0,
                    orientation=chart_orientation,
                )
            )
        return points

    path = _find_first(group, lambda element: _local_name(element.tag) == "path")
    if path is None:
        return []

    path_points = _select_monotonic_points(
        _parse_path_points(path.attrib.get("d", "")),
        len(values),
    )
    if not path_points:
        return []

    stroke = path.attrib.get("stroke") or path.attrib.get("fill") or color
    for index, (x, y) in enumerate(path_points[: len(values)]):
        value = values[index]
        points.append(
            ChartPoint(
                x=x,
                y=y,
                bottom_y=y,
                left_x=x,
                right_x=x,
                center_y=y,
                value=value,
                label=labels[index],
                color=stroke,
                positive=value >= 0,
                orientation="vertical",
            )
        )
    return points


def _clamp_x_position(x: float, width: float, edge_padding: float) -> float:
    return min(max(x, edge_padding), max(width - edge_padding, edge_padding))


def build_xychart_value_labels(
    svg_markup: str,
    metadata: XyChartMetadata,
) -> tuple[list[ChartLabel], tuple[float, float, float, float]]:
    if not metadata.series:
        return [], (0.0, 0.0, 0.0, 0.0)

    root = ET.fromstring(svg_markup)
    viewbox = _parse_viewbox(root)
    _, _, width, height = viewbox

    labels: list[ChartLabel] = []
    series_gap = 14.0
    edge_padding = 8.0

    for series in metadata.series:
        group = _find_group_by_class(root, f"{series.kind}-plot-{series.index}")
        points = _series_points_from_group(
            group,
            series.kind,
            series.values,
            series.display_values,
            metadata.chart_orientation,
        )
        for point in points:
            offset = series.index * series_gap
            text_anchor = "middle"
            if point.orientation == "horizontal":
                y = point.center_y + min(offset, 10.0)
                if point.positive:
                    x = _clamp_x_position(
                        point.right_x + 8.0 + offset,
                        width,
                        edge_padding,
                    )
                    text_anchor = "start"
                else:
                    x = _clamp_x_position(
                        point.left_x - 8.0 - offset,
                        width,
                        edge_padding,
                    )
                    text_anchor = "end"
            else:
                if point.positive:
                    y = point.y - 8.0 - offset
                else:
                    y = point.bottom_y + 18.0 + offset
                x = _clamp_x_position(point.x, width, edge_padding)

            y = min(max(y, 12.0), max(height - 4.0, 12.0))
            labels.append(
                ChartLabel(
                    x=x,
                    y=y,
                    text=point.label,
                    color=point.color,
                    text_anchor=text_anchor,
                )
            )

    return labels, viewbox


def annotate_xychart_svg(svg_markup: str, metadata: XyChartMetadata) -> str:
    labels, _ = build_xychart_value_labels(svg_markup, metadata)
    if not labels:
        return svg_markup

    root = ET.fromstring(svg_markup)
    parent = _find_group_by_class(root, "plot")
    if parent is None:
        parent = _find_first(root, lambda element: _local_name(element.tag) == "svg") or root

    labels_group = ET.Element(f"{{{SVG_NS}}}g", {"class": "xychart-value-labels"})
    for label in labels:
        text = ET.SubElement(
            labels_group,
            f"{{{SVG_NS}}}text",
            {
                "class": "xychart-value-label",
                "x": f"{label.x:.3f}",
                "y": f"{label.y:.3f}",
                "fill": label.color,
                "font-size": "12",
                "font-weight": "600",
                "text-anchor": label.text_anchor,
                "stroke": "#ffffff",
                "stroke-width": "3",
                "paint-order": "stroke",
            },
        )
        text.text = label.text

    parent.append(labels_group)
    return ET.tostring(root, encoding="unicode")


def _try_load_font(candidate: str, size: int):
    try:
        return ImageFont.truetype(candidate, size=size)
    except OSError:
        return None


def _load_font(size: int):
    for candidate in ("arial.ttf", "segoeui.ttf"):
        font = _try_load_font(candidate, size)
        if font is not None:
            return font
    return ImageFont.load_default()


def overlay_xychart_value_labels_on_png(
    png_path: str,
    svg_markup: str,
    metadata: XyChartMetadata,
) -> bool:
    if not PIL_AVAILABLE:
        return False

    labels, (_, _, viewbox_width, viewbox_height) = build_xychart_value_labels(svg_markup, metadata)
    if not labels or viewbox_width <= 0 or viewbox_height <= 0:
        return False

    with Image.open(png_path) as original:
        image = original.convert("RGBA")
        draw = ImageDraw.Draw(image)
        scale_x = image.width / viewbox_width
        scale_y = image.height / viewbox_height
        font_size = max(int(min(scale_x, scale_y) * 12), 14)
        font = _load_font(font_size)

        for label in labels:
            draw.text(
                (label.x * scale_x, label.y * scale_y),
                label.text,
                fill=label.color,
                font=font,
                anchor="ms",
                stroke_width=max(1, font_size // 7),
                stroke_fill="#ffffff",
            )

        output = BytesIO()
        image.convert("RGB").save(output, format="PNG", optimize=True)

    with open(png_path, "wb") as handle:
        handle.write(output.getvalue())
    return True
