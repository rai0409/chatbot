"""Stage 13: findings of the security static check (bandit). Office files
come from uploads, so their XML is parsed with defusedxml: entity
declarations (billion laughs, external entities) are refused. Synthetic data
only.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONVERTERS = ("docx_converter", "pptx_converter", "xlsx_converter")


def _docx(path: Path, document_xml: str) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
    return path


def test_plain_docx_still_converts(tmp_path):
    from rag_core.document_converters.docx_converter import convert_docx

    xml = f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>経費精算の締め日は毎月25日です。</w:t></w:r></w:p></w:body></w:document>'
    chunks = convert_docx(_docx(tmp_path / "plain.docx", xml))
    assert any("毎月25日" in c.get("text", "") for c in chunks)


@pytest.mark.parametrize("doctype", [
    '<!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>',
    '<!DOCTYPE d [<!ENTITY ext SYSTEM "file:///etc/hostname">]>',
])
def test_docx_with_entity_declarations_is_refused(tmp_path, doctype):
    from defusedxml import DefusedXmlException

    from rag_core.document_converters.docx_converter import convert_docx

    xml = f'<?xml version="1.0"?>{doctype}<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>'
    with pytest.raises(DefusedXmlException):
        convert_docx(_docx(tmp_path / "entity.docx", xml))


@pytest.mark.parametrize("name", CONVERTERS)
def test_office_converters_parse_only_with_defusedxml(name):
    import importlib

    from defusedxml.ElementTree import fromstring

    module = importlib.import_module(f"rag_core.document_converters.{name}")
    assert module._safe_fromstring is fromstring
    source = (ROOT / "rag_core" / "document_converters" / f"{name}.py").read_text(encoding="utf-8")
    assert "ET.fromstring(" not in source and "ET.parse(" not in source


def test_defusedxml_is_a_dependency():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("defusedxml") for line in lines)
