from docx.opc.part import PartFactory
from docx.opc.constants import CONTENT_TYPE as CT
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docx_extend.opc.parts.customprops import CustomPropertiesPart

PartFactory.part_type_for[CT.OFC_CUSTOM_PROPERTIES] = CustomPropertiesPart
