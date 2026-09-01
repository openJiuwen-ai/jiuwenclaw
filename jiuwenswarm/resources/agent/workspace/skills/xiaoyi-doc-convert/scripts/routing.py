"""
Document conversion routing module.

Defines the conversion graph and finds conversion paths between formats.
"""

from typing import List, Dict, Optional, Tuple

# Conversion graph: source -> {target: adapter_name}
CONVERSION_GRAPH = {
    'html': {'pdf': 'html2pdf', 'md': 'html2md'},
    'docx': {'pdf': 'soffice'},
    'pptx': {'pdf': 'soffice'},
    'pdf': {'pptx': 'soffice'},
    'md': {'docx': 'md2doc', 'xlsx': 'md2doc'},
    # Legacy format upgrades
    'doc': {'docx': 'soffice', 'pdf': 'soffice'},
    'ppt': {'pptx': 'soffice', 'pdf': 'soffice'},
}

# Format aliases (normalized to standard format names)
FORMAT_ALIASES = {
    'htm': 'html',
    'markdown': 'md',
    'txt': 'md',  # Text files treated as markdown
    'word': 'docx',
    'excel': 'xlsx',
    'xls': 'xlsx',
}


def normalize_format(fmt: str) -> str:
    """
    Normalize format name to standard form.

    Args:
        fmt: Input format string (e.g., 'HTML', 'htm', 'word')

    Returns:
        Normalized format name (lowercase, alias resolved)
    """
    fmt_lower = fmt.lower().lstrip('.')
    return FORMAT_ALIASES.get(fmt_lower, fmt_lower)


def get_supported_formats() -> List[str]:
    """Get list of all supported source formats."""
    return list(CONVERSION_GRAPH.keys())


def get_supported_targets(source: str) -> List[str]:
    """Get list of supported target formats for a source format."""
    source = normalize_format(source)
    return list(CONVERSION_GRAPH.get(source, {}).keys())


def find_conversion_path(source: str, target: str) -> List[Dict[str, str]]:
    """
    Find conversion path from source to target format.

    Uses BFS to find the shortest path in the conversion graph.

    Args:
        source: Source format (e.g., 'html', 'docx')
        target: Target format (e.g., 'pdf', 'pptx')

    Returns:
        List of conversion steps, each step is a dict with:
        - 'from': source format
        - 'to': target format
        - 'adapter': adapter name to use

        Returns empty list if no path found.
    """
    source = normalize_format(source)
    target = normalize_format(target)

    # Direct conversion available
    if source in CONVERSION_GRAPH and target in CONVERSION_GRAPH[source]:
        return [{
            'from': source,
            'to': target,
            'adapter': CONVERSION_GRAPH[source][target]
        }]

    # BFS to find path
    visited = {source}
    queue = [(source, [])]

    while queue:
        current, path = queue.pop(0)

        if current not in CONVERSION_GRAPH:
            continue

        for next_fmt, adapter in CONVERSION_GRAPH[current].items():
            if next_fmt in visited:
                continue

            new_path = path + [{
                'from': current,
                'to': next_fmt,
                'adapter': adapter
            }]

            if next_fmt == target:
                return new_path

            visited.add(next_fmt)
            queue.append((next_fmt, new_path))

    return []


def is_conversion_supported(source: str, target: str) -> bool:
    """
    Check if conversion from source to target is supported.

    Args:
        source: Source format
        target: Target format

    Returns:
        True if conversion is supported, False otherwise
    """
    path = find_conversion_path(source, target)
    return len(path) > 0


def get_conversion_matrix() -> Dict[str, List[str]]:
    """
    Get the full conversion matrix showing all supported conversions.

    Returns:
        Dict mapping source formats to lists of supported target formats
    """
    matrix = {}
    for source in CONVERSION_GRAPH:
        targets = set()
        # Direct conversions
        targets.update(CONVERSION_GRAPH[source].keys())
        # Indirect conversions (1 hop)
        for direct_target in list(targets):
            if direct_target in CONVERSION_GRAPH:
                for indirect_target in CONVERSION_GRAPH[direct_target]:
                    targets.add(indirect_target)
        matrix[source] = sorted(targets)
    return matrix
