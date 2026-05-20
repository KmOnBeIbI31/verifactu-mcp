"""
Tests para retry con backoff y categorizacion de errores.
- 5xx y errores de red: reintenta hasta MAX_RETRIES
- 4xx: no reintenta, propaga inmediato
- Respuesta handler distingue tipos: http_error / network / config / unexpected
"""

import asyncio
import json
from unittest.mock import patch

import httpx

from verifactu_mcp import server
from verifactu_mcp.server import VeriFactuClient, call_tool


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
# Retry en VeriFactuClient
# ---------------------------------------------------------------------------

def _make_client_with_responses(responses):
    """Crea VeriFactuClient con MockTransport que devuelve responses en orden."""
    state = {"i": 0}

    def handler(request):
        r = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        if isinstance(r, Exception):
            raise r
        return r

    transport = httpx.MockTransport(handler)
    c = VeriFactuClient(token="fake-token", transport=transport)
    return c, state


def test_retry_5xx_luego_exito():
    responses = [
        httpx.Response(503, json={"err": "down"}),
        httpx.Response(503, json={"err": "down"}),
        httpx.Response(200, json={"data": {"items": [{"id": 1}]}}),
    ]
    c, state = _make_client_with_responses(responses)
    with patch("verifactu_mcp.server.time.sleep") as ms:
        resultado = c.crear_alta({"foo": "bar"})
    assert resultado == {"data": {"items": [{"id": 1}]}}
    assert state["i"] == 3, f"Esperaba 3 intentos, hubo {state['i']}"
    assert ms.call_count == 2  # backoff entre intentos
    print("OK test_retry_5xx_luego_exito")


def test_retry_agota_intentos_5xx():
    responses = [httpx.Response(500, text="boom")] * 5
    c, state = _make_client_with_responses(responses)
    with patch("verifactu_mcp.server.time.sleep"):
        try:
            c.crear_alta({"foo": "bar"})
            assert False, "Debe levantar HTTPStatusError tras agotar reintentos"
        except httpx.HTTPStatusError as e:
            assert e.response.status_code == 500
    assert state["i"] == VeriFactuClient.MAX_RETRIES
    print("OK test_retry_agota_intentos_5xx")


def test_no_retry_en_4xx():
    responses = [httpx.Response(401, json={"error": "bad token"})]
    c, state = _make_client_with_responses(responses)
    with patch("verifactu_mcp.server.time.sleep") as ms:
        try:
            c.crear_alta({"foo": "bar"})
            assert False, "Debe levantar HTTPStatusError en 401"
        except httpx.HTTPStatusError as e:
            assert e.response.status_code == 401
    assert state["i"] == 1, f"4xx no debe reintentar, hubo {state['i']} llamadas"
    ms.assert_not_called()
    print("OK test_no_retry_en_4xx")


def test_retry_error_red_luego_exito():
    req = httpx.Request("POST", "http://fake")
    responses = [
        httpx.ConnectError("conn refused", request=req),
        httpx.Response(200, json={"ok": 1}),
    ]
    c, state = _make_client_with_responses(responses)
    with patch("verifactu_mcp.server.time.sleep"):
        resultado = c.crear_alta({"foo": "bar"})
    assert resultado == {"ok": 1}
    assert state["i"] == 2
    print("OK test_retry_error_red_luego_exito")


def test_retry_timeout_agota():
    req = httpx.Request("POST", "http://fake")
    responses = [httpx.TimeoutException("slow", request=req)] * 5
    c, state = _make_client_with_responses(responses)
    with patch("verifactu_mcp.server.time.sleep"):
        try:
            c.crear_alta({"foo": "bar"})
            assert False, "Debe levantar TimeoutException tras agotar"
        except httpx.TimeoutException:
            pass
    assert state["i"] == VeriFactuClient.MAX_RETRIES
    print("OK test_retry_timeout_agota")


def test_sin_token_no_intenta_red():
    c = VeriFactuClient(token="", transport=httpx.MockTransport(lambda r: 1 / 0))
    try:
        c.crear_alta({"foo": "bar"})
        assert False, "Debe levantar ValueError sin token"
    except ValueError as e:
        assert "TOKEN" in str(e).upper()
    print("OK test_sin_token_no_intenta_red")


# ---------------------------------------------------------------------------
# Categorizacion de errores en handlers
# ---------------------------------------------------------------------------

def test_handler_categoriza_http_error():
    response = httpx.Response(400, json={"detalle": "campo X"})
    err = httpx.HTTPStatusError(
        "400 Bad Request",
        request=httpx.Request("POST", "http://fake"),
        response=response,
    )
    with patch.object(server.client, "crear_alta", side_effect=err):
        r = _run("send_invoice", _factura_valida())
    assert r["ok"] is False
    assert r["error_type"] == "http_error"
    assert r["status_code"] == 400
    assert r["body"] == {"detalle": "campo X"}
    print("OK test_handler_categoriza_http_error")


def test_handler_categoriza_network_error():
    err = httpx.ConnectError("conn refused", request=httpx.Request("POST", "http://fake"))
    with patch.object(server.client, "crear_alta", side_effect=err):
        r = _run("send_invoice", _factura_valida())
    assert r["ok"] is False
    assert r["error_type"] == "network"
    print("OK test_handler_categoriza_network_error")


def test_handler_categoriza_config_error():
    err = ValueError("VERIFACTU_API_TOKEN no configurado.")
    with patch.object(server.client, "crear_alta", side_effect=err):
        r = _run("send_invoice", _factura_valida())
    assert r["ok"] is False
    assert r["error_type"] == "config"
    print("OK test_handler_categoriza_config_error")


def test_handler_categoriza_unexpected():
    with patch.object(server.client, "crear_alta", side_effect=RuntimeError("???")):
        r = _run("send_invoice", _factura_valida())
    assert r["ok"] is False
    assert r["error_type"] == "unexpected"
    print("OK test_handler_categoriza_unexpected")
