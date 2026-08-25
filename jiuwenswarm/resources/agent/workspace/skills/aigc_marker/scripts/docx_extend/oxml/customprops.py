"""Custom element classes for core properties-related XML elements."""

from __future__ import annotations

from typing import cast

from docx.oxml.ns import nsdecls, qn
from docx.oxml.parser import parse_xml
from docx.oxml.xmlchemy import BaseOxmlElement


class CT_Lpwstr(BaseOxmlElement):
    @classmethod
    def new(cls, value: str) -> CT_Lpwstr:
        xml = cls._lpwstr_xml(value)
        lpwstr = cast(CT_Lpwstr, parse_xml(xml))
        return lpwstr

    @classmethod
    def _lpwstr_xml(cls, value) -> str:
        return (
                "<vt:lpwstr %s>%s</vt:lpwstr>"
                % (nsdecls("vt"), value)
        )


class CT_Property(BaseOxmlElement):
    _property_tmpl = "<property fmtid=\"{%s}\" pid=\"%d\" name=\"%s\"/>"

    @classmethod
    def new(cls, key: str, value: str, ftmid: str, pid: int) -> CT_Property:
        """Return a new `<cp:coreProperties>` element."""
        xml = cls._property_tmpl % (ftmid, pid, key)
        prop = cast(CT_Property, parse_xml(xml))
        lpwstr = CT_Lpwstr.new(value)
        prop.append(lpwstr)
        return prop


class CT_CustomProperties(BaseOxmlElement):
    @classmethod
    def new(cls):
        xml = cls._properties_xml()
        custom_properties = parse_xml(xml)
        return custom_properties

    def add_property(self, prop) -> CT_Property:
        self.append(prop)
        return prop

    @classmethod
    def _properties_xml(cls) -> str:
        return (
                "<Properties xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/custom-properties\" %s/>\n"
                % (nsdecls("vt"))
        )
