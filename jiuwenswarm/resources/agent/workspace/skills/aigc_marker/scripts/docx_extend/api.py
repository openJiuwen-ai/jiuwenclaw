from docx.document import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT

from docx_extend.opc.customprops import CustomProperties
from docx_extend.opc.parts.customprops import CustomPropertiesPart


class DocumentExtend:

    def __init__(self, doc: Document):
        self._doc = doc
        super().__init__()

    @property
    def custom_properties(self) -> CustomProperties:
        part = self.custom_properties_part
        return part.custom_properties

    @property
    def custom_properties_part(self) -> CustomPropertiesPart:
        package = self._doc._part.package
        try:
            return package.part_related_by(RT.CUSTOM_PROPERTIES)
        except KeyError:
            core_properties_part = CustomPropertiesPart.default(package)
            package.relate_to(core_properties_part, RT.CUSTOM_PROPERTIES)
            return core_properties_part
