"""DOCX converter for md2doc skill."""
from converters.converter import Converter
from utils.pandoc_args_utils import get_pandoc_extra_args
from logger import get_logger
from utils.pandoc_tool import create_docx

LOG = get_logger("DocxConverter")


class DocxConverter(Converter):
    """Converter for generating docx files."""

    def __init__(self):
        super().__init__(name="docx")

    def convert(self, content: str, target_path: str, **kwargs):
        request_id = kwargs.get("request_id", "")
        device_type = kwargs.get("device_type", "pc")
        LOG.info(f"request_id={request_id}, starting docx conversion")

        # Get configurable extra args
        extra_args = get_pandoc_extra_args(tgt_file_type="docx", device_type=device_type)
        LOG.info(f"request_id={request_id}, using pandoc extra args: {extra_args}")

        create_docx(request_id, content, target_path, extra_args)


docx_converter = DocxConverter()
