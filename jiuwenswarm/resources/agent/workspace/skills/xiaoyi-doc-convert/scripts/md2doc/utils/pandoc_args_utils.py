"""Pandoc extra args utilities."""
import os
from typing import Dict

from config import CONFIG
from logger import get_logger
from config import CONFIG_PATH

config = CONFIG.md2doc_config

LOG = get_logger("PandocArgsUtils")


def get_pandoc_extra_args(tgt_file_type: str = "docx", device_type: str = "pc") -> list:
    """Get pandoc extra args based on configuration.

    Args:
        tgt_file_type: Target file type (docx, pdf, etc.)
        device_type: Device type (pc, phone, etc.)

    Returns:
        List of extra arguments for pandoc
    """

    templates = config.get('md2doc_templates', {})
    template = templates.get('default', 'custom-template.docx')

    # Use absolute path for reference-doc only if template exists
    template_path = f"{CONFIG_PATH}/{template}"
    if os.path.exists(template_path):
        extra_args = [f"--reference-doc={template_path}"]
    else:
        LOG.warning(f"Template file not found: {template_path}, using default pandoc styling")
        extra_args = []

    # Add user-configured extra args with absolute paths
    user_extra_args = config.get('md2doc_pandoc_extra_args', [])
    if user_extra_args:
        for arg in user_extra_args:
            if arg.startswith("--lua-filter="):
                filter_name = arg.split("=")[-1]
                extra_args.append(f"--lua-filter={CONFIG_PATH}/{filter_name}")
            else:
                extra_args.append(arg)

    return extra_args


def create_regex_filters_config() -> Dict[str, Dict[str, str]]:
    """Create regex filter configurations from md2doc.yaml.

    Reads md2doc_content_filters from config/md2doc.yaml and converts
    pattern/target format to pattern/replacement format.

    Returns:
        Dictionary of filter configurations keyed by filter name
    """
    # 延迟导入避免循环依赖
    from config import CONFIG
    config = CONFIG.md2doc_config
    filters_config = {}

    content_filters = config.get('md2doc_content_filters', [])
    for filter_item in content_filters:
        name = filter_item.get('name')
        pattern = filter_item.get('pattern', '')
        target = filter_item.get('target', '')
        description = filter_item.get('description', '')

        if name and pattern:
            filters_config[name] = {
                "pattern": pattern,
                "replacement": target,
                "description": description
            }

    LOG.debug(f"Loaded {len(filters_config)} regex filters from config")
    return filters_config
