from pptx.oxml.ns import _nsmap

new_ns_map = {
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
}
_nsmap.update(new_ns_map)
