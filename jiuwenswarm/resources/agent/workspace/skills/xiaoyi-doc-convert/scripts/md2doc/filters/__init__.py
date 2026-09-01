"""Filters package for md2doc skill."""
from filters.filter import Filter
from filters.filter_manager import FilterManager, RegexFilter, create_filter_manager
from filters.common_ai_mark_filter import CommonAiMarkFilter, MdImplicitAiMarkFilter
from filters.text_filters import (
    FormulaProtectFilter,
    PdfFormulaProtectFilter,
    Md2HtmlFilter,
    HtmlTableFilter,
    HtmlCodeFilter,
    LocalPathFilter,
    ImageLink2ImgTagFilter,
    ImgBase64ToImgTagFilter
)
from filters.image_code_filter import ImageCodeFilter

__all__ = [
    'Filter',
    'FilterManager',
    'RegexFilter',
    'create_filter_manager',
    'CommonAiMarkFilter',
    'MdImplicitAiMarkFilter',
    'FormulaProtectFilter',
    'PdfFormulaProtectFilter',
    'Md2HtmlFilter',
    'HtmlTableFilter',
    'HtmlCodeFilter',
    'LocalPathFilter',
    'ImageLink2ImgTagFilter',
    'ImgBase64ToImgTagFilter',
    'ImageCodeFilter'
]