"""El costeo de los tokens cacheados (migración 2026-09-01b).

Desde que las auditorías usan la caché de contexto (ver AuditorIA/cache_plantillas.py),
una parte del input se cobra ~10 veces más barato. `prompt_token_count` de Gemini
INCLUYE esos tokens, así que la fórmula tiene que separar las dos porciones: si se
suman las dos enteras se cobra de más, y si se ignora la caché se cobra el input
completo y el ahorro queda invisible en la pantalla de gastos.

Esta es la mitad Python de la fórmula; la otra mitad es pagina_web.vw_IA_Uso_Costos y
las dos tienen que decir lo mismo (ver el comentario en MODOS_MITAD_DE_PRECIO).

El costo se redondea a 4 decimales (conducta previa de `calcular_costo_usd`), así que
los esperados se comparan redondeados igual.

Test 100% offline: no toca DB ni Gemini.
"""
import pytest

from AuditorIA.execution_log import calcular_costo_usd


# gemini-3.8-flash con el precio promocional: input 0,75 / output 3,75 / caché 0,075
TARIFA = (0.75, 3.75, 0.075)


def test_sin_tokens_cacheados_cuesta_igual_que_antes():
    """La fila de una auditoría vieja (o de un modelo sin caché) no cambia de precio."""
    tokens = {"input_tokens": 10_000, "output_tokens": 500, "thoughts_tokens": 1_500}
    # 10.000 x 0,75 + 2.000 x 3,75, todo por millón
    assert calcular_costo_usd(tokens, "sync", TARIFA) == pytest.approx(0.0150, abs=1e-6)


def test_los_cacheados_se_cobran_aparte_y_no_se_suman_de_mas():
    """El caso real: 3.847 de los 10.000 tokens de entrada vienen de la caché."""
    tokens = {"input_tokens": 10_000, "output_tokens": 500,
              "thoughts_tokens": 1_500, "cached_tokens": 3_847}
    # (10.000-3.847) x 0,75 + 3.847 x 0,075 + 2.000 x 3,75
    esperado = round((6153 * 0.75 + 3847 * 0.075 + 2000 * 3.75) / 1_000_000, 4)
    assert calcular_costo_usd(tokens, "sync", TARIFA) == esperado
    # Y tiene que salir MÁS BARATO que la misma fila sin caché.
    sin_cache = calcular_costo_usd(
        {k: v for k, v in tokens.items() if k != "cached_tokens"}, "sync", TARIFA)
    assert calcular_costo_usd(tokens, "sync", TARIFA) < sin_cache


def test_el_batch_sigue_valiendo_la_mitad_de_todo():
    tokens = {"input_tokens": 10_000, "output_tokens": 500,
              "thoughts_tokens": 1_500, "cached_tokens": 3_847}
    sync = calcular_costo_usd(tokens, "sync", TARIFA)
    assert calcular_costo_usd(tokens, "batch", TARIFA) == pytest.approx(sync / 2, abs=1e-6)
    assert calcular_costo_usd(tokens, "flex", TARIFA) == pytest.approx(sync / 2, abs=1e-6)


def test_una_tarifa_vieja_de_dos_valores_sigue_funcionando():
    """`obtener_tarifa` devolvía (input, output) hasta la migración 2026-09-01b. Si
    algún llamador guardó una de esas, el caché se cobra al 10% del input en vez de
    reventar por índice."""
    tokens = {"input_tokens": 10_000, "output_tokens": 0,
              "thoughts_tokens": 0, "cached_tokens": 4_000}
    esperado = round((6000 * 0.75 + 4000 * 0.075) / 1_000_000, 4)
    assert calcular_costo_usd(tokens, "sync", (0.75, 3.75)) == esperado


def test_una_fila_con_mas_cacheados_que_input_no_resta_ni_regala():
    """Pasa de verdad: hay filas de `asistente_docs` con cacheados registrados y el
    input en NULL. Esos tokens se consumieron igual, así que se cobran a precio de
    caché; lo que no puede pasar es que la resta se vaya a negativo y descuente plata.

    Es la MISMA cuenta que hace el CASE de pagina_web.vw_IA_Uso_Costos: validado
    contra las filas reales de los últimos 30 días, las dos dan lo mismo."""
    tokens = {"input_tokens": 0, "output_tokens": 0,
              "thoughts_tokens": 0, "cached_tokens": 5_000_000}
    costo = calcular_costo_usd(tokens, "sync", TARIFA)
    assert costo == pytest.approx(5 * 0.075, abs=1e-4)   # 5M x 0,075
    assert costo >= 0


def test_sin_tarifa_no_inventa_un_costo():
    assert calcular_costo_usd({"input_tokens": 10_000}, "sync", None) is None
