from __future__ import annotations

from docx.opc.constants import CONTENT_TYPE as CT
from docx.opc.package import OpcPackage
from docx.opc.packuri import PackURI
from docx.opc.part import XmlPart

from docx_extend.opc.customprops import CustomProperties
from docx_extend.oxml.customprops import CT_CustomProperties


class CustomPropertiesPart(XmlPart):
    """Corresponds to part named ``/docProps/custom.xml``.
    """

    @classmethod
    def default(cls, package: OpcPackage):
        custom_properties_part = cls._new(package)
        return custom_properties_part

    @property
    def custom_properties(self) -> CustomProperties:
        return CustomProperties(self.element)

    @property
    def next_id(self) -> int:
        id_str_lst = self._element.xpath("//@pid")
        used_ids = [int(id_str) for id_str in id_str_lst if id_str.isdigit()]
        if not used_ids:
            return 2
        return max(used_ids) + 1

    @classmethod
    def _new(cls, package: OpcPackage) -> CustomPropertiesPart:
        partname = PackURI("/docProps/custom.xml")
        content_type = CT.OFC_CUSTOM_PROPERTIES
        custom_properties = CT_CustomProperties.new()
        return CustomPropertiesPart(partname, content_type, custom_properties, package)
