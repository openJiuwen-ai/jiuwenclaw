# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO

from jiuwenclaw.agentserver.tools.deepresearch_plugin.mermaid_preprocess import XyChartMetadata

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

# 资源边界配置
MAX_SVG_BYTES = 10 * 1024 * 1024  # 最大SVG大小（10MB）
MAX_SERIES_COUNT = 20  # 最大series数量
MAX_POINTS_PER_SERIES = 1000  # 每个series最大point数量
MAX_TOTAL_LABELS = 5000  # 最大总label数量

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
    label: str = ""  # 兼容旧代码，等同于 text


def _local_name(tag: str) -> str:
    """Extracts the local tag name from a namespace-qualified XML tag.

    Removes the namespace prefix (e.g., '{http://www.w3.org/2000/svg}')
    from an XML element tag, returning only the local name portion.

    Args:
        tag: The XML tag string, which may include a namespace URI prefix.

    Returns:
        The local tag name without namespace prefix. For example, given
        '{http://www.w3.org/2000/svg}g', returns 'g'.
    """
    return tag.rsplit("}", 1)[-1]


def _element_class(element: ET.Element) -> str:
    """Retrieves the class attribute value from an XML element.

    Args:
        element: The XML element to extract the class attribute from.

    Returns:
        The stripped class attribute value, or an empty string if the
        element has no class attribute.
    """
    return (element.attrib.get("class") or "").strip()


def _find_first(element: ET.Element, predicate) -> ET.Element | None:
    """Finds the first descendant element matching a predicate function.

    Iterates through all elements in the subtree (including the root)
    and returns the first element that satisfies the predicate condition.

    Args:
        element: The root XML element to search from.
        predicate: A callable that takes an ET.Element and returns True
            if the element matches the desired condition.

    Returns:
        The first matching ET.Element, or None if no element satisfies
        the predicate.
    """
    for child in element.iter():
        if predicate(child):
            return child
    return None


def _find_group_by_class(root: ET.Element, class_name: str) -> ET.Element | None:
    """Finds an SVG group element (<g>) by its class attribute value.

    Searches for the first 'g' (group) element in the SVG tree that has
    a matching class attribute. Used to locate specific chart elements
    like series plots, axes, or plot areas.

    Args:
        root: The root XML element of the SVG document.
        class_name: The class attribute value to match (e.g., 'bar-plot-0').

    Returns:
        The matching SVG group element, or None if not found.
    """
    return _find_first(
        root,
        lambda element: _local_name(element.tag) == "g" and _element_class(element) == class_name,
    )


def _parse_float(value: str | None, default: float = 0.0) -> float:
    """Safely parses a string value to a float with a fallback default.

    Handles None values, empty strings, and malformed numeric strings
    by returning the default value instead of raising an exception.

    Args:
        value: The string value to parse, or None.
        default: The fallback value to return if parsing fails. Defaults to 0.0.

    Returns:
        The parsed float value, or the default if value is None or
        cannot be converted to a float.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_viewbox(root: ET.Element) -> tuple[float, float, float, float]:
    """Extracts the viewBox dimensions from an SVG root element.

    Parses the 'viewBox' attribute to obtain the SVG coordinate system
    bounds. Falls back to width/height attributes if viewBox is not defined,
    with default dimensions of 700x500 if neither are present.

    Args:
        root: The root XML element of the SVG document.

    Returns:
        A tuple of four floats (min_x, min_y, width, height) representing
        the viewBox dimensions. Defaults to (0.0, 0.0, 700.0, 500.0) if
        no dimensions are found.
    """
    raw = root.attrib.get("viewBox")
    if raw:
        parts = raw.replace(",", " ").split()
        if len(parts) == 4:
            return tuple(_parse_float(part) for part in parts)

    width = _parse_float(root.attrib.get("width"), 0.0)
    height = _parse_float(root.attrib.get("height"), 0.0)
    return (0.0, 0.0, width or 700.0, height or 500.0)


def _parse_path_points(path_data: str) -> list[tuple[float, float]]:
    """Parses SVG path data to extract coordinate points.

    Extracts x,y coordinates from 'M' (move) and 'L' (line) commands
    in SVG path 'd' attribute data. Used to recover data point positions
    from line chart SVG paths.

    Args:
        path_data: The SVG path 'd' attribute string containing commands
            like 'M100,50 L200,75'.

    Returns:
        A list of (x, y) coordinate tuples extracted from move and line
        commands. Returns an empty list if path_data is empty or None.
    """
    points: list[tuple[float, float]] = []
    for _, x_text, y_text in POINT_RE.findall(path_data or ""):
        points.append((float(x_text), float(y_text)))
    return points


def _select_monotonic_points(
    points: list[tuple[float, float]],
    expected_count: int,
) -> list[tuple[float, float]]:
    """Selects points with monotonically increasing or equal x-coordinates.

    Filters a list of coordinate points to select those that form a valid
    sequence for chart data. Points must have x-coordinates that increase
    or stay the same. Duplicate points (same x and y) are skipped, and
    points with decreasing x-values terminate selection early.

    Args:
        points: List of (x, y) coordinate tuples to filter.
        expected_count: The maximum number of points to select. Selection
            stops once this count is reached.

    Returns:
        A filtered list of (x, y) tuples with monotonically non-decreasing
        x-coordinates, up to expected_count items. Returns empty list if
        expected_count is 0 or negative.
    """
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
    """Extracts chart point data from an SVG group element for a data series.

    Parses either bar chart rectangles or line chart path data to build
    ChartPoint objects with position, value, label, and styling information.
    Handles both vertical and horizontal bar orientations.

    Args:
        group: The SVG group element containing the series visualization
            (rects for bars, path for lines). May be None.
        kind: The chart type ('bar' or 'line').
        values: List of numeric values for the data series.
        labels: List of display label strings corresponding to each value.
        chart_orientation: The bar chart orientation ('vertical' or 'horizontal').
            Only used for bar charts.

    Returns:
        A list of ChartPoint objects with coordinates, values, labels,
        and color information. Returns empty list if group is None or
        if the visualization elements cannot be found.
    """
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
    """Constrains an x-coordinate position within chart boundaries.

    Ensures that label positions stay within the visible chart area
    by clamping to a minimum and maximum bound with edge padding.

    Args:
        x: The unconstrained x-coordinate position.
        width: The total width of the chart area.
        edge_padding: The minimum distance from the chart edges to maintain.

    Returns:
        The clamped x-coordinate, constrained between edge_padding and
        (width - edge_padding), ensuring labels remain visible.
    """
    return min(max(x, edge_padding), max(width - edge_padding, edge_padding))


def build_xychart_value_labels(
    svg_markup: str,
    metadata: XyChartMetadata,
) -> tuple[list[ChartLabel], tuple[float, float, float, float]]:
    """Builds value label annotations for an XY chart from SVG markup.

    Parses SVG chart elements and metadata to compute optimal positions
    for value labels on each data point. Handles both bar and line charts
    with proper positioning based on chart orientation and value polarity.

    Args:
        svg_markup: The SVG markup string containing the chart visualization.
        metadata: XyChartMetadata object containing series information including
            kind (bar/line), values, display labels, and chart orientation.

    Returns:
        A tuple containing:
            - A list of ChartLabel objects with computed positions, text,
              colors, and text-anchor settings for rendering.
            - A tuple of four floats (min_x, min_y, width, height) representing
              the SVG viewBox dimensions. Returns ([], (0,0,0,0)) if no series
              data is present or resource limits exceeded.
    """
    if not metadata.series:
        return [], (0.0, 0.0, 0.0, 0.0)

    # 检查SVG大小上限
    if len(svg_markup) > MAX_SVG_BYTES:
        return [], (0.0, 0.0, 0.0, 0.0)

    # 检查series数量上限
    if len(metadata.series) > MAX_SERIES_COUNT:
        # 只处理前 MAX_SERIES_COUNT 个 series
        metadata.series = metadata.series[:MAX_SERIES_COUNT]

    root = ET.fromstring(svg_markup)
    viewbox = _parse_viewbox(root)
    _, _, width, height = viewbox

    labels: list[ChartLabel] = []
    series_gap = 14.0
    edge_padding = 8.0

    for series in metadata.series:
        # 检查总label数量上限
        if len(labels) >= MAX_TOTAL_LABELS:
            break

        # 检查每个series的point数量上限
        values_to_process = series.values[:MAX_POINTS_PER_SERIES]
        display_values_to_process = series.display_values[:MAX_POINTS_PER_SERIES]

        group = _find_group_by_class(root, f"{series.kind}-plot-{series.index}")
        points = _series_points_from_group(
            group,
            series.kind,
            values_to_process,
            display_values_to_process,
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
    """Annotates an SVG chart with value labels rendered as text elements.

    Takes SVG markup and chart metadata, computes label positions, and
    injects new SVG text elements into the chart. Labels are styled with
    white stroke outlines for readability over colored chart elements.

    Args:
        svg_markup: The SVG markup string containing the chart visualization.
        metadata: XyChartMetadata object containing series information.

    Returns:
        The modified SVG markup string with value labels added as a new
        group element. Returns the original markup unchanged if no labels
        are generated or resource limits exceeded.
    """
    # 检查SVG大小上限
    if len(svg_markup) > MAX_SVG_BYTES:
        return svg_markup

    labels, _ = build_xychart_value_labels(svg_markup, metadata)
    if not labels:
        return svg_markup

    # 复用第一次解析的root，避免重复解析
    try:
        root = ET.fromstring(svg_markup)
    except ET.ParseError:
        return svg_markup

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
        text.text = label.label

    parent.append(labels_group)
    return ET.tostring(root, encoding="unicode")


def _try_load_font(candidate: str, size: int):
    """Attempts to load a TrueType font file with a specified size.

    A helper function that safely attempts font loading without raising
    exceptions on failure, allowing for graceful fallback behavior.

    Args:
        candidate: The font file name or path to load (e.g., 'arial.ttf').
        size: The font size in pixels.

    Returns:
        An ImageFont object if the font loads successfully, or None if
        the font file cannot be found or opened.
    """
    try:
        return ImageFont.truetype(candidate, size=size)
    except OSError:
        return None


def _load_font(size: int):
    """Loads a font with fallback to system fonts or default.

    Attempts to load common system fonts (Arial, Segoe UI) before
    falling back to PIL's default font. Used for rendering value
    labels on PNG chart exports.

    Args:
        size: The font size in pixels.

    Returns:
        An ImageFont object. Returns the first successfully loaded
        TrueType font, or PIL's default bitmap font if all TrueType
        fonts fail to load.
    """
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
    """Overlays value labels onto a PNG chart image and saves in-place.

    Renders computed value labels directly onto a PNG image file using PIL.
    Scales label positions from SVG viewBox coordinates to PNG pixel
    coordinates and draws text with white stroke outlines for readability.

    Args:
        png_path: The file path to the PNG image to modify. The file will
            be overwritten with the labeled version.
        svg_markup: The SVG markup string used to compute label positions
            and derive scale factors.
        metadata: XyChartMetadata object containing series information.

    Returns:
        True if the overlay was successfully applied and the PNG was saved.
        Returns False if PIL is unavailable, no labels were generated, or
        the SVG viewBox dimensions are invalid.
    """
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
