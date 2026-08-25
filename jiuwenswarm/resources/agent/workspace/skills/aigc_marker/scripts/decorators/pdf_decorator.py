"""PDF AIGC decorator."""
import os
import stat

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, DictionaryObject

from decorators.common import RawTextStringObject, get_aigc_signature, is_aigc_complete, parse_aigc_json


class PdfAigcDecorator:
    """PDF AIGC decorator - adds hidden AIGC mark to PDF documents."""

    def __init__(self):
        self.name = "pdf_aigc_decorator"

    def decorate(self, file_path: str, content: str, add_visible_mark: bool = True):
        """Add AIGC mark to PDF file."""
        existing = self._get_aigc_data(file_path)
        if existing and is_aigc_complete(existing):
            print(f"  [PDF] AIGC mark complete, skipping")
            return
        try:
            aigc_signature = get_aigc_signature(content)
            aigc_signature = aigc_signature.replace("\\", "\\\\")
            self._add_aigc_mark(file_path, {"AIGC": aigc_signature, "Creator": "", "Producer": ""}, add_visible_mark)
            print(f"  [PDF] AIGC mark added successfully")
        except Exception as e:
            print(f"  [PDF] Warning: Failed to add AIGC mark: {str(e)}")

    def _get_aigc_data(self, file_path: str) -> dict | None:
        """Read and parse AIGC metadata from PDF info dictionary."""
        try:
            with open(file_path, "rb") as f:
                reader = PdfReader(f)
                if reader.metadata:
                    for key, value in reader.metadata.items():
                        if "AIGC" in key:
                            return parse_aigc_json(str(value))
        except Exception:
            return None
        return None

    def _add_aigc_mark(self, input_path: str, data: dict, add_visible_mark: bool = True) -> None:
        """Add or update metadata and optional watermark in a PDF file."""
        # Read input PDF
        reader = PdfReader(input_path)
        writer = PdfWriter()

        # Copy all pages
        writer.clone_reader_document_root(reader)

        # Add visible watermark if requested
        if add_visible_mark:
            self._add_visible_watermark(writer, reader)

        # Ensure writer._info is a DictionaryObject
        if writer._info is None:
            writer._info = DictionaryObject()

        # Preserve existing metadata
        if reader.metadata:
            for key, value in reader.metadata.items():
                pdf_key = NameObject(key)
                if pdf_key not in writer._info:
                    writer._info[pdf_key] = RawTextStringObject(str(value))

        # Add/update new metadata
        for key, value in data.items():
            pdf_key = NameObject(f"/{key.lstrip('/')}")
            writer._info[pdf_key] = RawTextStringObject(str(value))

        # Write output (overwrite input file)
        file_flags = os.O_WRONLY | os.O_CREAT
        file_mode = stat.S_IWUSR | stat.S_IRUSR
        try:
            with os.fdopen(os.open(input_path, file_flags, file_mode), 'wb') as f:
                writer.write(f)
        except Exception as e:
            raise Exception(f"PDF add metadata failed: {str(e)}") from e

    def _add_visible_watermark(self, writer: PdfWriter, reader: PdfReader):
        """Add visible '内容由AI生成' watermark to PDF pages using reportlab-generated watermark."""
        # 水印文件路径
        watermark_dir = os.path.expanduser("~/.jiuwenswarm/agent/workspace/.aigc")
        os.makedirs(watermark_dir, exist_ok=True)
        watermark_path = os.path.join(watermark_dir, "aigc_watermark.pdf")

        # 如果水印文件不存在，生成它
        if not os.path.exists(watermark_path):
            from pdf_extend.pdf_watermark import create_watermark_pdf
            create_watermark_pdf(
                output_path=watermark_path,
                text="内容由AI生成",
                font_size=10.5,
                opacity=1.0,
                angle=0,
                color=(0, 0, 0),  # 黑色
                position='bottom-center'
            )

        # 读取水印
        watermark_reader = PdfReader(watermark_path)
        watermark_page = watermark_reader.pages[0]

        # 为每一页添加水印（合并到底层）
        for page in writer.pages:
            # 将水印页合并到当前页（水印在底层）
            page.merge_page(watermark_page, over=False)
