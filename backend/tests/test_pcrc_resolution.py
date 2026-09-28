"""Tests de la resolución PCRC -> chatbot del grupo CSV.

- normalizar_pcrc / slugify_*: puros, offline.
- Data-layer (BD real): el mapeo pagina_web.ChatbotPcrc debe cubrir todos los
  PCRC operativos. Se saltea si la migración 2026-07-08_chatbots_en_bd.sql
  todavía no corrió en la BD.
"""
import pytest
from sqlalchemy import text

from app.chatbot_config import normalizar_pcrc, slugify_campana, slugify_nombre, SLUG_REGEX


# ------------------------------------------------------------------ puros

@pytest.mark.parametrize("crudo,esperado", [
    ("NO PREMIUM", "NO PREMIUM"),
    ("no premium", "NO PREMIUM"),
    ("  Tokenización  ", "TOKENIZACION"),          # acentos + espacios
    ("AT.   EJECUTIVOS   VIP", "AT. EJECUTIVOS VIP"),  # espacios colapsados
    ("", ""),
    (None, ""),
])
def test_normalizar_pcrc(crudo, esperado):
    assert normalizar_pcrc(crudo) == esperado


@pytest.mark.parametrize("crudo,esperado", [
    ("csv no premium", "csv_no_premium"),
    ("CSV Premium", "csv_premium"),
    ("  voltara  ", "voltara"),
    ("csv_vip", "csv_vip"),                        # un slug ya normalizado no cambia
])
def test_slugify_campana(crudo, esperado):
    assert slugify_campana(crudo) == esperado


@pytest.mark.parametrize("nombre,esperado", [
    ("CSV Isla de Productos", "csv_isla_de_productos"),
    ("Tokenización", "tokenizacion"),
    ("  Paygo 2.0  ", "paygo_2_0"),
])
def test_slugify_nombre(nombre, esperado):
    slug = slugify_nombre(nombre)
    assert slug == esperado
    assert SLUG_REGEX.match(slug)


# ------------------------------------------------------------------ data-layer

def _tabla_existe(engine, tabla: str) -> bool:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT OBJECT_ID(:t, 'U')"), {"t": tabla}
        ).scalar() is not None


@pytest.fixture()
def _requiere_migracion(engine):
    if not _tabla_existe(engine, "pagina_web.ChatbotPcrc"):
        pytest.skip("La migración 2026-07-08_chatbots_en_bd.sql no corrió en esta BD.")


def test_seed_pcrc_completo(engine, _requiere_migracion):
    """Los 13 PCRC del dict legacy PCRC_A_CAMPANA deben estar mapeados a un bot
    activo del grupo CSV."""
    esperados = {
        "AT. EJECUTIVOS VIP": "csv_vip",
        "BANCENTRO-BSF": "csv_bancentro",
        "COMMERCIAL CARDS": "csv_commercial",
        "DENUNCIAS": "csv_denuncias",
        "MARCAS": "csv_isla_de_productos",
        "NO PREMIUM": "csv_no_premium",
        "NO PREMIUM S2S": "csv_no_premium",
        "PREMIUM": "csv_premium",
        "PREMIUM S2S": "csv_premium",
        "PTO A PTO AUSTRAL": "csv_pto_a_pto",
        "RECLAMOS NO PREMIUM": "csv_no_premium",
        "RECLAMOS PREMIUM": "csv_premium",
        "TOKENIZACION": "csv_tokenizacion",
    }
    query = text("""
        SELECT m.pcrc_normalizado, c.slug, c.activo, c.grupo
        FROM pagina_web.ChatbotPcrc m
        JOIN pagina_web.Chatbots c ON c.id = m.chatbot_id
    """)
    with engine.connect() as conn:
        filas = {r.pcrc_normalizado: r for r in conn.execute(query)}

    faltantes = set(esperados) - set(filas)
    assert not faltantes, f"PCRCs sin mapear: {faltantes}"
    for pcrc, slug in esperados.items():
        assert filas[pcrc].slug == slug, f"{pcrc} mapeado a {filas[pcrc].slug}, se esperaba {slug}"
        assert filas[pcrc].activo, f"{pcrc} apunta al bot inactivo {slug}"
        assert filas[pcrc].grupo == "csv", f"{pcrc} apunta a un bot fuera del grupo CSV"


def test_pcrcs_operativos_tienen_chatbot(engine, _requiere_migracion):
    """Todo PCRC con OPERADORES ACTIVOS debería tener bot asignado (aviso
    temprano de PCRCs nuevos sin mapear). Usa la misma cadena de joins que
    resolver_slug_csv_por_pcrc: un PCRC asignado a skills sin operadores
    vigentes no afecta a nadie y no debe fallar la suite."""
    query = text("""
        SELECT DISTINCT np.PCRC
        FROM [Acme].[dbo].[CSV Historial Skill-PCRC] hsp
        JOIN [CSV Normalizador PCRC] np ON hsp.PCRC_ID = np.id
        JOIN CSV.vw_ausentismo_latest ns ON hsp.Skill_ID = ns.[Skill 4]
        JOIN usuarios u ON TRY_CAST(u.usuario AS INT) = ns.[Identif. de conexión.1]
        JOIN nomina n ON n.id = u.nomina_id
        JOIN operadores o ON o.legajo_id = n.id AND o.estado = 1 AND o.fecha_hasta IS NULL
        WHERE hsp.fecha_hasta IS NULL AND Desconexion IS NULL
    """)
    with engine.connect() as conn:
        vigentes = [r.PCRC for r in conn.execute(query)]
        mapeados = {
            r.pcrc_normalizado
            for r in conn.execute(text("SELECT pcrc_normalizado FROM pagina_web.ChatbotPcrc"))
        }

    sin_mapear = sorted({normalizar_pcrc(p) for p in vigentes} - mapeados)
    assert not sin_mapear, (
        f"PCRCs vigentes sin chatbot asignado: {sin_mapear}. "
        "Asignarlos desde Administración > Chatbots."
    )
