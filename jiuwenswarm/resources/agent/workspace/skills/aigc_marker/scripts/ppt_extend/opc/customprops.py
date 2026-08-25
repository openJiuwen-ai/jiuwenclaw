from __future__ import annotations

from ppt_extend.oxml.customprops import CT_CustomProperties, CT_Property


class CustomProperties:
    """
    Corresponds to part named ``/docProps/core.xml``, containing the core documentproperties for this document package.
    """

    def __init__(self, element: CT_CustomProperties):
        self._element = element

    def add_property(self, key: str, value: str, fmtid: str, pid: int):
        prop = CT_Property.new(key, value, fmtid, pid)
        self._element.append(prop)