"""Tests offline de las tablas de datos de los chatbots (app/chatbot_tablas.py).

No tocan la BD: las tablas se construyen a mano y `obtener_tablas` se
monkeypatchea. Los casos NO son inventados — son las consultas reales que están
en pagina_web.query_chatbots_logs y que hoy fallan por el camino RAG:

    vantix   "sucursal de palermo" (score -8,43), "sucursal boedo" (-6,51),
            "en que sucursal entran motos?" (-2,22), "sucursal en zona sur apto
            utilitario" (-1,65), "que le puedo ofrecer a un cliente de gualeguay"
            (-2,70), "mail de responsables de la suc de cordoba" (+2,17)
    benefix "gestor de cobranza de la razon social ALFA - RED SA",
            "gestor de cobranzas del cliente 33815.1"

Y los dos casos que el usuario pidió que funcionaran y hoy son imposibles:
"qué taller de moto hay en zona norte" y "a qué taller puede ir un cliente de
Benavídez" — el segundo no puede resolverlo ningún retriever, porque la palabra
"Benavídez" no está escrita en ninguna parte del corpus.
"""
import pytest

from app import chatbot_tablas
from app.chatbot_tablas import Columna, Fila, Tabla
from app.config import settings


# ------------------------------------------------------------------ fixtures

def _tabla(nombre, descripcion, columnas, claves, filas_datos, modo="auto",
           terminos=(), nota="", tabla_id=1, chatbot_id=1):
    cols = [
        Columna(nombre=c, clave=(c in claves))
        for c in columnas
    ]
    filas = []
    for i, datos in enumerate(filas_datos):
        busqueda = " | ".join(str(datos.get(c, "")) for c in claves)
        filas.append(Fila(orden=i, datos={k: str(v) for k, v in datos.items()},
                          busqueda=chatbot_tablas.normalizar(busqueda)))
    tabla = Tabla(
        id=tabla_id, chatbot_id=chatbot_id, nombre=nombre, descripcion=descripcion,
        terminos=[chatbot_tablas.normalizar(t) for t in terminos],
        columnas=cols, nota=nota, modo_declarado=modo, filas=filas,
    )
    tabla.preparar()
    return tabla


# Las bases de vantix tal como están en el documento 15 (subconjunto real).
BASES_VANTIX = [
    {"Base": "Base Móvil Microcentro - Paseo La Plaza", "Zona": "CABA",
     "Dirección": "Montevideo 350, CABA", "Vehículos aptos": "Autos y motos",
     "Acepta pago en base": "Sí", "Responsables": "persona1@vantix.example"},
    {"Base": "Base Móvil Autocentro JBJ", "Zona": "CABA",
     "Dirección": "Av. Juan B Justo 5439", "Vehículos aptos": "Autos, 4x4, motos",
     "Acepta pago en base": "No", "Responsables": "persona1@vantix.example"},
    {"Base": "Autocentro Tigre", "Zona": "Norte",
     "Dirección": "Av. Agustín García 5392, Tigre", "Vehículos aptos": "Autos grandes y 4x4",
     "Acepta pago en base": "No", "Responsables": "persona2@vantix.example"},
    {"Base": "Autocentro Pilar", "Zona": "Norte",
     "Dirección": "José Evaristo Uriburu 164, Pilar", "Vehículos aptos": "Autos grandes y motos",
     "Acepta pago en base": "No", "Responsables": "persona2@vantix.example"},
    {"Base": "Autocentro Escobar", "Zona": "Norte",
     "Dirección": "Av. 25 de Mayo 835, Belén de Escobar", "Vehículos aptos": "Autos grandes y motos",
     "Acepta pago en base": "No", "Responsables": "persona2@vantix.example"},
    {"Base": "Sarandí", "Zona": "Sur", "Dirección": "Madariaga 133, Sarandí",
     "Vehículos aptos": "Autos grandes y motos", "Acepta pago en base": "Sí",
     "Responsables": "persona3@vantix.example"},
    {"Base": "Base Movil Autocentro Morón", "Zona": "Oeste",
     "Dirección": "Blvd. Juan Manuel de Rosas 473", "Vehículos aptos": "Autos y 4x4",
     "Acepta pago en base": "No", "Responsables": "persona1@vantix.example"},
    {"Base": "Base Móvil Bahía Blanca", "Zona": "Interior de Buenos Aires",
     "Dirección": "Maldonado 680", "Vehículos aptos": "Todo tipo excepto motos",
     "Acepta pago en base": "Sí", "Responsables": "persona4@vantix.example"},
    {"Base": "Córdoba Ducasse", "Zona": "Interior",
     "Dirección": "Córdoba capital", "Vehículos aptos": "Autos y motos",
     "Acepta pago en base": "No", "Responsables": "persona5@vantix.example"},
]


@pytest.fixture
def bases():
    return _tabla(
        "Bases y talleres de instalación",
        "Dónde se puede instalar el dispositivo: dirección, zona, qué vehículos acepta "
        "cada base y quiénes son sus responsables.",
        ["Base", "Zona", "Dirección", "Vehículos aptos", "Acepta pago en base", "Responsables"],
        claves=["Base"],
        filas_datos=BASES_VANTIX,
        terminos=["base", "sucursal", "taller", "instalar", "direccion", "mail de responsable"],
        tabla_id=1,
    )


# La cartera de benefix: misma forma que los documentos 68..82, con las dos
# Solange que hoy ningún embedding puede distinguir.
CARTERA_BENEFIX = [
    {"Razón Social": "ALFA - RED SA", "N° Cliente": "33815", "SubCta": "1",
     "Cl2": "33815.1", "Gestor de Cobranzas": "DANIEL CORREA"},
    {"Razón Social": "CONEXIONES DEL PLATA SA", "N° Cliente": "29075", "SubCta": "2",
     "Cl2": "29075.2", "Gestor de Cobranzas": "DANIEL CORREA"},
    {"Razón Social": "C.H.ROBLEDO GLOBAL ARG.SA", "N° Cliente": "18523", "SubCta": "2",
     "Cl2": "18523.2", "Gestor de Cobranzas": "MAGALI MOLINA"},
    {"Razón Social": "ROBLEDO LOGISTICS S.A.", "N° Cliente": "33358", "SubCta": "2",
     "Cl2": "33358.2", "Gestor de Cobranzas": "MAGALI MOLINA"},
    {"Razón Social": "MUNICIPIO/HACIENDA", "N° Cliente": "20874", "SubCta": "7",
     "Cl2": "20874.7", "Gestor de Cobranzas": "SOLANGE CABRERA"},
    {"Razón Social": "ENERGIA DEL NORTE SA", "N° Cliente": "30106", "SubCta": "1",
     "Cl2": "30106.1", "Gestor de Cobranzas": "SOLANGE ACOSTA"},
    {"Razón Social": "ENERGIA DEL NORTE SA", "N° Cliente": "30106", "SubCta": "2",
     "Cl2": "30106.2", "Gestor de Cobranzas": "SOLANGE ACOSTA"},
    {"Razón Social": "COBRANZASUR S.A.DELE INSTALACIO", "N° Cliente": "15054", "SubCta": "7",
     "Cl2": "15054.7", "Gestor de Cobranzas": "LAURA FERNANDEZ"},
]


@pytest.fixture
def cartera():
    return _tabla(
        "Cartera de cobranzas",
        "Qué gestor de cobranzas tiene asignado cada cliente, por razón social, "
        "número de cliente o clave Cl2.",
        ["Razón Social", "N° Cliente", "SubCta", "Cl2", "Gestor de Cobranzas"],
        claves=["Razón Social", "N° Cliente", "Cl2"],
        filas_datos=CARTERA_BENEFIX,
        modo="lookup",
        terminos=["gestor", "cartera", "cobranzas"],
        nota="No informar teléfonos de cobranzas de forma proactiva.",
        tabla_id=2,
    )


@pytest.fixture
def rutear(monkeypatch):
    """resolver() contra un conjunto fijo de tablas, sin tocar la BD."""
    def _instalar(*tablas):
        monkeypatch.setattr(chatbot_tablas, "obtener_tablas", lambda cid: list(tablas))
    return _instalar


# ------------------------------------------------------- normalización/tokens

def test_tokenizar_conserva_la_clave_compuesta():
    """33815.1 es la clave Cl2 de benefix: partirla en '33815' y '1' la haría
    indistinguible de 33815.2, que es OTRO cliente."""
    tokens = chatbot_tablas.tokenizar("Cl2 33815.1")
    assert "33815.1" in tokens
    # Y además las partes, para que "el cliente 33815" también encuentre la fila.
    assert "33815" in tokens


def test_normalizar_saca_acentos_y_mayusculas():
    assert chatbot_tablas.normalizar("  Bahía  BLANCA ") == "bahia blanca"


# --------------------------------------------------------- lookup por clave

def test_encuentra_por_razon_social(cartera):
    """Consulta real del log: 'gestor de cobranza de la razon social ALFA - RED SA'."""
    filas = cartera.buscar("gestor de cobranza de la razon social ALFA - RED SA")
    assert [f.datos["Gestor de Cobranzas"] for f in filas] == ["DANIEL CORREA"]


def test_encuentra_por_clave_cl2(cartera):
    """Consulta real del log: 'gestor de cobranzas del cliente 33815.1'."""
    filas = cartera.buscar("gestor de cobranzas del cliente 33815.1")
    assert filas and filas[0].datos["Razón Social"] == "ALFA - RED SA"


def test_no_confunde_subcuentas_distintas(cartera):
    """30106.1 y 30106.2 son dos filas: pedir una no puede traer la otra primero."""
    filas = cartera.buscar("cliente 30106.2")
    assert filas[0].datos["Cl2"] == "30106.2"


def test_devuelve_todas_las_subcuentas_de_una_razon_social(cartera):
    """ENERGIA DEL NORTE tiene dos subcuentas: el operador tiene que verlas las
    dos, no una elegida al azar."""
    filas = cartera.buscar("quien gestiona ENERGIA DEL NORTE SA")
    assert {f.datos["Cl2"] for f in filas} == {"30106.1", "30106.2"}


def test_los_sufijos_societarios_no_arrastran_filas(cartera):
    """'SA' está en casi todas las razones sociales: si pesara, cualquier consulta
    traería media tabla. Lo neutraliza el IDF."""
    filas = cartera.buscar("gestor de la razon social CONEXIONES DEL PLATA SA")
    assert len(filas) == 1
    assert filas[0].datos["Razón Social"] == "CONEXIONES DEL PLATA SA"


def test_consulta_ajena_no_matchea_ninguna_fila(cartera):
    """'consulta de saldo' es una consulta de procedimiento de benefix: tiene que
    seguir de largo al RAG, no colarse en la cartera."""
    assert cartera.buscar("como realizo una consulta de saldo") == []


def test_tolera_el_nombre_escrito_distinto(cartera):
    """El operador escribe 'ROBLEDO LOGISTICS' sin el S.A."""
    filas = cartera.buscar("gestor de ROBLEDO LOGISTICS")
    assert filas[0].datos["Razón Social"] == "ROBLEDO LOGISTICS S.A."


# ------------------------------------------------------------ modo y ruteo

def test_tabla_chica_entra_entera(bases):
    assert bases.modo == "completa"


def test_tabla_grande_pasa_a_lookup(monkeypatch, bases):
    """El modo 'auto' sigue al tamaño: la misma tabla, si crece, deja de entrar."""
    monkeypatch.setattr(settings, "CHATBOT_TABLA_MAX_CHARS_COMPLETA", 10)
    assert bases.modo == "lookup"


def test_rutea_por_termino_y_manda_la_tabla_entera(rutear, bases):
    """'en que sucursal entran motos?' (score real -2,22 por RAG). No nombra
    ninguna base, así que la única forma de responder es ver la tabla completa."""
    rutear(bases)
    consulta = chatbot_tablas.resolver(1, "en que sucursal entran motos?")
    assert consulta is not None
    assert consulta.forma == "completa"
    assert consulta.motivo == "termino"
    assert len(consulta.filas) == len(BASES_VANTIX)


def test_rutea_una_localidad_que_no_esta_en_la_tabla(rutear, bases):
    """El caso de Benavídez: la palabra no aparece en ninguna fila, y aun así la
    consulta tiene que llegar a la tabla — con las filas delante el modelo puede
    ubicar la base más cercana, que es algo que ningún retriever puede hacer."""
    rutear(bases)
    consulta = chatbot_tablas.resolver(1, "a que taller puede ir un cliente de Benavidez")
    assert consulta is not None and consulta.forma == "completa"


def test_rutea_por_nombre_de_base(rutear, bases):
    """'daytona tigre' (consulta real del log): la clave está escrita tal cual."""
    rutear(bases)
    consulta = chatbot_tablas.resolver(1, "daytona tigre")
    assert consulta is not None and consulta.motivo == "clave"
    # Aunque haya identificado una fila, la tabla es chica: va entera, para que el
    # modelo pueda comparar con las otras si el operador repregunta.
    assert consulta.forma == "completa"


def test_consulta_de_procedimiento_no_rutea(rutear, bases):
    """'como se realiza la instalacion' (+4,08 por RAG, o sea: el RAG la responde
    bien). Si la robara la tabla, se rompería algo que hoy funciona."""
    rutear(bases)
    assert chatbot_tablas.resolver(1, "como se realiza la instalacion") is None


def test_dos_tablas_del_mismo_bot_no_se_pisan(rutear, bases, cartera):
    """Con las dos tablas cargadas, cada consulta tiene que ir a la suya."""
    rutear(bases, cartera)
    a = chatbot_tablas.resolver(1, "gestor de cobranzas del cliente 33815.1")
    b = chatbot_tablas.resolver(1, "mail de responsables de la suc de cordoba")
    assert a is not None and a.tabla.nombre == "Cartera de cobranzas"
    assert b is not None and b.tabla.nombre == "Bases y talleres de instalación"


def test_lookup_sin_clave_devuelve_el_resumen(rutear, cartera):
    """'que gestores de cobranza hay?' no nombra ningún cliente: no hay nada que
    buscar. En vez de volcar 2.500 filas (o de mandarla al RAG, donde el documento
    ya no está) se responde con el resumen."""
    rutear(cartera)
    consulta = chatbot_tablas.resolver(1, "que gestores de cobranza hay")
    assert consulta is not None
    assert consulta.forma == "resumen"
    assert consulta.filas == []
    contexto = consulta.contexto()
    assert "DANIEL CORREA" in contexto and "SOLANGE ACOSTA" in contexto
    # El resumen NO lista las columnas identificatorias: son una por fila.
    assert "ALFA - RED SA" not in contexto


def test_el_resumen_no_incluye_columnas_de_alta_cardinalidad(cartera):
    resumen = cartera.resumen()
    assert "Gestor de Cobranzas" in resumen
    assert "Cl2" not in resumen


def test_kill_switch(monkeypatch, cartera):
    monkeypatch.setattr(settings, "CHATBOT_TABLAS_ACTIVO", False)
    chatbot_tablas.invalidar_cache()
    assert chatbot_tablas.obtener_tablas(1) == []


# --------------------------------------------------------------- contexto

def test_el_contexto_lleva_la_nota_de_politica(rutear, cartera):
    """La política de benefix ('no informar de forma proactiva') no puede depender
    de que alguien se acuerde de ponerla en el system prompt: viaja con la tabla."""
    rutear(cartera)
    consulta = chatbot_tablas.resolver(1, "gestor del cliente 33815.1")
    assert "No informar teléfonos" in consulta.contexto()


def test_el_contexto_de_lookup_solo_trae_las_filas_encontradas(rutear, cartera):
    rutear(cartera)
    consulta = chatbot_tablas.resolver(1, "gestor de cobranza de la razon social ALFA - RED SA")
    contexto = consulta.contexto()
    assert "ALFA - RED SA" in contexto
    # El resto de la cartera no viaja: ese es todo el punto del modo lookup.
    assert "CONEXIONES DEL PLATA" not in contexto
    assert "COBRANZASUR" not in contexto


def test_el_log_guarda_el_contexto_real(rutear, bases):
    """query_chatbots_logs.context tiene que dejar reconstruir por qué respondió lo
    que respondió, igual que con los chunks del RAG."""
    rutear(bases)
    consulta = chatbot_tablas.resolver(1, "daytona tigre")
    log = consulta.para_log()
    assert "[tabla:Bases y talleres de instalación]" in log
    assert "motivo=clave" in log
    assert "Autocentro Tigre" in log


# ================================================== parseo del markdown (ingesta)

from app import chatbot_tablas_admin  # noqa: E402

# El documento 68 de benefix trae la MISMA cartera dos veces, ordenada distinto
# (una por razón social, otra por número de cliente), porque era la única forma de
# que el chunk correcto le llegara al retriever según cómo preguntara el operador.
MD_CARTERA_DUPLICADA = """
# Cartera de Cobranzas - Daniel Curcio

## Información general
- **Gestor:** Daniel Curcio

### Razones Sociales: R - Z

| Razón Social | N° Cliente | Cl2 | Gestor de Cobranzas |
| :--- | :--- | :--- | :--- |
| CONEXIONES DEL PLATA SA | 29075 | 29075.2 | DANIEL CORREA |
| RENUEVA SA | 30618 | 30618.1 | DANIEL CORREA |

### Clientes: 30000 a 39999

| N° Cliente | Cl2 | Razón Social | Gestor de Cobranzas |
| :--- | :--- | :--- | :--- |
| 30618 | 30618.1 | RENUEVA SA | DANIEL CORREA |
| 33815 | 33815.1 | ALFA - RED SA | DANIEL CORREA |
"""


def test_extrae_las_tablas_markdown():
    tablas = chatbot_tablas_admin.extraer_tablas(MD_CARTERA_DUPLICADA)
    assert len(tablas) == 2
    assert tablas[0]["filas"][0]["Razón Social"] == "CONEXIONES DEL PLATA SA"


def test_las_dos_vueltas_de_la_misma_tabla_quedan_en_grupos_separados():
    """Mismas columnas en OTRO orden = grupos distintos, a propósito.

    Las filas se conservan también en crudo (posicionales), así que juntarlas por
    conjunto de columnas correría los valores una columna. La duplicación del doc
    68 se colapsa después, en `deduplicar`, que compara por clave."""
    grupos = chatbot_tablas_admin.agrupar_tablas(
        chatbot_tablas_admin.extraer_tablas(MD_CARTERA_DUPLICADA)
    )
    assert len(grupos) == 2
    assert sum(len(g["filas"]) for g in grupos) == 4


def test_la_duplicacion_del_documento_se_colapsa():
    """RENUEVA SA está en las dos vueltas: tiene que quedar una sola vez."""
    grupos = chatbot_tablas_admin.agrupar_tablas(
        chatbot_tablas_admin.extraer_tablas(MD_CARTERA_DUPLICADA)
    )
    todas = [f for g in grupos for f in g["filas"]]
    filas, duplicadas = chatbot_tablas_admin.deduplicar(todas, ["Cl2"])
    assert duplicadas == 1
    assert sorted(f["Cl2"] for f in filas) == ["29075.2", "30618.1", "33815.1"]


def test_respeta_los_pipes_escapados():
    md = "| Base | Nota |\n| --- | --- |\n| Sarandí | abre 9 \\| 18 |"
    filas = chatbot_tablas_admin.extraer_tablas(md)[0]["filas"]
    assert filas[0]["Nota"] == "abre 9 | 18"


def test_ignora_las_filas_rotas():
    """Una fila con menos celdas que el encabezado es un error de la fuente, no un
    dato: entra como fila incompleta y desplaza todas las columnas."""
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 |\n| 4 | 5 |"
    filas = chatbot_tablas_admin.extraer_tablas(md)[0]["filas"]
    assert filas == [{"A": "1", "B": "2"}, {"A": "4", "B": "5"}]


def test_detecta_una_clave_inventada():
    """La guarda contra invenciones: si una entidad no está escrita en el material,
    no puede aparecer en la tabla."""
    filas = [{"Base": "Autocentro Tigre"}, {"Base": "Autocentro Marte"}]
    sospechosas = chatbot_tablas_admin.filas_no_verificadas(
        filas, ["Base"], "La base Autocentro Tigre queda en Av. García 5392."
    )
    assert sospechosas == ["Base: Autocentro Marte"]


# --------------------------------------------------- detección barata (sin IA)

def test_detecta_un_listado_en_prosa():
    """Las bases de vantix no son una tabla markdown: son bloques 'Campo: valor'
    repetidos. Si esto no diera positivo, nunca se le preguntaría a la IA."""
    bloques = []
    for i in range(9):
        bloques.append(
            f"Base {i}\nDirección: Calle {i}\nEntrecalles: A y B\nVehículos aptos: Autos"
        )
    assert chatbot_tablas_admin.parece_tabla("\n\n".join(bloques))


def test_detecta_una_tabla_markdown_larga():
    filas = "\n".join(f"| CLIENTE {i} | {i} |" for i in range(12))
    assert chatbot_tablas_admin.parece_tabla(f"| Razón Social | Cl2 |\n| --- | --- |\n{filas}")


def test_un_procedimiento_no_dispara_el_analisis():
    """El filtro tiene que ser barato Y correcto: un procedimiento con un cuadro
    chico no puede pagar una llamada a la IA en cada formateo."""
    md = (
        "# Cambio de domicilio\n\n## Procedimiento\n1. Validar identidad\n2. Cargar el caso\n\n"
        "| Dato | Obligatorio |\n| --- | --- |\n| DNI | Sí |\n| Teléfono | No |\n\n"
        "## Consideraciones\nImportante: verificar el titular.\n"
    )
    assert not chatbot_tablas_admin.parece_tabla(md)


# ============================================ el camino completo en stream_query

def test_stream_query_responde_por_la_tabla_y_no_recupera_nada(monkeypatch, bases):
    """Camino entero sin red: que la respuesta salga de la tabla, que NO se toque el
    retriever (los chunks solo competirían con el dato exacto), que no se cachee
    (dos números de cliente parecidos superan el umbral del caché) y que el log
    guarde el contexto real."""
    import asyncio
    import types as tipos
    from unittest.mock import MagicMock

    from llama_index.core.memory import ChatMemoryBuffer

    import chatBot as modulo_chatbot
    from chatBot import ChatBot

    bot = object.__new__(ChatBot)
    bot.slug = "vantix"
    bot.config = tipos.SimpleNamespace(id=1, slug="vantix")
    bot.system_prompt = "Sos el asistente de Vantix."
    bot.bm25_retriever = None
    bot.token_counter = MagicMock()
    bot.cache = MagicMock()
    bot.index = MagicMock()
    bot.reranker = MagicMock()
    bot._log_query_details = MagicMock()

    async def _memoria(user_id):
        return ChatMemoryBuffer.from_defaults(token_limit=3000)

    async def _embedding(texto):
        return [0.1, 0.2, 0.3]

    bot._get_memory_for_user_async = _memoria

    mensajes_vistos = {}

    class _Chunk:
        def __init__(self, delta):
            self.delta = delta

    async def _astream_chat(mensajes):
        mensajes_vistos["mensajes"] = mensajes

        async def _gen():
            for t in ("Autocentro ", "Tigre."):
                yield _Chunk(t)
        return _gen()

    bot.llm = tipos.SimpleNamespace(astream_chat=_astream_chat)

    consulta = chatbot_tablas.Consulta(
        tabla=bases, filas=bases.filas, forma="completa", motivo="termino"
    )
    monkeypatch.setattr(chatbot_tablas, "resolver", lambda *a, **k: consulta)
    monkeypatch.setattr(
        modulo_chatbot, "Settings",
        tipos.SimpleNamespace(embed_model=MagicMock(aget_query_embedding=_embedding)),
    )

    async def correr():
        return "".join([
            t async for t in bot.stream_query(
                "que taller de moto hay en zona norte", 42, "vantix", "task-1")
        ])

    assert asyncio.run(correr()) == "Autocentro Tigre."

    # El retriever no se construyó siquiera.
    bot.index.as_retriever.assert_not_called()
    # Ni se consultó ni se guardó el caché.
    bot.cache.check.assert_not_called()
    bot.cache.save.assert_not_called()

    # El modelo recibió las filas y la instrucción de usarlas como única fuente.
    sistema = mensajes_vistos["mensajes"][0].content
    assert "Autocentro Pilar" in sistema
    assert "Bases y talleres de instalación" in sistema
    assert bot.system_prompt in sistema

    # Y el log guarda de dónde salió la respuesta.
    contexto_logueado = bot._log_query_details.call_args[0][2]
    assert "[tabla:Bases y talleres de instalación]" in contexto_logueado


# ============================ unificar los 10 documentos de la cartera en UNA tabla

# Los encabezados REALES de los documentos 68..82 de benefix, que NO comparten
# esquema entre sí (verificado 2026-09-02 contra la base). El gestor es un VALOR
# de columna, así que los 10 tienen que terminar en una sola tabla.
MD_DOC_68 = """
| Razón Social | N° Cliente | SubCta | Cl2 | Cartera | Gestor de Cobranzas |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 4 A SRL | 28001 | 1 | 28001.1 | CORREA | DANIEL CORREA |
"""
# 69/70: igual pero la columna se llama "Cliente" en vez de "N° Cliente".
MD_DOC_70 = """
| Razón Social | Cliente | SubCta | Cl2 | Cartera | Gestor de Cobranzas |
| :--- | :--- | :--- | :--- | :--- | :--- |
| LABORATORIOS ANDINOS S.A. | 21100 | 3 | 21100.3 | ORTIZ | CARLA ORTIZ |
"""
# 75/78/79/80: mismas columnas, OTRO orden.
MD_DOC_80 = """
| Cliente | SubCta | Cl2 | Razón Social | Cartera | Gestor de Cobranzas |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1838 | 4 | 1838.4 | FERROVIAS DEL ESTE S.A./XC | FERNANDEZ | LAURA FERNANDEZ |
"""
# 81/82: el encabezado está ROTO (1 columna declarada sobre filas de 6) y encima
# el orden es otro: cartera, gestor, Cl2, cliente, subcta, razón social.
MD_DOC_81 = """
| Razón Social |
| :--- | :--- | :--- | :--- | :--- | :--- |
| BENITEZ | SOLANGIE BENITEZ | 1824.23 | 1824 | 23 | AUTOMOTORES DEL SUR |
| BENITEZ | SOLANGIE BENITEZ | 1900.1 | 1900 | 1 | OTRA EMPRESA SA |
"""

CANONICAS = ["Razón Social", "N° Cliente", "SubCta", "Cl2", "Cartera", "Gestor de Cobranzas"]


def _grupos_de_la_cartera():
    md = "\n\n".join([MD_DOC_68, MD_DOC_70, MD_DOC_80, MD_DOC_81])
    return chatbot_tablas_admin.agrupar_tablas(chatbot_tablas_admin.extraer_tablas(md))


def test_los_documentos_de_la_cartera_dan_grupos_distintos():
    """Confirma el hallazgo: los 10 docs NO comparten esquema. Si dieran un solo
    grupo, el mapeo de la IA sobraría."""
    grupos = _grupos_de_la_cartera()
    assert len(grupos) == 4
    # Y el del encabezado roto viene sin nombres pero CON sus filas.
    roto = [g for g in grupos if g["columnas"] is None]
    assert len(roto) == 1
    assert roto[0]["ancho"] == 6
    assert len(roto[0]["filas_crudas"]) == 2


def test_el_encabezado_roto_no_se_descarta_en_silencio():
    """Los docs 81 y 82 son 1.384 filas, el 30% de la cartera. Con el encabezado
    roto no se pueden nombrar las columnas, pero las filas están enteras."""
    tabla = chatbot_tablas_admin.extraer_tablas(MD_DOC_81)[0]
    assert tabla["columnas"] is None      # no se inventa un nombre
    assert tabla["filas"] == []           # y por eso no hay filas armadas...
    assert len(tabla["filas_crudas"]) == 2  # ...pero los datos siguen ahí
    assert tabla["filas_crudas"][0][5] == "AUTOMOTORES DEL SUR"


def test_unifica_los_cuatro_esquemas_en_una_tabla():
    """El caso completo: cuatro esquemas distintos (uno sin encabezado) y un solo
    juego de columnas, con el mapeo que daría la IA."""
    grupos = _grupos_de_la_cartera()
    mapeos = {
        0: ["Razón Social", "N° Cliente", "SubCta", "Cl2", "Cartera", "Gestor de Cobranzas"],
        1: ["Razón Social", "N° Cliente", "SubCta", "Cl2", "Cartera", "Gestor de Cobranzas"],
        2: ["N° Cliente", "SubCta", "Cl2", "Razón Social", "Cartera", "Gestor de Cobranzas"],
        3: ["Cartera", "Gestor de Cobranzas", "Cl2", "N° Cliente", "SubCta", "Razón Social"],
    }
    unificado = chatbot_tablas_admin.unificar_grupos(grupos, CANONICAS, mapeos)

    assert unificado["grupos_descartados"] == []
    assert len(unificado["filas"]) == 5
    # Cada fila quedó con los valores en la columna correcta, venga del esquema
    # que venga.
    por_cl2 = {f["Cl2"]: f for f in unificado["filas"]}
    assert por_cl2["28001.1"]["Razón Social"] == "4 A SRL"
    assert por_cl2["1838.4"]["Gestor de Cobranzas"] == "LAURA FERNANDEZ"
    assert por_cl2["1824.23"]["Razón Social"] == "AUTOMOTORES DEL SUR"
    assert por_cl2["1824.23"]["N° Cliente"] == "1824"


def test_un_grupo_sin_mapeo_se_reporta_en_vez_de_adivinarse():
    """Meter filas con las columnas corridas es peor que no meterlas: sin mapeo
    válido el grupo queda afuera y se avisa."""
    grupos = _grupos_de_la_cartera()
    mapeos = {0: CANONICAS}   # los otros tres, sin mapeo
    unificado = chatbot_tablas_admin.unificar_grupos(grupos, CANONICAS, mapeos)
    assert len(unificado["filas"]) == 1
    assert len(unificado["grupos_descartados"]) == 3


def test_un_mapeo_de_largo_equivocado_no_corre_las_columnas():
    grupos = _grupos_de_la_cartera()
    unificado = chatbot_tablas_admin.unificar_grupos(
        grupos, CANONICAS, {0: ["Razón Social", "N° Cliente"]}
    )
    assert unificado["filas"] == []
    assert unificado["grupos_descartados"]


def test_la_tabla_unificada_encuentra_clientes_de_cualquier_documento():
    """La prueba que importa: una sola tabla responde por los clientes de todos los
    gestores, y el resumen sabe qué gestores hay — dos cosas imposibles con una
    tabla por gestor."""
    grupos = _grupos_de_la_cartera()
    mapeos = {
        0: CANONICAS, 1: CANONICAS,
        2: ["N° Cliente", "SubCta", "Cl2", "Razón Social", "Cartera", "Gestor de Cobranzas"],
        3: ["Cartera", "Gestor de Cobranzas", "Cl2", "N° Cliente", "SubCta", "Razón Social"],
    }
    unificado = chatbot_tablas_admin.unificar_grupos(grupos, CANONICAS, mapeos)
    tabla = _tabla(
        "Cartera de cobranzas", "Qué gestor tiene asignado cada cliente.",
        CANONICAS, claves=["Razón Social", "N° Cliente", "Cl2"],
        filas_datos=unificado["filas"], modo="lookup", terminos=["gestor", "cartera"],
    )

    def gestor(consulta):
        return tabla.buscar(consulta)[0].datos["Gestor de Cobranzas"]

    assert gestor("gestor de FERROVIAS DEL ESTE") == "LAURA FERNANDEZ"
    assert gestor("cliente 1824.23") == "SOLANGIE BENITEZ"
    assert gestor("LABORATORIOS ANDINOS") == "CARLA ORTIZ"

    resumen = tabla.resumen()
    for gestor in ("DANIEL CORREA", "CARLA ORTIZ", "LAURA FERNANDEZ", "SOLANGIE BENITEZ"):
        assert gestor in resumen


# ====================================== validación de una fila editada a mano

def test_una_fila_nueva_se_recorta_a_las_columnas_declaradas(bases):
    """Una columna que no está en el esquema nunca se le muestra al modelo (el
    prompt arma las columnas desde el esquema): guardarla sería guardar un dato
    invisible."""
    meta = {"columnas": [c.nombre for c in bases.columnas], "claves": ["Base"]}
    limpia = chatbot_tablas_admin._validar_fila(
        {"Base": "Autocentro Nueva", "Zona": "Norte", "Inventada": "x"}, meta)
    assert "Inventada" not in limpia
    assert limpia["Base"] == "Autocentro Nueva"
    # Las columnas declaradas que no vinieron quedan vacías, no ausentes.
    assert limpia["Dirección"] == ""


def test_una_fila_sin_clave_se_rechaza(bases):
    """Sin valor en ninguna columna clave, el chatbot nunca podría encontrarla: es
    una fila que ocupa lugar y no responde nada."""
    meta = {"columnas": [c.nombre for c in bases.columnas], "claves": ["Base"]}
    with pytest.raises(ValueError, match="nunca podría encontrarla"):
        chatbot_tablas_admin._validar_fila({"Zona": "Norte"}, meta)


# ================================ actualizar una tabla con material nuevo (diff)

def _diff(existentes, entrantes, modo, claves=("Cl2",), columnas=None,
          editadas=(), monkeypatch=None):
    """Corre diff_contra_tabla con la BD fingida: el diff es lógica pura sobre las
    filas, y lo que importa probar es esa lógica, no el SELECT."""
    columnas = list(columnas or ["Razón Social", "Cl2", "Gestor de Cobranzas"])
    filas_bd = [
        {"id": i + 1, "datos": __import__("json").dumps(f),
         "editada_at": "x" if i in editadas else None}
        for i, f in enumerate(existentes)
    ]

    class _Conn:
        def execute(self, *a, **k):
            class _R:
                def mappings(self_inner):
                    return self_inner
                def all(self_inner):
                    return filas_bd
            return _R()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(chatbot_tablas_admin, "_get_engine",
                        lambda: type("E", (), {"connect": lambda self: _Conn()})())
    monkeypatch.setattr(chatbot_tablas_admin, "_tabla_para_escribir",
                        lambda conn, cid, tid: {"columnas": columnas, "claves": list(claves)})
    return chatbot_tablas_admin.diff_contra_tabla(1, 1, entrantes, modo)


BASE = [
    {"Razón Social": "ALFA - RED SA", "Cl2": "33815.1", "Gestor de Cobranzas": "DANIEL CORREA"},
    {"Razón Social": "CONEXIONES DEL PLATA SA", "Cl2": "29075.2", "Gestor de Cobranzas": "DANIEL CORREA"},
]


def test_el_diff_separa_altas_cambios_y_sin_cambios(monkeypatch):
    entrantes = [
        dict(BASE[0]),                                                   # igual
        {**BASE[1], "Gestor de Cobranzas": "LAURA FERNANDEZ"},              # cambió de gestor
        {"Razón Social": "NUEVA SA", "Cl2": "40000.1",
         "Gestor de Cobranzas": "LAURA FERNANDEZ"},                         # cliente nuevo
    ]
    d = _diff(BASE, entrantes, "incremental", monkeypatch=monkeypatch)
    assert d["sin_cambios"] == 1
    assert [a["Cl2"] for a in d["altas"]] == ["40000.1"]
    assert len(d["cambios"]) == 1
    assert d["cambios"][0]["columnas"] == ["Gestor de Cobranzas"]
    assert d["cambios"][0]["antes"]["Gestor de Cobranzas"] == "DANIEL CORREA"


def test_incremental_nunca_da_de_baja(monkeypatch):
    """El material trae UN cliente y la tabla tiene dos: en incremental el que no
    vino no se toca. Es la diferencia que evita vaciar una cartera con un archivo
    parcial."""
    d = _diff(BASE, [dict(BASE[0])], "incremental", monkeypatch=monkeypatch)
    assert d["bajas"] == []


def test_reemplazo_da_de_baja_lo_que_no_vino(monkeypatch):
    d = _diff(BASE, [dict(BASE[0])], "reemplazo", monkeypatch=monkeypatch)
    assert [b["clave"] for b in d["bajas"]] == ["29075.2"]


def test_un_cambio_que_pisa_una_correccion_a_mano_queda_marcado(monkeypatch):
    """El conflicto real: un analista corrigió el gestor y la planilla vuelve con
    el dato viejo. Se muestra, no se resuelve solo."""
    entrantes = [{**BASE[0], "Gestor de Cobranzas": "DATO VIEJO"}]
    d = _diff(BASE, entrantes, "incremental", editadas=(0,), monkeypatch=monkeypatch)
    assert d["cambios"][0]["editada_a_mano"] is True
    assert any("corrigió a mano" in n for n in d["notas"])


def test_los_espacios_de_mas_no_generan_cambios(monkeypatch):
    """Sin esto, una planilla con la misma información da miles de cambios y nadie
    revisa nada."""
    entrantes = [{"Razón Social": "  ALFA - RED SA ", "Cl2": "33815.1  ",
                  "Gestor de Cobranzas": "DANIEL CORREA"}]
    d = _diff(BASE, entrantes, "incremental", monkeypatch=monkeypatch)
    assert d["cambios"] == []
    assert d["sin_cambios"] == 1


def test_el_material_repetido_no_duplica_filas(monkeypatch):
    entrantes = [dict(BASE[0]), dict(BASE[0])]
    d = _diff(BASE, entrantes, "incremental", monkeypatch=monkeypatch)
    assert d["altas"] == [] and d["cambios"] == []
    assert any("repetidas" in n for n in d["notas"])


# ------------------------------------------- lectura del material (sin modelo)

def test_lee_una_tabla_pegada_como_texto():
    grupos = chatbot_tablas_admin.grupos_desde_fuentes([
        {"tipo": "texto", "texto": MD_DOC_68},
    ])
    assert len(grupos) == 1
    assert grupos[0]["filas_crudas"][0][0] == "4 A SRL"


def test_un_pdf_no_se_intenta_leer_como_grilla():
    """Reconstruir una grilla desde un PDF con un modelo es justo el paso que
    rompió los datos de la cartera: para actualizar hace falta la planilla."""
    assert chatbot_tablas_admin.grupos_desde_fuentes([
        {"tipo": "archivo", "mime": "application/pdf", "nombre": "cartera.pdf",
         "datos": b"%PDF-1.4 ..."},
    ]) == []


def test_los_enteros_de_una_planilla_no_llegan_como_decimales():
    """1824.0 no matchea con 1824 y la fila entraría como alta en vez de cambio:
    la cartera se duplicaría entera en cada carga."""
    grupo = chatbot_tablas_admin._grupo_desde_filas(
        ["Cliente", "Razón Social"], [[1824.0, "AUTOMOTORES"], [1900.5, "OTRA"]])
    assert grupo["filas_crudas"][0][0] == "1824"
    assert grupo["filas_crudas"][1][0] == "1900.5"


# ============================ guarda contra omisiones (listado escrito en prosa)
#
# Es el único camino donde el modelo REESCRIBE las filas (no hay grilla que
# parsear), así que es el único que puede perder entidades. Pasó de verdad con las
# bases de vantix: 28 bloques en el material, 17 filas en la tabla, y las bases
# terceras desaparecieron sin que nada avisara.

def _bases_en_prosa(cantidad: int) -> str:
    """Material con la forma real del documento de vantix: un bloque por base."""
    bloques = []
    for i in range(cantidad):
        bloques.append(
            f"Base {i}\n"
            f"Dirección: Calle {i} 100\n"
            f"Entrecalles: A y B\n"
            f"Vehículos aptos: Autos y motos\n"
        )
    return "\n".join(bloques)


def test_estima_cuantas_entidades_tiene_un_listado_en_prosa():
    esperadas, etiqueta = chatbot_tablas_admin.entidades_estimadas(_bases_en_prosa(28))
    assert esperadas == 28
    assert etiqueta in ("direccion", "entrecalles", "vehiculos aptos")


def test_un_procedimiento_no_estima_entidades():
    """La guarda no puede dispararse sobre un documento que no es un listado."""
    md = "# Cambio de domicilio\n\n## Procedimiento\nImportante: validar identidad.\n"
    assert chatbot_tablas_admin.entidades_estimadas(md) == (0, "")


def _analizar_prosa(monkeypatch, material: str, filas_que_devuelve: int):
    """Corre analizar_tabla por el camino de prosa con el modelo stubeado."""
    from AuditorIA import asistente_docs

    cuerpo = "\n".join(f"| Base {i} | Calle {i} 100 |" for i in range(filas_que_devuelve))
    respuesta = {
        "es_tabla": True,
        "motivo": "Es un listado de bases.",
        "nombre": "Bases",
        "descripcion": "Dónde se instala.",
        "terminos": ["base"],
        "columnas": [{"nombre": "Base / Taller", "clave": True},
                     {"nombre": "Dirección", "clave": False}],
        "tabla_markdown": f"| Base / Taller | Dirección |\n| --- | --- |\n{cuerpo}",
    }
    monkeypatch.setattr(asistente_docs, "_generar_json", lambda *a, **k: respuesta)
    return asistente_docs.analizar_tabla([{"titulo": "Bases", "markdown": material}])


def test_avisa_cuando_la_tabla_quedo_con_menos_filas_que_el_material(monkeypatch):
    """El caso real de vantix, reproducido: 28 bloques -> 17 filas."""
    propuesta = _analizar_prosa(monkeypatch, _bases_en_prosa(28), filas_que_devuelve=17)
    assert propuesta["es_tabla"] is True
    assert propuesta["filas_total"] == 17
    aviso = [n for n in propuesta["notas"] if "28" in n and "17" in n]
    assert aviso, f"no avisó de la pérdida: {propuesta['notas']}"
    assert "prosa" in aviso[0]


def test_no_avisa_cuando_la_tabla_trae_todo(monkeypatch):
    """La guarda tiene que ser silenciosa cuando no hay nada que reportar: un aviso
    que sale siempre deja de leerse."""
    propuesta = _analizar_prosa(monkeypatch, _bases_en_prosa(12), filas_que_devuelve=12)
    assert not [n for n in propuesta["notas"] if "entidades" in n]


def test_tolera_una_diferencia_chica(monkeypatch):
    """El conteo por etiqueta es aproximado (un bloque puede repetirla, o no
    tenerla): una fila de diferencia no puede disparar la alarma."""
    propuesta = _analizar_prosa(monkeypatch, _bases_en_prosa(20), filas_que_devuelve=19)
    assert not [n for n in propuesta["notas"] if "entidades" in n]
