"""
Tests del tool list_invoices con cliente AEAT mockeado.
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


def test_list_invoices_sin_filtros():
    respuesta = {"data": {"items": [{"id": 1}, {"id": 2}, {"id": 3}]}}
    with patch.object(server.client, "listar_altas", return_value=respuesta) as m:
        r = _run("list_invoices", {})
    assert r["ok"] is True
    assert r["total"] == 3
    assert r["items"] == respuesta["data"]["items"]
    m.assert_called_once_with({})


def test_list_invoices_filtra_por_emisor():
    respuesta = {"data": {"items": [{"id": 1}]}}
    with patch.object(server.client, "listar_altas", return_value=respuesta) as m:
        r = _run("list_invoices", {"id_emisor": "B12345674"})
    assert r["ok"] is True
    params = m.call_args.args[0]
    assert params == {"id_emisor": "B12345674"}


def test_list_invoices_paginacion():
    """per_page se traduce a perPage (camelCase) según contrato API real."""
    respuesta = {"data": {"items": []}}
    with patch.object(server.client, "listar_altas", return_value=respuesta) as m:
        r = _run("list_invoices", {"page": 2, "per_page": 50})
    assert r["ok"] is True
    params = m.call_args.args[0]
    assert params["page"] == 2
    assert params["perPage"] == 50
    assert "per_page" not in params


def test_list_invoices_extra_params_pasa_tal_cual():
    respuesta = {"data": {"items": []}}
    with patch.object(server.client, "listar_altas", return_value=respuesta) as m:
        r = _run("list_invoices", {
            "id_emisor": "B12345674",
            "extra_params": {"fecha_desde": "01-01-2025", "estado": "ACEPTADO"},
        })
    assert r["ok"] is True
    params = m.call_args.args[0]
    assert params["fecha_desde"] == "01-01-2025"
    assert params["estado"] == "ACEPTADO"
    assert params["id_emisor"] == "B12345674"


def test_list_invoices_rechaza_emisor_invalido_sin_pegar_api():
    with patch.object(server.client, "listar_altas") as m:
        r = _run("list_invoices", {"id_emisor": "B12345670"})
    assert r["ok"] is False
    assert "inválido" in r["error"] or "invalido" in r["error"].lower()
    m.assert_not_called()


def test_list_invoices_rechaza_extra_params_no_dict():
    with patch.object(server.client, "listar_altas") as m:
        r = _run("list_invoices", {"extra_params": "no-soy-dict"})
    assert r["ok"] is False
    assert "extra_params" in r["error"]
    m.assert_not_called()


def test_list_invoices_propaga_error_http():
    with patch.object(
        server.client,
        "listar_altas",
        side_effect=httpx.HTTPStatusError(
            "401 Unauthorized",
            request=httpx.Request("GET", "http://fake"),
            response=httpx.Response(401, json={"error": "token invalido"}),
        ),
    ):
        r = _run("list_invoices", {})
    assert r["ok"] is False
    assert r["error_type"] == "http_error"
    assert r["status_code"] == 401


def test_list_invoices_respuesta_sin_items():
    """API puede devolver formato distinto. No debe explotar."""
    with patch.object(server.client, "listar_altas", return_value={"otra_cosa": True}):
        r = _run("list_invoices", {})
    assert r["ok"] is True
    assert r["total"] == 0
    assert r["items"] == []


def test_list_invoices_sin_token():
    with patch.object(
        server.client,
        "listar_altas",
        side_effect=ValueError("VERIFACTU_API_TOKEN no configurado."),
    ):
        r = _run("list_invoices", {})
    assert r["ok"] is False
    assert r["error_type"] == "config"
    assert "token" in r["error"].lower()
