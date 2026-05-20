"""
Tests de la capa de persistencia (state.db) y del encadenamiento automático
de la huella entre llamadas a send_invoice / generate_invoice_xml.
"""
import asyncio
import json
from unittest.mock import patch

from verifactu_mcp import server
from verifactu_mcp.server import (
    call_tool,
    get_last_huella,
    save_huella,
    _state_db_path,
)


def _run(name, args):
    result = asyncio.run(call_tool(name, args))
    return json.loads(result[0].text)


# ---------------------------------------------------------------------------
# Capa de DB
# ---------------------------------------------------------------------------

def test_state_db_usa_override_env():
    """La fixture autouse debe redirigir la DB a tmp_path."""
    path = str(_state_db_path())
    assert "state.db" in path
    # No debe ser la ruta default del home del usuario
    assert ".verifactu-mcp" not in path or "Temp" in path or "tmp" in path.lower()


def test_get_last_huella_vacio_si_no_hay_registros():
    assert get_last_huella("B12345674") == ""


def test_save_y_get_last_huella_round_trip():
    save_huella("B12345674", "2025/001", "15-05-2025", "A" * 64)
    assert get_last_huella("B12345674") == "A" * 64


def test_get_last_huella_devuelve_la_ultima_insertada():
    save_huella("B12345674", "2025/001", "15-05-2025", "A" * 64)
    save_huella("B12345674", "2025/002", "16-05-2025", "B" * 64)
    save_huella("B12345674", "2025/003", "17-05-2025", "C" * 64)
    assert get_last_huella("B12345674") == "C" * 64


def test_get_last_huella_aisla_por_emisor():
    save_huella("B12345674", "2025/001", "15-05-2025", "A" * 64)
    save_huella("12345678Z", "X-001", "15-05-2025", "B" * 64)
    assert get_last_huella("B12345674") == "A" * 64
    assert get_last_huella("12345678Z") == "B" * 64


# ---------------------------------------------------------------------------
# Encadenamiento automático en send_invoice
# ---------------------------------------------------------------------------

def _factura():
    return {
        "id_emisor": "B12345674",
        "num_serie": "2025/001",
        "fecha": "15-05-2025",
        "descripcion": "Servicios test",
        "base_imponible": 100.0,
        "tipo_iva": 21.0,
    }


def test_send_invoice_persiste_huella_tras_exito():
    respuesta_fake = {"data": {"items": [{"id": 1}]}}
    with patch.object(server.client, "crear_alta", return_value=respuesta_fake):
        r = _run("send_invoice", _factura())
    assert r["ok"] is True
    # Si la API no devuelve Huella, fallback a la calculada local
    assert get_last_huella("B12345674") == r["huella"]


def test_send_invoice_persiste_huella_canonica_de_api_cuando_disponible():
    """Si la API devuelve Huella en el response, ESA debe persistirse,
    no la calculada localmente."""
    huella_canonica = "F" * 64
    respuesta_fake = {
        "data": {"items": [{"id": 1, "Huella": huella_canonica}]}
    }
    with patch.object(server.client, "crear_alta", return_value=respuesta_fake):
        r = _run("send_invoice", _factura())
    assert r["ok"] is True
    assert r["huella"] == huella_canonica
    assert r["huella_local_estimada"] != huella_canonica
    assert get_last_huella("B12345674") == huella_canonica


def test_send_invoice_no_persiste_si_falla():
    import httpx
    with patch.object(
        server.client,
        "crear_alta",
        side_effect=httpx.HTTPStatusError(
            "500",
            request=httpx.Request("POST", "http://fake"),
            response=httpx.Response(500),
        ),
    ):
        r = _run("send_invoice", _factura())
    assert r["ok"] is False
    assert get_last_huella("B12345674") == ""


def test_send_invoice_encadena_automaticamente_con_la_huella_previa():
    """Segunda factura usa la huella de la primera como huella_anterior."""
    respuesta_fake = {"data": {"items": [{"id": 1}]}}
    with patch.object(server.client, "crear_alta", return_value=respuesta_fake) as m:
        r1 = _run("send_invoice", _factura())

        factura_2 = _factura()
        factura_2["num_serie"] = "2025/002"
        r2 = _run("send_invoice", factura_2)

    assert r1["ok"] and r2["ok"]
    assert r2["huella_anterior"] == r1["huella"], (
        "Segunda factura debe encadenar con la primera automáticamente"
    )
    # Payload enviado a la API también debe llevar la huella correcta
    payload_2 = m.call_args_list[1].args[0]
    assert payload_2["Huella"] == r2["huella"]


def test_send_invoice_respeta_huella_anterior_explicita():
    """Si el usuario pasa huella_anterior, se respeta sin lookup."""
    save_huella("B12345674", "2025/000", "14-05-2025", "Z" * 64)
    respuesta_fake = {"data": {"items": [{"id": 1}]}}
    payload = _factura()
    payload["huella_anterior"] = "F" * 64
    with patch.object(server.client, "crear_alta", return_value=respuesta_fake):
        r = _run("send_invoice", payload)
    assert r["ok"] is True
    assert r["huella_anterior"] == "F" * 64


# ---------------------------------------------------------------------------
# generate_invoice_xml usa lookup pero NO persiste
# ---------------------------------------------------------------------------

def test_generate_xml_usa_huella_previa_sin_persistir():
    save_huella("B12345674", "2025/000", "14-05-2025", "Z" * 64)
    r = _run("generate_invoice_xml", _factura())
    assert "xml" in r
    assert r["huella_anterior"] == "Z" * 64
    # No debe modificar la DB
    assert get_last_huella("B12345674") == "Z" * 64


# ---------------------------------------------------------------------------
# Tool get_last_hash
# ---------------------------------------------------------------------------

def test_tool_get_last_hash_sin_registros():
    r = _run("get_last_hash", {"id_emisor": "B12345674"})
    assert r["ok"] is True
    assert r["huella"] == ""
    assert r["tiene_registros"] is False


def test_tool_get_last_hash_con_registros():
    save_huella("B12345674", "2025/001", "15-05-2025", "D" * 64)
    r = _run("get_last_hash", {"id_emisor": "B12345674"})
    assert r["ok"] is True
    assert r["huella"] == "D" * 64
    assert r["tiene_registros"] is True


def test_tool_get_last_hash_rechaza_nif_invalido():
    r = _run("get_last_hash", {"id_emisor": "B12345670"})
    assert r["ok"] is False
    assert "inválido" in r["error"] or "invalido" in r["error"].lower()
