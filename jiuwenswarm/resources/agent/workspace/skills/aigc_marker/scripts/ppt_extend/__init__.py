from pptx.opc.package import PartFactory
from pptx.opc.constants import CONTENT_TYPE as CT

from ppt_extend.parts.customprops import CustomPropertiesPart

PartFactory.part_type_for[CT.OFC_CUSTOM_PROPERTIES] = CustomPropertiesPart
