# verifactu-mcp

<!-- mcp-name: io.github.KmOnBeIbI31/verifactu-mcp -->

Servidor MCP para integrar **VeriFactu (AEAT)** con Claude Code y Cursor.

Permite generar, encadenar y enviar registros de facturación electrónica desde
lenguaje natural, sin necesidad de leer la documentación técnica de la AEAT.

---

## Herramientas disponibles

| Herramienta | Descripción |
|---|---|
| `generate_invoice_xml` | Genera XML válido con hash SHA-256 encadenado (sin enviar) |
| `send_invoice` | Envía registro de alta a la AEAT via API y persiste la huella |
| `cancel_invoice` | Envía registro de anulación |
| `check_invoice_status` | Consulta el estado de un registro |
| `list_invoices` | Lista los registros enviados (filtros opcionales por emisor y paginación) |
| `get_last_hash` | Devuelve la última huella registrada localmente para un emisor |
| `calculate_hash` | Calcula el hash SHA-256 de cualquier registro |

El encadenamiento real lo gestiona la API verifactuapi.es internamente (vía
`previous_id`). El servidor MCP **persiste la huella canónica que devuelve la
API** en SQLite local (`~/.verifactu-mcp/state.db`) como **audit log**, no como
mecanismo de encadenamiento operativo. La herramienta `get_last_hash` consulta
ese log.

`calculate_hash` y la huella `huella_local_estimada` de `send_invoice` son
**estimaciones heurísticas** para inspección — la huella canónica AEAT incluye
el timestamp server-side y solo el servidor puede generarla.

### Smoke test contra sandbox

```bash
$env:VERIFACTU_API_TOKEN = "<tu_token_sandbox>"
python smoke_test.py <id_emisor> [nif_receptor]
```

Ejecuta `list_invoices` + `send_invoice` (¡emite registro real en sandbox!) +
`check_invoice_status` + `get_last_hash`. NO ejecutar contra producción.

---

## Instalación

```bash
# 1. Clona el repositorio
git clone https://github.com/tuusuario/verifactu-mcp
cd verifactu-mcp

# 2. Instala las dependencias
pip install -e .
```

---

## Configuración en Claude Code

Tras `pip install -e .`, el script `verifactu-mcp` queda disponible globalmente.
Añade esto a tu `~/.claude.json` (o `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "verifactu": {
      "command": "verifactu-mcp",
      "env": {
        "VERIFACTU_API_TOKEN": "tu_token_aqui"
      }
    }
  }
}
```

Alternativa sin script instalado (vía `python -m`):

```json
{
  "mcpServers": {
    "verifactu": {
      "command": "python",
      "args": ["-m", "verifactu_mcp.server"],
      "cwd": "/ruta/a/verifactu-mcp",
      "env": {
        "VERIFACTU_API_TOKEN": "tu_token_aqui"
      }
    }
  }
}
```

---

## Uso desde Claude Code

```
# Generar un XML sin enviar (ideal para pruebas)
"Genera un registro VeriFactu para una factura de 100€ + 21% IVA,
 emisor B12345678, serie 2025/001, fecha 15-05-2025,
 descripción: Servicios de desarrollo web"

# Enviar a la AEAT — la huella anterior se resuelve automáticamente
"Envía a la AEAT la factura 2025/001 del emisor B12345678,
 base 100€, IVA 21%, fecha hoy"

# Consultar estado
"¿Cuál es el estado del registro con ID 42?"

# Anular una factura
"Anula la factura 2025/001 del emisor B12345678"

# Listar las facturas enviadas
"Lista las últimas 50 facturas enviadas para el emisor B12345678"

# Inspeccionar el último hash del emisor
"¿Cuál es la última huella encadenada para B12345678?"
```

---

## Variables de entorno

| Variable | Descripción | Requerida |
|---|---|---|
| `VERIFACTU_API_TOKEN` | Token de acceso a verifactuapi.es | Sí (para enviar) |
| `VERIFACTU_API_BASE` | URL base de la API (por defecto producción) | No |
| `VERIFACTU_STATE_DB` | Ruta a la DB SQLite de estado (por defecto `~/.verifactu-mcp/state.db`) | No |

---

## Entornos AEAT

| Entorno | URL |
|---|---|
| Sandbox | `https://prewww1.aeat.es/wlpl/TIKE-CONT/ws/SistemaFacturacion/VerifactuSOAP` |
| Producción | `https://www1.aeat.es/wlpl/TIKE-CONT/ws/SistemaFacturacion/VerifactuSOAP` |

Para certificados de prueba (sandbox): `catentidades@correo.aeat.es`

<!-- mcp-name: io.github.KmOnBeIbI31/verifactu-mcp -->

---

## Licencia

MIT
