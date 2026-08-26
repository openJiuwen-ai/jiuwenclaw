"""Excel converter for md2doc skill."""
from converters.converter import Converter
from logger import get_logger
from utils.excel_utils import html2df, df2excel, adjust_wb

LOG = get_logger("ExcelConverter")


class ExcelConverter(Converter):
    """Converter for generating excel files."""

    def __init__(self):
        super().__init__(name="xlsx")

    def convert(self, content: str, target_path: str, **kwargs):
        request_id = kwargs.get("request_id", "")
        LOG.debug(f"request_id={request_id}, starting excel conversion")
        tables = html2df(content, request_id)
        df2excel(tables, target_path, **kwargs)
        adjust_wb(target_path, request_id)
        LOG.debug(f"request_id={request_id}, md2excel done.")


excel_converter = ExcelConverter()
