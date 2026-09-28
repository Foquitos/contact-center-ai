"""
Smoke test EN VIVO de los chatbots: 1 consulta real por chatbot para confirmar
que el pipeline completo responde. CONSUME TOKENS de IA.

Ejecutar solo esta suite:   pytest tests/test_chatbots_live.py -m tokens
Excluirla del resto:        pytest -m "not tokens"

No valida la corrección del contenido (eso requeriría criterio humano), solo que
cada bot devuelva una respuesta sin error.

Nota: los servicios se importan DENTRO de los tests (no a nivel de módulo) para
que la colección de pytest sea liviana y no construya todo el stack de IA cuando
se corre la suite offline.
"""
import asyncio

import pytest

pytestmark = pytest.mark.tokens

USER_ID = 0
TASK_ID = "test_smoke"

# Los bots viven en la BD (pagina_web.Chatbots): la lista se arma dinámicamente
# desde el registry, así los bots que cree calidad quedan cubiertos solos.
PREGUNTAS = {"voltara": "¿Qué es la tarifa social?"}
PREGUNTA_DEFAULT = "¿Qué información tenés disponible?"


def _slugs_activos():
    import app.services as services

    return sorted(c.slug for c in services.chatbot_registry.list_active())


def pytest_generate_tests(metafunc):
    if "slug" in metafunc.fixturenames:
        # La colección importa services solo cuando se corre esta suite (marker
        # tokens); offline (-m "not tokens") no se construye el stack de IA.
        if metafunc.config.getoption("-m") and "not tokens" in metafunc.config.getoption("-m"):
            metafunc.parametrize("slug", [])
            return
        slugs = _slugs_activos()
        metafunc.parametrize("slug", slugs, ids=slugs)


def test_chatbot_rag_responde(slug):
    import app.services as services

    bot = services.chatbot_registry.get(slug)
    if bot is None:
        pytest.fail(f"El chatbot '{slug}' no está disponible (sin índice o falló la instanciación).")

    pregunta = PREGUNTAS.get(slug, PREGUNTA_DEFAULT)
    resp = bot.query(pregunta, USER_ID, slug, TASK_ID)
    texto = (resp.get("response") or "").strip()

    assert texto, f"[{slug}] respuesta vacía"
    assert not texto.startswith("Error"), f"[{slug}] el bot devolvió un error: {texto[:200]}"


# (id, pregunta) — una por agente del orquestador para cubrir el ruteo.
SQL_BOT_CASOS = [
    ("general", "¿Cuántos equipos de trabajo hay registrados?"),
    ("hidra", "¿Cuántas llamadas recibió Hidra ayer?"),
    ("dental", "¿Cuántos turnos se agendaron para Odonto Plus en los últimos 7 días?"),
    ("dental-canal", "¿Cuántas llamadas telefónicas (sin chats) tuvo Odonto Plus ayer?"),
    ("plansenior", "¿Cuántas llamadas tuvo Plansenior la semana pasada?"),
]


@pytest.mark.parametrize("nombre,pregunta", SQL_BOT_CASOS, ids=[c[0] for c in SQL_BOT_CASOS])
def test_chatbot_sql_responde(nombre, pregunta):
    import app.services as services

    if services.chatbot_sql is None:
        pytest.fail("El ChatbotSQLService no se inicializó (instancia None).")

    resultado = asyncio.run(services.chatbot_sql.consultar_con_analisis(pregunta))

    assert "error" not in resultado, f"ChatbotSQL devolvió error: {resultado.get('error')} / {resultado.get('detalle')}"
    assert resultado.get("query_generada"), "ChatbotSQL no generó ninguna query SQL"
    assert resultado.get("resultados"), "ChatbotSQL no devolvió resultados por agente"
