# verifactu-mcp — Notas para Claude

Servidor MCP en Python que expone herramientas de facturación electrónica
VeriFactu (AEAT) a Claude Code / Cursor. Diseñado para que un usuario pueda
generar, encadenar, enviar y consultar registros de facturación desde lenguaje
natural sin tocar la documentación técnica de la AEAT.

## Decisiones arquitectónicas

- **Endpoint:** REST intermediario `https://app.verifactuapi.es/api` en `VeriFactuClient`. **No** SOAP directo contra AEAT. Esta decisión es explícita; no la propongas revertir salvo que el usuario lo pida.
- **Layout:** src-layout (`src/verifactu_mcp/server.py`). Todo el código vive en `server.py` — un único módulo, sin partir por capas hasta que haga falta.
- **Persistencia de huella:** SQLite en `~/.verifactu-mcp/state.db` (overrideable con `VERIFACTU_STATE_DB`). Encadenamiento automático: si `huella_anterior` no se pasa, se busca la última huella del emisor en DB.
- **`generate_invoice_xml`:** modo preview — lee la huella anterior pero **no persiste**. Solo `send_invoice` persiste, y únicamente tras OK de la API.
- **Tests aislados:** `conftest.py` redirige `VERIFACTU_STATE_DB` a `tmp_path` autouse en cada test. No tocar.

## Herramientas (7)

| Tool | Función |
|---|---|
| `generate_invoice_xml` | XML preview, sin enviar |
| `send_invoice` | POST alta + persiste huella tras éxito |
| `cancel_invoice` | POST anulación |
| `check_invoice_status` | GET registro por id |
| `list_invoices` | GET listado con filtros + paginación |
| `get_last_hash` | Lee última huella persistida (no toca API) |
| `calculate_hash` | Hash manual sin lateral effects |

## Validación

`validar_nif(s)` cubre DNI (8 dígitos + letra), NIE (XYZ + 7 + letra) y CIF (letra + 7 dígitos + control). Se aplica antes de cualquier llamada a la API a través de `_validar_nifs_factura` y, para `list_invoices` / `get_last_hash`, en el handler directamente.

## Cliente HTTP

- `httpx.Client` por llamada (no pool persistente — el server es de baja frecuencia).
- Retry exponencial 3 intentos en 5xx y errores de red (`ConnectError`, `TimeoutException`, `ReadError`). Base 0.5 s, backoff `2^n`.
- 4xx no reintenta — propaga inmediato.
- Categorización del handler: `http_error` / `network` / `config` / `unexpected`.

## Tests

`pytest -q` desde la raíz. 65 tests. `[tool.pytest.ini_options] pythonpath = ["src"]` hace que se resuelva `verifactu_mcp.server` sin necesidad de `pip install -e .`.

Para tests que mockean: usar `patch.object(server.client, "metodo")` o `patch("verifactu_mcp.server.<simbolo>")` con la ruta absoluta del paquete.

## Hallazgos del E2E real (sandbox verifactuapi.es, 2026-05-20)

- **API gestiona encadenamiento server-side**: usa `previous_id` (ID interno del registro previo) automáticamente. El cliente NO necesita pasar `Huella` anterior ni `previous_id` en el payload para que encadene. La persistencia local de huella es **audit log**, no operativa.
- **`calcular_huella` es heurística**: coincide con la huella canónica AEAT en muchos casos pero no en todos. La huella oficial la genera el server (incluye `FechaHoraHusoGenRegistro` server-side). Por eso `send_invoice` captura `Huella` del response API y persiste ESA, no la local. La local se reporta como `huella_local_estimada`.
- **Payload `Desglose` obligatorio**: con campos `Impuesto: "01"`, `ClaveRegimen: "01"`, `CalificacionOperacion: "S1"`, `TipoImpositivo`, `BaseImponibleOImporteNoSujeto` (capital `I` en `Importe`), `CuotaRepercutida`.
- **`Destinatarios` obligatorio para TipoFactura F1**: forma `[{"NIF": ..., "NombreRazon": ...}]`.
- **Paginación API en camelCase**: parámetro `perPage`, no `per_page`. Mi tool acepta `per_page` y lo traduce.
- **Smoke script:** `python smoke_test.py <id_emisor> [nif_receptor]` con `VERIFACTU_API_TOKEN` en env. Pega contra sandbox real, NO contra producción.

## Pendiente real

- **Anulación E2E**: `cancel_invoice` aún no probado contra sandbox real. Probable que necesite ajustes al payload similares a `send_invoice` (TipoFactura, etc).
- **`get_last_hash` con datos asíncronos**: el GET inicial tras POST devuelve `Huella: null` (procesamiento async server-side). El valor en `data.items[0].Huella` del POST response sí es definitivo y es lo que persistimos.

## Qué NO hacer

- No añadir SOAP directo a AEAT. Diferido.
- No mover `server.py` a otro layout. Está estable.
- No introducir ORM ni Alembic — SQLite stdlib con DDL inline es suficiente.
- No "limpiar" los `print("OK ...")` dentro de los tests legacy — son ruido inofensivo y modificarlos es churn sin valor.
- No abrir conexiones HTTP fuera de `_request` — todo pasa por ahí para que el retry/categorización funcione.
