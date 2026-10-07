"""ETL: decodificación y reparación de codificación (Req. 1) y sanitización de HTML (Req. 2)."""

from __future__ import annotations

import pytest
from hypothesis import assume, given, strategies as st

from core.encoding import UnreadableCsv, decode_rows, fix_mojibake
from core.html_clean import has_target_tags, sanitize_html

SAFE_TEXT = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ABCXYZ0123456789áéíóúñü.,", min_size=1, max_size=60)


# ---- decodificación -------------------------------------------------------------------------------------

def test_utf8_con_bom_conserva_el_sku_como_texto() -> None:
    data = b"\xef\xbb\xbfsku,nombre\n000375947,Crema\n"
    rows, skipped, header = decode_rows(data)
    assert header == ["sku", "nombre"]
    assert rows == [{"sku": "000375947", "nombre": "Crema"}]
    assert skipped == []


def test_campos_entre_comillas_con_coma_y_salto_de_linea() -> None:
    rows, _, _ = decode_rows('a,b\n"x, y","l1\nl2"\n'.encode("utf-8"))
    assert rows == [{"a": "x, y", "b": "l1\nl2"}]


def test_archivo_en_windows_1252_se_lee_sin_perder_acentos() -> None:
    data = "sku,nombre\n1,Limpiador hidratación\n2,Piña\n".encode("cp1252")
    rows, skipped, _ = decode_rows(data)
    assert [r["nombre"] for r in rows] == ["Limpiador hidratación", "Piña"]
    assert skipped == []


def test_texto_doble_codificado_se_repara() -> None:
    mojibake = "hidratación".encode("utf-8").decode("latin-1")
    assert mojibake != "hidratación"
    rows, _, _ = decode_rows(f"sku,nombre\n1,{mojibake}\n".encode("utf-8"))
    assert rows[0]["nombre"] == "hidratación"


def test_omite_exactamente_las_filas_no_decodificables_con_numero_base_1() -> None:
    # Fila 1 = encabezado. La fila 3 contiene 0x81, que no existe en UTF-8 ni en Windows-1252.
    data = b"sku,nombre\n1,Cami\xf3n\n2,\x81\n3,Sol\n"
    rows, skipped, _ = decode_rows(data)
    assert skipped == [3]
    assert [r["sku"] for r in rows] == ["1", "3"]
    assert rows[0]["nombre"] == "Camión"


def test_una_fila_con_caracter_de_reemplazo_se_omite() -> None:
    rows, skipped, _ = decode_rows("sku,nombre\n1,ok\n2,mal\ufffd\n".encode("utf-8"))
    assert skipped == [3]
    assert [r["sku"] for r in rows] == ["1"]


def test_filas_con_mas_o_menos_columnas_se_ajustan_al_encabezado() -> None:
    rows, _, _ = decode_rows(b"a,b,c\n1,2\n1,2,3,4\n")
    assert rows == [{"a": "1", "b": "2", "c": ""}, {"a": "1", "b": "2", "c": "3"}]


def test_lineas_en_blanco_se_ignoran_y_solo_encabezado_da_cero_filas() -> None:
    assert decode_rows(b"a,b\n\n1,2\n")[0] == [{"a": "1", "b": "2"}]
    rows, skipped, header = decode_rows(b"a,b\n")
    assert rows == [] and skipped == [] and header == ["a", "b"]


@pytest.mark.parametrize("data", [b"", b"   \n\t", b",,\n1,2,3\n"])
def test_archivo_vacio_o_sin_encabezado_legible_lanza_error(data: bytes) -> None:
    with pytest.raises(UnreadableCsv):
        decode_rows(data)


# ---- Feature: skincare-voice-advisor, Property 1 y 2: reparación de mojibake -------------------------

@given(SAFE_TEXT)
def test_texto_limpio_no_cambia_y_la_reparacion_es_idempotente(text: str) -> None:
    assert fix_mojibake(text) == text
    assert fix_mojibake(fix_mojibake(text)) == fix_mojibake(text)


@given(SAFE_TEXT)
def test_la_reparacion_recupera_el_texto_original(text: str) -> None:
    assume(any(c in text for c in "áéíóúñü"))
    broken = text.encode("utf-8").decode("latin-1")
    assert fix_mojibake(broken) == text


def test_fix_mojibake_no_toca_comillas_ni_ligaduras() -> None:
    text = "“comillas” — ﬁ ligadura y ‘simples’"
    assert fix_mojibake(text) == text


# ---- sanitización de HTML ------------------------------------------------------------------------------

def test_listas_y_parrafos_pasan_a_saltos_de_linea() -> None:
    assert sanitize_html("<p>Hola</p><ul><li>Uno</li><li>Dos</li></ul>") == "Hola\n\nUno\n\nDos"


def test_insensible_a_mayusculas_atributos_y_autocierre() -> None:
    assert sanitize_html('<P class="x">A</P><BR/>B') == "A\n\nB"
    assert sanitize_html("linea<br />siguiente") == "linea\nsiguiente"


def test_etiquetas_de_formato_se_quitan_sin_salto() -> None:
    assert sanitize_html("<b>Negrita</b> y <strong>fuerte</strong>") == "Negrita y fuerte"


def test_etiqueta_formada_al_quitar_otra_tambien_se_elimina() -> None:
    assert sanitize_html("<<b>p>hola") == "hola"


def test_marcadores_internos_del_pim() -> None:
    assert sanitize_html("<iln12345>texto") == "texto"


def test_limita_saltos_consecutivos_a_dos_y_recorta_extremos() -> None:
    result = sanitize_html("<p></p><p></p><p>a</p><p></p><p></p><p>b</p><p></p>")
    assert result == "a\n\nb"
    assert "\n\n\n" not in result


# ---- Feature: skincare-voice-advisor, Property 5: identidad sin etiquetas ---------------------------

@pytest.mark.parametrize("value", [None, 5, 3.5, ["<p>"], "", "sin etiquetas", "precio < 100 y > 50", "<bold>x</bold>"])
def test_devuelve_sin_cambios_lo_que_no_es_texto_con_etiquetas(value: object) -> None:
    assert sanitize_html(value) == value


@given(st.text(alphabet="abc XYZ019\n.,áé&;", max_size=80))
def test_texto_sin_etiquetas_queda_identico(text: str) -> None:
    assert sanitize_html(text) == text


# ---- Feature: skincare-voice-advisor, Property 4: quita etiquetas, conserva el texto, idempotente ---

@given(tag=st.sampled_from(["b", "p", "li", "strong", "em", "span", "div"]), text=SAFE_TEXT)
def test_envolver_texto_en_una_etiqueta_y_sanitizar_devuelve_el_texto(tag: str, text: str) -> None:
    assert sanitize_html(f"<{tag}>{text}</{tag}>") == text.strip()


@given(
    st.lists(
        st.sampled_from(["<p>", "</p>", "<li>", "</li>", "<ul>", "</ul>", "<br>", "<b>", "</b>", "hola", "mundo", " ", "\n"]),
        max_size=25,
    )
)
def test_sanitizar_no_deja_etiquetas_es_idempotente_y_limita_saltos(parts: list[str]) -> None:
    raw = "".join(parts)
    result = sanitize_html(raw)
    assert not has_target_tags(result)
    assert sanitize_html(result) == result
    if has_target_tags(raw):
        # Solo se limpia el formato cuando había etiquetas; sin ellas el texto queda idéntico (Req. 2.5).
        assert "\n\n\n" not in result
        assert result == result.strip()
    else:
        assert result == raw
