"""
Tests de las 3 herramientas que pegan a la API AEAT (mockeada).
Cubre: send_invoice, cancel_invoice, check_invoice_status.
"""

import asyncio
import json
from unittest.mock import patch

import httpx

from verifactu_mcp import server
from verifactu_mcp.server import call_tool


def _run(name, args):
    result = asyncio.run(call_tool(name, args))
    return json.loads(result[0].text)


def _factura_valida():
    return {
        "id_emisor": "B12345674",
        "num_serie": "2025/001",
        "fecha": "15-05-2025",
        "descripcion": "Servicios test",
        "base_imponible": 100.0,
        "tipo_iva": 21.0,
    }


# ---------------------------------------------------------------------------
# send_invoice
# ---------------------------------------------------------------------------

def test_send_invoice_exito():
    respuesta_fake = {"data": {"items": [{"id": 12345}]}}
    with patch.object(server.client, "crear_alta", return_value=respuesta_fake) as m:
        r = _run("send_invoice", _factura_valida())
    assert r["ok"] is True, f"Debe ok=True, obtuvo {r}"
    assert r["registro_id"] == 12345, f"registro_id esperado 12345, obtuvo {r.get('registro_id')}"
    assert "huella" in r and len(r["huella"]) == 64
    m.assert_called_once()
    # Payload contiene campos clave AEAT
    payload = m.call_args.args[0] if m.call_args.args else m.call_args.kwargs.get("payload")
    assert payload["IDEmisorFactura"] == "B12345674"
    assert payload["Huella"] == r["huella"]
    print("OK test_send_invoice_exito")


def test_send_invoice_http_error():
    with patch.object(
        server.client,
        "crear_alta",
        side_effect=httpx.HTTPStatusError(
            "401 Unauthorized",
            request=httpx.Request("POST", "http://fake"),
            response=httpx.Response(401, json={"error": "token invalido"}),
        ),
    ):
        r = _run("send_invoice", _factura_valida())
    assert r["ok"] is False
    assert "error" in r and r["error"]
    print("OK test_send_invoice_http_error")


def test_send_invoice_sin_token():
    payload = _factura_valida()
    # Simula token no configurado al pegar a la API
    with patch.object(
        server.client,
        "crear_alta",
        side_effect=ValueError("VERIFACTU_API_TOKEN no configurado."),
    ):
        r = _run("send_invoice", payload)
    assert r["ok"] is False
    assert "TOKEN" in r["error"].upper() or "token" in r["error"]
    print("OK test_send_invoice_sin_token")


def test_send_invoice_pasa_receptor():
    payload = _factura_valida()
    payload["nif_receptor"] = "12345678Z"
    payload["nombre_receptor"] = "Cliente SA"
    respuesta_fake = {"data": {"items": [{"id": 99}]}}
    with patch.object(server.client, "crear_alta", return_value=respuesta_fake) as m:
        r = _run("send_invoice", payload)
    assert r["ok"] is True
    p = m.call_args.args[0] if m.call_args.args else m.call_args.kwargs.get("payload")
    assert p["Destinatarios"] == [{"NIF": "12345678Z", "NombreRazon": "Cliente SA"}]
    print("OK test_send_invoice_pasa_receptor")


def test_send_invoice_rechaza_nif_invalido_sin_pegar_api():
    """Validación NIF debe disparar ANTES de llamar al cliente."""
    payload = _factura_valida()
    payload["id_emisor"] = "B12345670"  # control incorrecto
    with patch.object(server.client, "crear_alta") as m:
        r = _run("send_invoice", payload)
    assert r["ok"] is False
    m.assert_not_called()
    print("OK test_send_invoice_rechaza_nif_invalido_sin_pegar_api")


# ---------------------------------------------------------------------------
# cancel_invoice
# ---------------------------------------------------------------------------

def test_cancel_invoice_exito():
    respuesta_fake = {"data": {"items": [{"id": 7}]}}
    payload_in = {
        "id_emisor": "B12345674",
        "num_serie": "2025/001",
        "fecha": "15-05-2025",
        "id_registro_anulado": 42,
    }
    with patch.object(server.client, "crear_anulacion", return_value=respuesta_fake) as m:
        r = _run("cancel_invoice", payload_in)
    assert r["ok"] is True
    assert r["respuesta_api"] == respuesta_fake
    p = m.call_args.args[0] if m.call_args.args else m.call_args.kwargs.get("payload")
    assert p["id_registro_anulado"] == 42
    assert p["IDEmisorFactura"] == "B12345674"
    print("OK test_cancel_invoice_exito")


def test_cancel_invoice_http_error():
    payload_in = {
        "id_emisor": "B12345674",
        "num_serie": "2025/001",
        "fecha": "15-05-2025",
        "id_registro_anulado": 42,
    }
    with patch.object(
        server.client,
        "crear_anulacion",
        side_effect=httpx.HTTPStatusError(
            "500 Server Error",
            request=httpx.Request("POST", "http://fake"),
            response=httpx.Response(500),
        ),
    ):
        r = _run("cancel_invoice", payload_in)
    assert r["ok"] is False
    assert "error" in r
    print("OK test_cancel_invoice_http_error")


# ---------------------------------------------------------------------------
# check_invoice_status
# ---------------------------------------------------------------------------

def test_check_status_exito():
    respuesta_fake = {"id": 7, "estado": "ACEPTADO", "huella": "ABC123"}
    with patch.object(server.client, "consultar_estado", return_value=respuesta_fake) as m:
        r = _run("check_invoice_status", {"registro_id": 7})
    assert r["ok"] is True
    assert r["estado"] == respuesta_fake
    m.assert_called_once_with(7)
    print("OK test_check_status_exito")


def test_check_status_not_found():
    with patch.object(
        server.client,
        "consultar_estado",
        side_effect=httpx.HTTPStatusError(
            "404 Not Found",
            request=httpx.Request("GET", "http://fake/123"),
            response=httpx.Response(404),
        ),
    ):
        r = _run("check_invoice_status", {"registro_id": 123})
    assert r["ok"] is False
    assert "error" in r
    print("OK test_check_status_not_found")


def test_check_status_sin_token():
    with patch.object(
        server.client,
        "consultar_estado",
        side_effect=ValueError("VERIFACTU_API_TOKEN no configurado."),
    ):
        r = _run("check_invoice_status", {"registro_id": 7})
    assert r["ok"] is False
    assert "token" in r["error"].lower()
    print("OK test_check_status_sin_token")
