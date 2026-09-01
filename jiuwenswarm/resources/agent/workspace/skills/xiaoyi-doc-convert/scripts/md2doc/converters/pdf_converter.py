"""PDF converter for md2doc skill."""
from time import perf_counter

try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except Exception as err:
    WEASYPRINT_AVAILABLE = False
    HTML = None
    import logging
    logging.getLogger("PdfConverter").warning(f"weasyprint import failed: {str(err)}")

from converters.converter import Converter
from logger import get_logger

LOG = get_logger("PdfConverter")


class PdfConverter(Converter):
    """HTML to PDF converter."""

    def __init__(self):
        super().__init__(name="pdf")
        if not WEASYPRINT_AVAILABLE:
            LOG.warning("weasyprint is not available. PDF conversion will not work.")

    def convert(self, content: str, target_path: str, **kwargs):
        if not WEASYPRINT_AVAILABLE or HTML is None:
            raise ValueError("weasyprint is not available. Please install weasyprint with system dependencies.")

        t0 = perf_counter()
        request_id = kwargs.get("request_id", "")
        try:
            html = HTML(string=content, base_url='.')
            html.write_pdf(target_path, presentational_hints=True)
            LOG.info(f"request_id={request_id}, md2pdf done, cost {perf_counter() - t0:.2f}s.")
        except Exception as e:
            message = f"html2pdf failed: {str(e)}"
            LOG.error(f"request_id=[{request_id}], message={message}")
            raise ValueError(message) from e


pdf_converter = PdfConverter() if WEASYPRINT_AVAILABLE else None
