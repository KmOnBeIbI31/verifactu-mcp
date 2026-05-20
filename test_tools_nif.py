"""
Tests de integracion: MCP tools rechazan NIFs invalidos antes de generar/enviar.
"""

import asyncio
import json

from verifactu_mcp.server import call_tool


def _run(name, args):
    result = asyncio.run(call_tool(name, args))
    return json.loads(result[0].text)


# ---------------------------------------------------------------------------
# generate_invoice_xml
# ---------------------------------------------------------------------------

def test_generate_rechaza_emisor_invalido():
    payload = {
        "id_emisor": "B12345670",  # CIF con control incorrecto
        "num_serie": "2025/001",
        "fecha": "15-05-2025",
        "descripcion": "Servicios test",
        "base_imponible": 100.0,
        "tipo_iva": 21.0,
    }
    r = _run("generate_invoice_xml", payload)
    assert "error" in r, f"Debe devolver error con emisor invalido, devolvio: {r}"
    assert "id_emisor" in r["error"].lower() or "nif" in r["error"].lower()
    print("OK test_generate_rechaza_emisor_invalido")


def test_generate_rechaza_receptor_invalido():
    payload = {
        "id_emisor": "B12345674",  # valido
        "num_serie": "2025/001",
        "fecha": "15-05-2025",
        "descripcion": "Servicios test",
        "base_imponible": 100.0,
        "tipo_iva": 21.0,
        "nif_receptor": "12345678A",  # DNI con letra incorrecta
    }
    r = _run("generate_invoice_xml", payload)
    assert "error" in r, f"Debe devolver error con receptor invalido, devolvio: {r}"
    assert "receptor" in r["error"].lower() or "nif" in r["error"].lower()
    print("OK test_generate_rechaza_receptor_invalido")


def test_generate_acepta_nifs_validos():
    payload = {
        "id_emisor": "B12345674",
        "num_serie": "2025/001",
        "fecha": "15-05-2025",
        "descripcion": "Servicios test",
        "base_imponible": 100.0,
        "tipo_iva": 21.0,
        "nif_receptor": "12345678Z",
    }
    r = _run("generate_invoice_xml", payload)
    assert "xml" in r, f"Debe generar XML con NIFs validos, devolvio: {r}"
    assert "huella" in r
    print("OK test_generate_acepta_nifs_validos")


def test_generate_acepta_sin_receptor():
    payload = {
        "id_emisor": "B12345674",
        "num_serie": "2025/001",
        "fecha": "15-05-2025",
        "descripcion": "Servicios test",
        "base_imponible": 100.0,
        "tipo_iva": 21.0,
    }
    r = _run("generate_invoice_xml", payload)
    assert "xml" in r
    print("OK test_generate_acepta_sin_receptor")


# ---------------------------------------------------------------------------
# cancel_invoice
# ---------------------------------------------------------------------------

def test_cancel_rechaza_emisor_invalido():
    payload = {
        "id_emisor": "B12345670",
        "num_serie": "2025/001",
        "fecha": "15-05-2025",
        "id_registro_anulado": 42,
    }
    r = _run("cancel_invoice", payload)
    assert "error" in r, f"Debe devolver error con emisor invalido, devolvio: {r}"
    print("OK test_cancel_rechaza_emisor_invalido")
