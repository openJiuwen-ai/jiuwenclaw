from pptx.presentation import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

from ppt_extend.opc.customprops import CustomProperties
from ppt_extend.parts.customprops import CustomPropertiesPart


class PresentationExtend:

    def __init__(self, prs: Presentation):
        self._prs = prs
        super().__init__()

    @property
    def custom_properties(self) -> CustomProperties:
        part = self.custom_properties_part
        return part.custom_properties

    @property
    def custom_properties_part(self) -> CustomPropertiesPart:
        package = self._prs._part.package
        try:
            return package.part_related_by(RT.CUSTOM_PROPERTIES)
        except KeyError:
            core_properties_part = CustomPropertiesPart.default(package)
            package.relate_to(core_properties_part, RT.CUSTOM_PROPERTIES)
            return core_properties_part
