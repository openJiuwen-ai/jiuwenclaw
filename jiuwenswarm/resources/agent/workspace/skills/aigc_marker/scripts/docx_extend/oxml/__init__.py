from docx.oxml.ns import pfxmap, nsmap
from .customprops import CT_Lpwstr

new_ns_map = {
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "w10": "urn:schemas-microsoft-com:office:word",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
}
nsmap.update(new_ns_map)
pfxmap.update({value: key for key, value in new_ns_map.items()})
