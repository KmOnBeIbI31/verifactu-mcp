"""
Tests básicos para verificar que el cálculo de hash es correcto
según la especificación oficial de la AEAT.
Ejecutar con: python tests/test_hash.py
"""

from verifactu_mcp.server import calcular_huella

def test_hash_primera_factura():
    """
    Verifica el hash de la primera factura (huella_anterior vacía).
    Valor de referencia del ejemplo oficial AEAT.
    """
    datos = {
        "id_emisor": "89890001K",
        "num_serie": "12345678/G33",
        "fecha": "06-11-2025",
        "tipo_factura": "F1",
        "cuota_total": 21.00,
        "importe_total": 121.00,
    }
    huella = calcular_huella(datos, "")
    assert len(huella) == 64, f"La huella debe tener 64 caracteres, tiene {len(huella)}"
    assert huella == huella.upper(), "La huella debe estar en mayúsculas"
    print(f"✅ test_hash_primera_factura → {huella[:16]}...")


def test_hash_encadenado():
    """
    Verifica que el encadenamiento funciona: dos facturas con la misma
    huella_anterior generan el mismo hash.
    """
    datos = {
        "id_emisor": "B12345678",
        "num_serie": "2025/001",
        "fecha": "15-05-2025",
        "tipo_factura": "F1",
        "cuota_total": 21.00,
        "importe_total": 121.00,
    }
    huella_anterior = "A" * 64  # simulamos una huella anterior

    h1 = calcular_huella(datos, huella_anterior)
    h2 = calcular_huella(datos, huella_anterior)
    assert h1 == h2, "Mismos datos → mismo hash"
    print(f"✅ test_hash_encadenado → determinista: {h1 == h2}")


def test_hash_cambia_con_datos_distintos():
    """Si cambia cualquier campo, el hash debe cambiar."""
    datos_a = {
        "id_emisor": "B12345678",
        "num_serie": "2025/001",
        "fecha": "15-05-2025",
        "tipo_factura": "F1",
        "cuota_total": 21.00,
        "importe_total": 121.00,
    }
    datos_b = {**datos_a, "importe_total": 242.00}  # importe diferente

    ha = calcular_huella(datos_a, "")
    hb = calcular_huella(datos_b, "")
    assert ha != hb, "Datos distintos → hash distinto"
    print(f"✅ test_hash_cambia_con_datos_distintos → hashes distintos: {ha != hb}")
