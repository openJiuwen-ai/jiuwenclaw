from __future__ import annotations

from pptx.opc.constants import CONTENT_TYPE as CT
from pptx.opc.package import XmlPart
from pptx.opc.packuri import PackURI
from pptx.package import Package

from ppt_extend.opc.customprops import CustomProperties
from ppt_extend.oxml.customprops import CT_CustomProperties


class CustomPropertiesPart(XmlPart):
    """Corresponds to part named ``/docProps/custom.xml``.
    """
    _element: CT_CustomProperties

    @classmethod
    def default(cls, package: Package):
        custom_properties_part = cls._new(package)
        return custom_properties_part

    @property
    def custom_properties(self) -> CustomProperties:
        return CustomProperties(self._element)

    @property
    def next_id(self) -> int:
        id_str_lst = self._element.xpath("//@pid")
        used_ids = [int(id_str) for id_str in id_str_lst if id_str.isdigit()]
        if not used_ids:
            return 2
        return max(used_ids) + 1

    @classmethod
    def _new(cls, package: Package) -> CustomPropertiesPart:
        partname = PackURI("/docProps/custom.xml")
        content_type = CT.OFC_CUSTOM_PROPERTIES
        custom_properties = CT_CustomProperties.new()
        return CustomPropertiesPart(partname, content_type, package, custom_properties)
