"""
Tests para validacion completa de NIF/NIE/CIF con algoritmo de digito control.
Ejecutar con: python test_nif.py
"""

from verifactu_mcp.server import validar_nif


# ---------------------------------------------------------------------------
# DNI / NIF persona fisica: 8 digitos + letra
# ---------------------------------------------------------------------------

def test_dni_valido():
    assert validar_nif("12345678Z") is True, "DNI con letra correcta debe ser valido"
    print("OK test_dni_valido")


def test_dni_letra_incorrecta():
    assert validar_nif("12345678A") is False, "DNI con letra incorrecta debe fallar"
    print("OK test_dni_letra_incorrecta")


def test_dni_sin_letra():
    assert validar_nif("123456789") is False, "DNI sin letra (todo digitos) debe fallar"
    print("OK test_dni_sin_letra")


# ---------------------------------------------------------------------------
# NIE extranjero: X/Y/Z + 7 digitos + letra
# ---------------------------------------------------------------------------

def test_nie_valido_X():
    assert validar_nif("X1234567L") is True, "NIE X1234567L valido"
    print("OK test_nie_valido_X")


def test_nie_letra_incorrecta():
    assert validar_nif("X1234567A") is False, "NIE con letra control incorrecta debe fallar"
    print("OK test_nie_letra_incorrecta")


# ---------------------------------------------------------------------------
# CIF empresa: letra + 7 digitos + digito o letra control
# ---------------------------------------------------------------------------

def test_cif_valido_digito_control():
    # A58818501: A starts CIF (digito control), validado paso a paso
    assert validar_nif("A58818501") is True, "CIF A58818501 valido"
    print("OK test_cif_valido_digito_control")


def test_cif_valido_B():
    assert validar_nif("B12345674") is True, "CIF B12345674 valido"
    print("OK test_cif_valido_B")


def test_cif_control_incorrecto():
    assert validar_nif("B12345670") is False, "CIF con digito control incorrecto debe fallar"
    print("OK test_cif_control_incorrecto")


def test_cif_letra_inicial_invalida():
    # I, O, T, X, Y, Z no validas como primera letra CIF
    assert validar_nif("I12345674") is False, "CIF con letra inicial invalida debe fallar"
    print("OK test_cif_letra_inicial_invalida")


# ---------------------------------------------------------------------------
# Casos limite
# ---------------------------------------------------------------------------

def test_vacio():
    assert validar_nif("") is False
    print("OK test_vacio")


def test_demasiado_corto():
    assert validar_nif("X") is False
    print("OK test_demasiado_corto")


def test_demasiado_largo():
    assert validar_nif("X12345678Z") is False
    print("OK test_demasiado_largo")


def test_lowercase_se_normaliza():
    assert validar_nif("b12345674") is True, "Lowercase debe normalizar a uppercase"
    print("OK test_lowercase_se_normaliza")


def test_espacios_se_eliminan():
    assert validar_nif("  B12345674  ") is True, "Espacios externos deben ignorarse"
    print("OK test_espacios_se_eliminan")


def test_caracteres_no_alfanumericos():
    assert validar_nif("B-12345674") is False, "Guiones no permitidos"
    print("OK test_caracteres_no_alfanumericos")
