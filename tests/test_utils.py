"""Pruebas unitarias de los validadores genéricos."""
import re

import pytest

from app.utils import checks, csv_reader, dates, numbers, text
from app.utils.errors import CellError, FileError


def code_of(fn, *args, **kwargs) -> str:
    with pytest.raises(CellError) as exc:
        fn(*args, **kwargs)
    return exc.value.code


@pytest.mark.parametrize("value,expected", [("-90", -90.0), ("90", 90.0), ("-0", 0.0), ("+5", 5.0), ("0.123456", 0.123456)])
def test_decimal_validos(value, expected):
    assert numbers.validate_decimal(value, -90, 90) == expected


@pytest.mark.parametrize("value,code", [
    ("NaN", "E_TIPO"), ("inf", "E_TIPO"), ("1e3", "E_TIPO"), ("N/A", "E_TIPO"), ("-", "E_TIPO"), (".5", "E_TIPO"),
    ("90.000001", "E_RANGO"), ("-90.1", "E_RANGO"), ("0.1234567", "E_DECIMALES"),
    ("٣", "E_TIPO"), ("1.5\n", "E_TIPO"), ("nan", "E_TIPO"), ("-inf", "E_TIPO"), ("--1", "E_TIPO"), ("1,5", "E_TIPO"),
])
def test_decimal_invalidos(value, code):
    assert code_of(numbers.validate_decimal, value, -90, 90) == code


def test_decimal_limite_superior_excluido():
    assert numbers.validate_decimal("359.999999", 0, 360, hi_inclusive=False) == 359.999999
    assert code_of(numbers.validate_decimal, "360", 0, 360, hi_inclusive=False) == "E_RANGO"


@pytest.mark.parametrize("value,code", [
    ("<b>", "E_CARACTER"), ("=CMD()", "E_CARACTER"), ("@x", "E_CARACTER"), ("+1", "E_CARACTER"),
    ("-1", "E_CARACTER"), ("a\x00b", "E_CARACTER"), ("a", "E_LONGITUD"), ("a" * 121, "E_LONGITUD"),
])
def test_texto_invalido(value, code):
    assert code_of(text.validate_text, value, 2, 120) == code


def test_texto_valido_y_limites():
    assert text.validate_text("ab", 2, 120) == "ab"
    assert len(text.validate_text("a" * 120, 2, 120)) == 120
    assert text.validate_text("Cráter ñandú – Mar", 2, 120)


def test_texto_tab_permitido_y_formula_si_se_autoriza():
    assert text.validate_text("a\tb", 2, 10)
    assert text.validate_text("-x", 2, 10, allow_formula=True)


@pytest.mark.parametrize("value,code", [
    ("2026-12-01T00:00+01:00", "E_FORMATO"), ("2026-12-01 00:00", "E_FORMATO"), ("2026-12-01T00:00:00Z", "E_FORMATO"),
    ("2026-12-01T00:00z", "E_FORMATO"), ("2026-12-01T00:00", "E_FORMATO"), ("2026-12-01T00:00Z\n", "E_FORMATO"), ("٢٠٢٦-12-01T00:00Z", "E_FORMATO"), ("2026-02-30T00:00Z", "E_TIPO"), ("2026-12-01T24:00Z", "E_TIPO"),
])
def test_fecha_invalida(value, code):
    assert code_of(dates.parse_utc, value) == code


def test_fecha_valida_bisiesto():
    assert dates.parse_utc("2028-02-29T23:59Z").minute == 59


def test_enum_regex_url():
    assert checks.validate_enum("apolo", ("apolo", "clps")) == "apolo"
    assert code_of(checks.validate_enum, "Apolo", ("apolo",)) == "E_ENUM"
    assert code_of(checks.validate_regex, "AB", re.compile(r"^[a-z]+$")) == "E_FORMATO"
    assert checks.validate_https_url("https://nasa.gov/x")
    assert code_of(checks.validate_https_url, "http://nasa.gov") == "E_FORMATO"
    assert code_of(checks.validate_https_url, "https://na sa.gov") == "E_FORMATO"


def test_lector_elimina_bom_y_acepta_linea_final_vacia():
    header, rows = csv_reader.parse_rows(csv_reader.decode(b"\xef\xbb\xbfa,b\r\n1,2\r\n\r\n"))
    assert header == ["a", "b"] and rows == [(2, ["1", "2"])]


def test_lector_fila_vacia_intermedia_se_conserva_para_reportarla():
    _, rows = csv_reader.parse_rows("a,b\n1,2\n\n3,4\n")
    assert rows[1] == (3, [])


def test_lector_archivo_vacio_y_solo_cabecera():
    for raw in ("a,b\n", "a,b"):
        with pytest.raises(FileError) as exc:
            csv_reader.parse_rows(raw)
        assert exc.value.code == "E_ARCHIVO_VACIO"


def test_lector_espacios_solo_es_vacio():
    with pytest.raises(FileError) as exc:
        csv_reader.check_raw("sites.csv", b"  \n \n")
    assert exc.value.code == "E_ARCHIVO_VACIO"


def test_lector_comillas_mal_cerradas():
    with pytest.raises(FileError) as exc:
        csv_reader.parse_rows('a,b\n"1,2\n3,4"x\n')
    assert exc.value.code == "E_FORMATO"


def test_decimal_limites_de_latitud_y_azimut():
    assert numbers.validate_decimal("90", -90, 90) == 90.0
    assert code_of(numbers.validate_decimal, "90.000001", -90, 90) == "E_RANGO"
    assert code_of(numbers.validate_decimal, "-90.000001", -90, 90) == "E_RANGO"
    assert numbers.validate_decimal("0", 0, 360, hi_inclusive=False) == 0.0


def test_decimales_exactos_6_si_7_no():
    assert numbers.validate_decimal("0.123456", 0, 1) == 0.123456
    assert code_of(numbers.validate_decimal, "0.1234567", 0, 1) == "E_DECIMALES"


def test_nombre_de_archivo_usa_fullmatch():
    from app.core import config
    pattern = config.FILENAME_PATTERNS["sites"]
    assert csv_reader.check_filename("sites.csv", pattern)
    with pytest.raises(FileError):
        csv_reader.check_filename("sites.csv\n", pattern)


def test_nulos_disfrazados_y_recortes():
    for token in ("NULL", "nan", "N/A", "-"):
        assert code_of(text.check_not_null_token, token) == "E_FORMATO"
    assert text.clip_value("x" * 80) == "x" * 50 and text.clip_value(None) is None
    assert text.sanitize_filename("a\x00b.csv") == "ab.csv"


def test_cabecera_exacta():
    assert list(csv_reader.header_issues(["a", "b"], ["a", "b"])) == []
    codes = {i.code for i in csv_reader.header_issues(["b", "a", "a", "z"], ["a", "b", "c"])}
    assert codes == {"E_CABECERA", "E_COLUMNA_DESCONOCIDA"}
    assert next(csv_reader.header_issues(["b", "a"], ["a", "b"])).code == "E_CABECERA"
    assert csv_reader.row_width_issue([], 3) and csv_reader.row_width_issue(["x"], 3)
    assert csv_reader.row_width_issue(["x", "y", "z"], 3) is None


def test_lector_numera_filas_logicas_y_no_cuenta_la_linea_final_vacia():
    header, rows = csv_reader.parse_rows("a,b\n1,2\n\n")
    assert [n for n, _ in rows] == [2]
