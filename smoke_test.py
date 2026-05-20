"""
Smoke test E2E contra verifactuapi.es (sandbox).

USO:
    $env:VERIFACTU_API_TOKEN = "<tu_token>"
    python smoke_test.py <id_emisor> [nif_receptor]

Requiere:
- Token válido en VERIFACTU_API_TOKEN
- Emisor dado de alta en el panel verifactuapi.es (modo TEST)

Operaciones que realiza:
- list_invoices (read-only)
- send_invoice — emite registro real en sandbox
- check_invoice_status sobre el id devuelto
- get_last_hash para auditar persistencia local

NO ejecutar contra producción real sin revisar el código y aceptar las
implicaciones legales: cada send_invoice emite un registro fiscal ante AEAT.
"""
import asyncio
import json
import os
import sys

from verifactu_mcp.server import call_tool


async def run_tool(name: str, args: dict) -> dict:
    result = await call_tool(name, args)
    return json.loads(result[0].text)


def section(title: str) -> None:
    print(f"\n{'='*60}\n{title}\n{'='*60}")


async def main(id_emisor: str, nif_receptor: str | None) -> int:
    if not os.getenv("VERIFACTU_API_TOKEN"):
        print("ERROR: VERIFACTU_API_TOKEN no está configurado.", file=sys.stderr)
        return 1

    section("1. list_invoices (read-only, valida auth)")
    r = await run_tool("list_invoices", {"per_page": 5})
    print(json.dumps(r, indent=2, ensure_ascii=False)[:800])
    if not r.get("ok"):
        print("FALLO en list_invoices, abortando.", file=sys.stderr)
        return 2

    section("2. send_invoice (emite registro real en sandbox)")
    factura = {
        "id_emisor": id_emisor,
        "num_serie": "SMOKE/E2E",
        "fecha": "20-05-2026",
        "descripcion": "Smoke test verifactu-mcp E2E",
        "base_imponible": 100.0,
        "tipo_iva": 21.0,
    }
    if nif_receptor:
        factura["nif_receptor"] = nif_receptor
        factura["nombre_receptor"] = "Cliente Smoke Test SL"
    r = await run_tool("send_invoice", factura)
    print("ok:", r.get("ok"))
    print("registro_id:", r.get("registro_id"))
    print("huella canónica (AEAT):", r.get("huella"))
    print("huella local (estimada):", r.get("huella_local_estimada"))
    if not r.get("ok"):
        print("body:", r.get("body"))
        return 3

    registro_id = r["registro_id"]

    section("3. check_invoice_status")
    r = await run_tool("check_invoice_status", {"registro_id": registro_id})
    print(json.dumps(r, indent=2, ensure_ascii=False)[:600])

    section("4. get_last_hash (persistencia local)")
    r = await run_tool("get_last_hash", {"id_emisor": id_emisor})
    print(json.dumps(r, indent=2, ensure_ascii=False))

    print("\nSmoke OK.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python smoke_test.py <id_emisor> [nif_receptor]", file=sys.stderr)
        sys.exit(1)
    emisor = sys.argv[1]
    receptor = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(asyncio.run(main(emisor, receptor)))
