"""PPT AIGC decorator."""
import zipfile
from xml.etree import ElementTree as ET

from ppt_extend.aigc import add_aigc_mark_to_pptx

from decorators.common import get_aigc_signature, is_aigc_complete, parse_aigc_json


class PptAigcDecorator:
    """PPT AIGC decorator - adds hidden AIGC mark to PowerPoint presentations."""

    def __init__(self):
        self.name = "ppt_aigc_decorator"

    def decorate(self, file_path: str, content: str, add_visible_mark: bool = True):
        """Add AIGC mark to PPTX file."""
        existing = self._get_aigc_data(file_path)
        if existing and is_aigc_complete(existing):
            print(f"  [PPT] AIGC mark complete, skipping")
            return
        try:
            aigc_signature = get_aigc_signature(content)
            add_aigc_mark_to_pptx(file_path, file_path, aigc_signature, add_visible_mark)
            print(f"  [PPT] AIGC mark added successfully")
        except Exception as e:
            print(f"  [PPT] Warning: Failed to add AIGC mark: {str(e)}")

    def _get_aigc_data(self, file_path: str) -> dict | None:
        """Read and parse AIGC metadata from PPTX custom properties."""
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                if 'docProps/custom.xml' not in zf.namelist():
                    return None
                custom_xml = zf.read('docProps/custom.xml')
                root = ET.fromstring(custom_xml)
                for elem in root.iter():
                    if elem.get('name') == 'AIGC':
                        for child in elem:
                            if child.text:
                                return parse_aigc_json(child.text)
                        return None
        except (zipfile.BadZipFile, ET.ParseError, KeyError):
            return None
        return None
