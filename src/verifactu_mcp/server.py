"""
verifactu-mcp — Servidor MCP para integrar VeriFactu (AEAT) con Claude Code
Permite generar, encadenar y enviar registros de facturación desde lenguaje natural.
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("verifactu-mcp")
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
VERIFACTU_API_BASE = os.getenv("VERIFACTU_API_BASE", "https://app.verifactuapi.es/api")
VERIFACTU_API_TOKEN = os.getenv("VERIFACTU_API_TOKEN", "")

_DEFAULT_STATE_DB = Path.home() / ".verifactu-mcp" / "state.db"


def _fecha_a_iso(fecha: str) -> str:
    """Convierte DD-MM-YYYY (formato cliente) a YYYY-MM-DD (ISO, formato API).

    Si la fecha ya viene en ISO, se devuelve tal cual.
    """
    if not fecha or "-" not in fecha:
        return fecha
    partes = fecha.split("-")
    if len(partes) != 3:
        return fecha
    if len(partes[0]) == 4:
        return fecha
    dd, mm, yyyy = partes
    return f"{yyyy}-{mm}-{dd}"


def _state_db_path() -> Path:
    override = os.getenv("VERIFACTU_STATE_DB")
    return Path(override) if override else _DEFAULT_STATE_DB


def _state_connect() -> sqlite3.Connection:
    path = _state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_emisor TEXT NOT NULL,
            num_serie TEXT NOT NULL,
            fecha TEXT NOT NULL,
            huella TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_invoices_emisor_created "
        "ON invoices(id_emisor, created_at DESC)"
    )
    return conn


def get_last_huella(id_emisor: str) -> str:
    """Devuelve la última huella registrada para un emisor, o cadena vacía."""
    with _state_connect() as conn:
        row = conn.execute(
            "SELECT huella FROM invoices WHERE id_emisor = ? "
            "ORDER BY id DESC LIMIT 1",
            (id_emisor,),
        ).fetchone()
    return row[0] if row else ""


def save_huella(id_emisor: str, num_serie: str, fecha: str, huella: str) -> None:
    """Persiste la huella de una factura ya enviada (commit a la AEAT)."""
    with _state_connect() as conn:
        conn.execute(
            "INSERT INTO invoices(id_emisor, num_serie, fecha, huella) "
            "VALUES(?, ?, ?, ?)",
            (id_emisor, num_serie, fecha, huella),
        )
        conn.commit()

# ---------------------------------------------------------------------------
# Utilidades de hash (implementación directa sin depender de la API externa)
# ---------------------------------------------------------------------------

def calcular_huella(datos: dict, huella_anterior: str = "") -> str:
    """
    Calcula el hash SHA-256 de un registro según la especificación oficial AEAT.
    Formato: campo1=valor1&campo2=valor2&...&Huella=<huella_anterior>
    Salida:  64 caracteres HEX en mayúsculas.
    """
    cadena = (
        f"IDEmisorFactura={datos['id_emisor']}&"
        f"NumSerieFactura={datos['num_serie']}&"
        f"FechaExpedicionFactura={datos['fecha']}&"
        f"TipoFactura={datos.get('tipo_factura', 'F1')}&"
        f"CuotaTotal={datos['cuota_total']}&"
        f"ImporteTotal={datos['importe_total']}&"
        f"Huella={huella_anterior}"
    )
    return hashlib.sha256(cadena.encode("utf-8")).hexdigest().upper()


_DNI_LETRAS = "TRWAGMYFPDXBNJZSQVHLCKE"
_CIF_LETRAS_CONTROL = "JABCDEFGHI"
_CIF_INICIAL_LETRA_CONTROL = set("PQSNWR")
_CIF_INICIAL_DIGITO_CONTROL = set("ABEH")
_CIF_INICIAL_VALIDA = set("ABCDEFGHJNPQRSUVW")


def validar_nif(nif: str) -> bool:
    """
    Valida NIF (DNI persona física), NIE o CIF español comprobando el dígito
    de control según el algoritmo oficial. Acepta espacios externos y
    minúsculas (se normalizan).
    """
    if not nif:
        return False
    nif = nif.strip().upper()
    if len(nif) != 9:
        return False
    if not nif.isalnum():
        return False

    # DNI: 8 dígitos + letra
    if re.fullmatch(r"[0-9]{8}[A-Z]", nif):
        numero = int(nif[:8])
        return nif[8] == _DNI_LETRAS[numero % 23]

    # NIE: X|Y|Z + 7 dígitos + letra
    if re.fullmatch(r"[XYZ][0-9]{7}[A-Z]", nif):
        prefijo = {"X": "0", "Y": "1", "Z": "2"}[nif[0]]
        numero = int(prefijo + nif[1:8])
        return nif[8] == _DNI_LETRAS[numero % 23]

    # CIF: letra inicial + 7 dígitos + dígito o letra control
    if re.fullmatch(r"[A-Z][0-9]{7}[0-9A-J]", nif):
        inicial = nif[0]
        if inicial not in _CIF_INICIAL_VALIDA:
            return False
        digitos = nif[1:8]
        control = nif[8]
        suma_pares = sum(int(d) for d in digitos[1::2])
        suma_impares = 0
        for d in digitos[0::2]:
            doble = int(d) * 2
            suma_impares += doble if doble < 10 else doble - 9
        digito_control = (10 - (suma_pares + suma_impares) % 10) % 10
        if inicial in _CIF_INICIAL_LETRA_CONTROL:
            return control == _CIF_LETRAS_CONTROL[digito_control]
        if inicial in _CIF_INICIAL_DIGITO_CONTROL:
            return control.isdigit() and int(control) == digito_control
        if control.isdigit():
            return int(control) == digito_control
        return control == _CIF_LETRAS_CONTROL[digito_control]

    return False


# ---------------------------------------------------------------------------
# Cliente API VeriFactu
# ---------------------------------------------------------------------------

class VeriFactuClient:
    """Wrapper sobre la API REST de VeriFactu con retry exponencial."""

    MAX_RETRIES = 3
    BACKOFF_BASE = 0.5  # segundos
    TIMEOUT = 30

    def __init__(
        self,
        base: str | None = None,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base = base if base is not None else VERIFACTU_API_BASE
        self.token = token if token is not None else VERIFACTU_API_TOKEN
        self.transport = transport
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _check_token(self):
        if not self.token:
            raise ValueError(
                "VERIFACTU_API_TOKEN no configurado. "
                "Añádelo como variable de entorno."
            )

    def _request(self, method: str, path: str, **kwargs) -> dict:
        self._check_token()
        url = f"{self.base}{path}"
        last_exc: Exception | None = None
        for intento in range(self.MAX_RETRIES):
            try:
                with httpx.Client(timeout=self.TIMEOUT, transport=self.transport) as cli:
                    r = cli.request(method, url, headers=self.headers, **kwargs)
                if 500 <= r.status_code < 600:
                    last_exc = httpx.HTTPStatusError(
                        f"{r.status_code} server error",
                        request=r.request,
                        response=r,
                    )
                    log.warning("AEAT %s %s -> %s (intento %d/%d)", method, path, r.status_code, intento + 1, self.MAX_RETRIES)
                    if intento < self.MAX_RETRIES - 1:
                        time.sleep(self.BACKOFF_BASE * (2 ** intento))
                        continue
                    raise last_exc
                r.raise_for_status()
                return r.json()
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as e:
                last_exc = e
                log.warning("AEAT %s %s -> %s (intento %d/%d)", method, path, type(e).__name__, intento + 1, self.MAX_RETRIES)
                if intento < self.MAX_RETRIES - 1:
                    time.sleep(self.BACKOFF_BASE * (2 ** intento))
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    def crear_alta(self, payload: dict) -> dict:
        return self._request("POST", "/alta-registro-facturacion", json=payload)

    def crear_anulacion(self, payload: dict) -> dict:
        return self._request("POST", "/anulacion-registro-facturacion", json=payload)

    def consultar_estado(self, registro_id: int) -> dict:
        return self._request("GET", f"/alta-registro-facturacion/{registro_id}")

    def listar_altas(self, params: dict | None = None) -> dict:
        return self._request(
            "GET",
            "/alta-registro-facturacion",
            params=params or {},
        )


client = VeriFactuClient()

# ---------------------------------------------------------------------------
# Servidor MCP
# ---------------------------------------------------------------------------

app = Server("verifactu-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="generate_invoice_xml",
            description=(
                "Genera el XML de un registro de alta de factura según el esquema "
                "oficial de la AEAT (VeriFactu). Calcula automáticamente el hash "
                "SHA-256 encadenado. No envía a la AEAT, solo genera el XML."
            ),
            inputSchema={
                "type": "object",
                "required": [
                    "id_emisor", "num_serie", "fecha",
                    "descripcion", "base_imponible", "tipo_iva",
                ],
                "properties": {
                    "id_emisor": {
                        "type": "string",
                        "description": "NIF/CIF del emisor de la factura (ej: B12345678)",
                    },
                    "num_serie": {
                        "type": "string",
                        "description": "Número de serie de la factura (ej: 2025/001)",
                    },
                    "fecha": {
                        "type": "string",
                        "description": "Fecha de expedición en formato DD-MM-YYYY",
                    },
                    "descripcion": {
                        "type": "string",
                        "description": "Descripción de la operación",
                    },
                    "base_imponible": {
                        "type": "number",
                        "description": "Base imponible en euros (sin IVA)",
                    },
                    "tipo_iva": {
                        "type": "number",
                        "description": "Porcentaje de IVA (ej: 21, 10, 4)",
                    },
                    "nif_receptor": {
                        "type": "string",
                        "description": "NIF/CIF del receptor (opcional)",
                    },
                    "nombre_receptor": {
                        "type": "string",
                        "description": "Nombre o razón social del receptor (opcional)",
                    },
                    "huella_anterior": {
                        "type": "string",
                        "description": "Hash SHA-256 de la factura anterior (vacío si es la primera)",
                        "default": "",
                    },
                },
            },
        ),
        Tool(
            name="send_invoice",
            description=(
                "Envía un registro de alta de factura a la AEAT a través de la API "
                "VeriFactu. Requiere VERIFACTU_API_TOKEN configurado."
            ),
            inputSchema={
                "type": "object",
                "required": [
                    "id_emisor", "num_serie", "fecha",
                    "descripcion", "base_imponible", "tipo_iva",
                ],
                "properties": {
                    "id_emisor": {"type": "string"},
                    "num_serie": {"type": "string"},
                    "fecha": {"type": "string"},
                    "descripcion": {"type": "string"},
                    "base_imponible": {"type": "number"},
                    "tipo_iva": {"type": "number"},
                    "nif_receptor": {"type": "string"},
                    "nombre_receptor": {"type": "string"},
                    "huella_anterior": {"type": "string", "default": ""},
                },
            },
        ),
        Tool(
            name="cancel_invoice",
            description="Envía un registro de anulación de factura a la AEAT.",
            inputSchema={
                "type": "object",
                "required": ["id_emisor", "num_serie", "fecha", "id_registro_anulado"],
                "properties": {
                    "id_emisor": {"type": "string"},
                    "num_serie": {"type": "string"},
                    "fecha": {"type": "string"},
                    "id_registro_anulado": {
                        "type": "integer",
                        "description": "ID del registro de alta que se quiere anular",
                    },
                },
            },
        ),
        Tool(
            name="check_invoice_status",
            description="Consulta el estado de un registro de facturación en la AEAT.",
            inputSchema={
                "type": "object",
                "required": ["registro_id"],
                "properties": {
                    "registro_id": {
                        "type": "integer",
                        "description": "ID del registro devuelto al crear el alta",
                    }
                },
            },
        ),
        Tool(
            name="list_invoices",
            description=(
                "Lista los registros de alta enviados a la AEAT a través de la API "
                "VeriFactu. Soporta filtros opcionales por emisor y paginación. "
                "Requiere VERIFACTU_API_TOKEN."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id_emisor": {
                        "type": "string",
                        "description": "Filtrar por NIF/CIF del emisor (opcional)",
                    },
                    "page": {
                        "type": "integer",
                        "description": "Página a recuperar (1-indexed). Opcional.",
                        "minimum": 1,
                    },
                    "per_page": {
                        "type": "integer",
                        "description": "Resultados por página. Opcional.",
                        "minimum": 1,
                        "maximum": 200,
                    },
                    "extra_params": {
                        "type": "object",
                        "description": (
                            "Parámetros adicionales a pasar tal cual a la API "
                            "(escape hatch para filtros no expuestos)."
                        ),
                        "additionalProperties": True,
                    },
                },
            },
        ),
        Tool(
            name="get_last_hash",
            description=(
                "Devuelve la última huella registrada localmente para un emisor. "
                "Útil para inspeccionar el estado del encadenamiento sin enviar "
                "ninguna factura."
            ),
            inputSchema={
                "type": "object",
                "required": ["id_emisor"],
                "properties": {
                    "id_emisor": {
                        "type": "string",
                        "description": "NIF/CIF del emisor",
                    },
                },
            },
        ),
        Tool(
            name="calculate_hash",
            description=(
                "Calcula el hash SHA-256 de una factura según la especificación AEAT. "
                "Útil para verificar manualmente el encadenamiento."
            ),
            inputSchema={
                "type": "object",
                "required": [
                    "id_emisor", "num_serie", "fecha",
                    "tipo_factura", "cuota_total", "importe_total",
                ],
                "properties": {
                    "id_emisor": {"type": "string"},
                    "num_serie": {"type": "string"},
                    "fecha": {"type": "string"},
                    "tipo_factura": {"type": "string", "default": "F1"},
                    "cuota_total": {"type": "number"},
                    "importe_total": {"type": "number"},
                    "huella_anterior": {"type": "string", "default": ""},
                },
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Handlers de cada herramienta
# ---------------------------------------------------------------------------

def _error_response(e: Exception) -> dict:
    """Categoriza una excepción del cliente AEAT en una respuesta estructurada."""
    if isinstance(e, httpx.HTTPStatusError):
        body: Any
        try:
            body = e.response.json()
        except Exception:
            body = e.response.text[:500] if e.response.text else None
        return {
            "ok": False,
            "error_type": "http_error",
            "status_code": e.response.status_code,
            "error": str(e),
            "body": body,
        }
    if isinstance(e, httpx.RequestError):
        return {
            "ok": False,
            "error_type": "network",
            "error": f"{type(e).__name__}: {e}",
        }
    if isinstance(e, ValueError):
        return {
            "ok": False,
            "error_type": "config",
            "error": str(e),
        }
    return {
        "ok": False,
        "error_type": "unexpected",
        "error": f"{type(e).__name__}: {e}",
    }


def _validar_nifs_factura(arguments: dict) -> str | None:
    """Devuelve mensaje de error si algún NIF es inválido, None si todo OK."""
    emisor = arguments.get("id_emisor")
    if emisor is not None and not validar_nif(emisor):
        return f"id_emisor inválido: '{emisor}' no supera la validación de NIF/CIF/NIE."
    receptor = arguments.get("nif_receptor")
    if receptor and not validar_nif(receptor):
        return f"nif_receptor inválido: '{receptor}' no supera la validación de NIF/CIF/NIE."
    return None


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:

    if name in ("generate_invoice_xml", "send_invoice", "cancel_invoice"):
        err = _validar_nifs_factura(arguments)
        if err:
            return [TextContent(type="text", text=json.dumps({"ok": False, "error": err}, ensure_ascii=False))]

    # ── generate_invoice_xml ────────────────────────────────────────────────
    if name == "generate_invoice_xml":
        base = arguments["base_imponible"]
        tipo = arguments["tipo_iva"]
        cuota = round(base * tipo / 100, 2)
        total = round(base + cuota, 2)

        datos_hash = {
            "id_emisor": arguments["id_emisor"],
            "num_serie": arguments["num_serie"],
            "fecha": arguments["fecha"],
            "tipo_factura": "F1",
            "cuota_total": cuota,
            "importe_total": total,
        }
        huella_anterior = arguments.get("huella_anterior") or get_last_huella(arguments["id_emisor"])
        huella = calcular_huella(datos_hash, huella_anterior)

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<sfe:SuministroLRFacturasEmitidas
  xmlns:sfe="https://www2.agenciatributaria.gob.es/static_files/common/internet/dep/aplicaciones/es/aeat/tikeV1.0/cont/ws/SuministroLR.xsd">
  <Cabecera>
    <ObligadoTributario>
      <NIF>{arguments['id_emisor']}</NIF>
    </ObligadoTributario>
    <TipoComunicacion>A0</TipoComunicacion>
  </Cabecera>
  <RegistroLRFacturasEmitidas>
    <PeriodoLiquidacion>
      <Ejercicio>{arguments['fecha'].split('-')[2]}</Ejercicio>
      <Periodo>{arguments['fecha'].split('-')[1]}</Periodo>
    </PeriodoLiquidacion>
    <IDFactura>
      <IDEmisorFactura>{arguments['id_emisor']}</IDEmisorFactura>
      <NumSerieFactura>{arguments['num_serie']}</NumSerieFactura>
      <FechaExpedicionFactura>{arguments['fecha']}</FechaExpedicionFactura>
    </IDFactura>
    <DatosFactura>
      <DescripcionOperacion>{arguments['descripcion']}</DescripcionOperacion>
      <TipoFactura>F1</TipoFactura>
      <TipoRectificativa/>
      <ImporteTotal>{total}</ImporteTotal>
    </DatosFactura>
    <TipoDesglose>
      <DesgloseFactura>
        <Sujeta>
          <NoExenta>
            <DetalleNoExenta>
              <TipoImpositivo>{tipo}</TipoImpositivo>
              <BaseImponible>{base}</BaseImponible>
              <CuotaRepercutida>{cuota}</CuotaRepercutida>
            </DetalleNoExenta>
          </NoExenta>
        </Sujeta>
      </DesgloseFactura>
    </TipoDesglose>
    {"<NombreRazonDestinatario>" + arguments['nombre_receptor'] + "</NombreRazonDestinatario>" if arguments.get('nombre_receptor') else ""}
    {"<NIFDestinatario>" + arguments['nif_receptor'] + "</NIFDestinatario>" if arguments.get('nif_receptor') else ""}
    <Huella>{huella}</Huella>
  </RegistroLRFacturasEmitidas>
</sfe:SuministroLRFacturasEmitidas>"""

        result = {
            "xml": xml,
            "huella": huella,
            "huella_anterior": huella_anterior,
            "resumen": {
                "emisor": arguments["id_emisor"],
                "serie": arguments["num_serie"],
                "fecha": arguments["fecha"],
                "base_imponible": base,
                "cuota_iva": cuota,
                "total": total,
            },
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    # ── send_invoice ────────────────────────────────────────────────────────
    elif name == "send_invoice":
        base = arguments["base_imponible"]
        tipo = arguments["tipo_iva"]
        cuota = round(base * tipo / 100, 2)
        total = round(base + cuota, 2)

        datos_hash = {
            "id_emisor": arguments["id_emisor"],
            "num_serie": arguments["num_serie"],
            "fecha": arguments["fecha"],
            "tipo_factura": "F1",
            "cuota_total": cuota,
            "importe_total": total,
        }
        huella_anterior = arguments.get("huella_anterior") or get_last_huella(arguments["id_emisor"])
        huella = calcular_huella(datos_hash, huella_anterior)

        payload = {
            "IDEmisorFactura": arguments["id_emisor"],
            "NumSerieFactura": arguments["num_serie"],
            "FechaExpedicionFactura": arguments["fecha"],
            "DescripcionOperacion": arguments["descripcion"],
            "TipoFactura": "F1",
            "CuotaTotal": cuota,
            "ImporteTotal": total,
            "Desglose": [
                {
                    "Impuesto": "01",
                    "ClaveRegimen": "01",
                    "CalificacionOperacion": "S1",
                    "TipoImpositivo": tipo,
                    "BaseImponibleOImporteNoSujeto": base,
                    "CuotaRepercutida": cuota,
                }
            ],
            "verifactu": True,
            "Huella": huella,
        }
        if arguments.get("nif_receptor") or arguments.get("nombre_receptor"):
            destinatario: dict = {}
            if arguments.get("nif_receptor"):
                destinatario["NIF"] = arguments["nif_receptor"]
            if arguments.get("nombre_receptor"):
                destinatario["NombreRazon"] = arguments["nombre_receptor"]
            payload["Destinatarios"] = [destinatario]

        try:
            resultado = client.crear_alta(payload)
            item = (resultado.get("data") or {}).get("items", [{}])[0]
            huella_real = item.get("Huella") or huella
            save_huella(
                arguments["id_emisor"],
                arguments["num_serie"],
                arguments["fecha"],
                huella_real,
            )
            return [TextContent(
                type="text",
                text=json.dumps({
                    "ok": True,
                    "registro_id": item.get("id"),
                    "huella": huella_real,
                    "huella_local_estimada": huella,
                    "huella_anterior": huella_anterior,
                    "respuesta_api": resultado,
                }, indent=2, ensure_ascii=False)
            )]
        except Exception as e:
            return [TextContent(type="text", text=json.dumps(_error_response(e), indent=2, ensure_ascii=False))]

    # ── cancel_invoice ──────────────────────────────────────────────────────
    elif name == "cancel_invoice":
        # API de anulación exige fecha en formato ISO YYYY-MM-DD
        payload = {
            "IDEmisorFactura": arguments["id_emisor"],
            "NumSerieFactura": arguments["num_serie"],
            "FechaExpedicionFactura": _fecha_a_iso(arguments["fecha"]),
            "id_registro_anulado": arguments["id_registro_anulado"],
            "verifactu": True,
        }
        try:
            resultado = client.crear_anulacion(payload)
            return [TextContent(
                type="text",
                text=json.dumps({"ok": True, "respuesta_api": resultado}, indent=2, ensure_ascii=False)
            )]
        except Exception as e:
            return [TextContent(type="text", text=json.dumps(_error_response(e), indent=2, ensure_ascii=False))]

    # ── check_invoice_status ────────────────────────────────────────────────
    elif name == "check_invoice_status":
        try:
            resultado = client.consultar_estado(arguments["registro_id"])
            return [TextContent(
                type="text",
                text=json.dumps({"ok": True, "estado": resultado}, indent=2, ensure_ascii=False)
            )]
        except Exception as e:
            return [TextContent(type="text", text=json.dumps(_error_response(e), indent=2, ensure_ascii=False))]

    # ── list_invoices ───────────────────────────────────────────────────────
    elif name == "list_invoices":
        emisor = arguments.get("id_emisor")
        if emisor is not None and not validar_nif(emisor):
            return [TextContent(type="text", text=json.dumps(
                {"ok": False, "error": f"id_emisor inválido: '{emisor}'"},
                ensure_ascii=False,
            ))]

        params: dict = {}
        if emisor:
            params["id_emisor"] = emisor
        if "page" in arguments:
            params["page"] = arguments["page"]
        if "per_page" in arguments:
            # La API espera el parámetro en camelCase
            params["perPage"] = arguments["per_page"]
        extra = arguments.get("extra_params") or {}
        if not isinstance(extra, dict):
            return [TextContent(type="text", text=json.dumps(
                {"ok": False, "error": "extra_params debe ser un objeto."},
                ensure_ascii=False,
            ))]
        params.update(extra)

        try:
            resultado = client.listar_altas(params)
            items = (resultado.get("data") or {}).get("items", []) if isinstance(resultado, dict) else []
            return [TextContent(type="text", text=json.dumps({
                "ok": True,
                "total": len(items),
                "items": items,
                "respuesta_api": resultado,
                "params": params,
            }, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [TextContent(type="text", text=json.dumps(_error_response(e), indent=2, ensure_ascii=False))]

    # ── get_last_hash ───────────────────────────────────────────────────────
    elif name == "get_last_hash":
        emisor = arguments["id_emisor"]
        if not validar_nif(emisor):
            return [TextContent(type="text", text=json.dumps(
                {"ok": False, "error": f"id_emisor inválido: '{emisor}'"},
                ensure_ascii=False,
            ))]
        h = get_last_huella(emisor)
        return [TextContent(type="text", text=json.dumps({
            "ok": True,
            "id_emisor": emisor,
            "huella": h,
            "tiene_registros": bool(h),
        }, indent=2, ensure_ascii=False))]

    # ── calculate_hash ──────────────────────────────────────────────────────
    elif name == "calculate_hash":
        huella = calcular_huella(arguments, arguments.get("huella_anterior", ""))
        cadena = (
            f"IDEmisorFactura={arguments['id_emisor']}&"
            f"NumSerieFactura={arguments['num_serie']}&"
            f"FechaExpedicionFactura={arguments['fecha']}&"
            f"TipoFactura={arguments.get('tipo_factura', 'F1')}&"
            f"CuotaTotal={arguments['cuota_total']}&"
            f"ImporteTotal={arguments['importe_total']}&"
            f"Huella={arguments.get('huella_anterior', '')}"
        )
        return [TextContent(
            type="text",
            text=json.dumps({
                "huella": huella,
                "cadena_concatenada": cadena,
                "longitud_hex": len(huella),
            }, indent=2)
        )]

    return [TextContent(type="text", text=json.dumps({"error": f"Herramienta desconocida: {name}"}))]


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

async def async_main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    """Entry point sync para el script `verifactu-mcp` instalado vía pip."""
    import asyncio
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
