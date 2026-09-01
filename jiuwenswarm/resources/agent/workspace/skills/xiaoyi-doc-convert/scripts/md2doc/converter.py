import os
import sys
import uuid
from pathlib import Path

# Add current directory to path for imports (md2doc uses top-level absolute imports)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CONFIG
from utils.pandoc_args_utils import create_regex_filters_config
from filters import (
    create_filter_manager,
    CommonAiMarkFilter,
    FormulaProtectFilter,
    PdfFormulaProtectFilter,
    Md2HtmlFilter,
    HtmlTableFilter,
    HtmlCodeFilter,
    LocalPathFilter,
    ImageLink2ImgTagFilter,
    ImgBase64ToImgTagFilter,
    ImageCodeFilter
)
from converters import docx_converter, pdf_converter, excel_converter
from logger import get_logger

LOG = get_logger("Converter")


def _get_filter_chain(target_format: str) -> list:
    """Get filter chain for target format."""
    common_filters = CONFIG.get('md2doc_content_filters_common', [])

    if target_format == 'docx':
        specific_filters = CONFIG.get('md2doc_content_filters_word', [])
    elif target_format == 'pdf':
        specific_filters = CONFIG.get('md2doc_content_filters_pdf', [])
    elif target_format == 'xlsx':
        specific_filters = CONFIG.get('md2doc_content_filters_excel', [])
    else:
        specific_filters = []

    return common_filters + specific_filters


def _register_filters(filter_manager):
    """Register all filters to filter manager."""
    ai_mark_content = CONFIG.get('md2doc_ai_mark_content', "内容由AI生成")

    # Register custom filters
    filter_manager.register_filter(CommonAiMarkFilter(ai_mark_content))
    filter_manager.register_filter(FormulaProtectFilter())
    filter_manager.register_filter(PdfFormulaProtectFilter())
    filter_manager.register_filter(Md2HtmlFilter())
    filter_manager.register_filter(HtmlTableFilter())
    filter_manager.register_filter(HtmlCodeFilter())
    filter_manager.register_filter(LocalPathFilter())
    filter_manager.register_filter(ImageLink2ImgTagFilter())
    filter_manager.register_filter(ImgBase64ToImgTagFilter())
    filter_manager.register_filter(ImageCodeFilter())

    # Register regex filters from config
    regex_config = create_regex_filters_config()
    for name, config in regex_config.items():
        from filters import RegexFilter
        filter_manager.register_filter(RegexFilter(
            name=name,
            pattern=config['pattern'],
            replacement=config['replacement'],
            description=config['description']
        ))


def _process_content(content: str, target_format: str, request_id: str) -> str:
    """Process content through filter chain."""
    filter_manager = create_filter_manager({})
    _register_filters(filter_manager)

    filter_chain = _get_filter_chain(target_format)
    LOG.info(f"{request_id}, applying filters: {filter_chain}")

    return filter_manager.apply_filters(content, filter_chain, request_id)


def _convert_content(content: str, target_format: str, output_path: str, request_id: str, device_type: str = "pc"):
    """Convert content to target format."""
    if target_format == 'docx':
        docx_converter.convert(content, output_path, request_id=request_id, device_type=device_type)
    elif target_format == 'pdf':
        if pdf_converter is None:
            raise ValueError("PDF converter is not available. Please install weasyprint with system dependencies.")
        pdf_converter.convert(content, output_path, request_id=request_id)
    elif target_format == 'xlsx':
        excel_converter.convert(content, output_path, request_id=request_id)
    else:
        raise ValueError(f"Unsupported target format: {target_format}")


def convert_markdown(input_path: str, output_path: str, target_format: str, device_type: str = "pc") -> None:
    """Convert a markdown file to the specified target format."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    request_id = str(uuid.uuid4())[:8]
    LOG.info(f"Starting conversion: {input_path} -> {target_format}")

    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    LOG.info(f"{request_id}, processing content...")
    processed_content = _process_content(content, target_format, request_id)

    LOG.info(f"{request_id}, converting to {target_format}...")
    _convert_content(processed_content, target_format, output_path, request_id, device_type)

    LOG.info(f"Conversion successful!")
    LOG.info(f"  Input:  {input_path}")
    LOG.info(f"  Output: {output_path}")
