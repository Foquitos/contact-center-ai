"""Configuración de un chatbot leída desde la BD (pagina_web.Chatbots).

Reemplaza a las ~35 variables *_EMBEDDING_STORAGE/_DOCS_FOLDER/_DRIVE_FILE_ID
del .env: todas las rutas se derivan del slug bajo CHATBOT_STORAGE_ROOT y las
colecciones de Qdrant siguen la convención alias `bot_{slug}` -> colección
física `bot_{slug}_v{N}` (el swap de alias hace atómico el reindexado).
"""
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.config import settings

# Convención de slug: minúsculas, dígitos y guión bajo. Los nombres legacy de
# campaña ("csv no premium") se normalizan con slugify_campana().
SLUG_REGEX = re.compile(r"^[a-z0-9_]{2,50}$")

GRUPO_CSV = "csv"


def slugify_campana(valor: str) -> str:
    """Normaliza un nombre de campaña legacy o un slug a la convención de slug
    ("CSV No Premium" -> "csv_no_premium")."""
    return "_".join(valor.strip().lower().split())


def slugify_nombre(nombre: str) -> str:
    """Genera un slug desde un nombre libre ("CSV Isla de Productos" -> "csv_isla_de_productos")."""
    sin_acentos = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", sin_acentos.lower()).strip("_")


def normalizar_pcrc(valor: Optional[str]) -> str:
    """Normaliza un PCRC para comparar contra pagina_web.ChatbotPcrc: sin acentos,
    mayúsculas y espacios colapsados."""
    if not valor:
        return ""
    sin_acentos = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode("ascii")
    return " ".join(sin_acentos.upper().split())


@dataclass(frozen=True)
class ChatbotConfig:
    id: int
    slug: str
    nombre: str
    descripcion: Optional[str]
    system_prompt: str
    grupo: Optional[str]
    permission_code: str
    activo: bool
    index_version: int
    index_status: Optional[str]
    last_indexed_at: Optional[datetime]
    updated_at: Optional[datetime]
    # Temperatura del LLM para ESTE bot. None => se usa DEFAULT_LLM_TEMP_REMOTE.
    # Un bot de procedimientos quiere temperatura baja (repetir el manual, no
    # reformularlo); uno que redacta cartas necesita margen para adaptar el texto.
    temperatura: Optional[float] = None
    # ¿Este bot acepta capturas/PDF adjuntos? Es una decisión POR BOT y no global:
    # un bot de procedimientos puros no gana nada con una imagen, y cada archivo que
    # se le manda a Gemini es contenido de un cliente saliendo de la empresa. Por eso
    # el default es False y se prende de a uno (ver migración 2026-08-12).
    permite_adjuntos: bool = False

    @classmethod
    def from_row(cls, row) -> "ChatbotConfig":
        """Construye desde un row-mapping del SELECT de Chatbots JOIN Permissions."""
        return cls(
            id=row["id"],
            slug=row["slug"],
            nombre=row["nombre"],
            descripcion=row["descripcion"],
            system_prompt=row["system_prompt"],
            grupo=row["grupo"],
            permission_code=row["permission_code"],
            activo=bool(row["activo"]),
            index_version=row["index_version"],
            index_status=row["index_status"],
            last_indexed_at=row["last_indexed_at"],
            updated_at=row["updated_at"],
            # Las consultas que no traen la columna (indexador, scripts) caen al default.
            temperatura=row["temperatura"] if "temperatura" in row.keys() else None,
            permite_adjuntos=(
                bool(row["permite_adjuntos"]) if "permite_adjuntos" in row.keys() else False
            ),
        )

    @property
    def es_csv(self) -> bool:
        return self.grupo == GRUPO_CSV

    @property
    def storage_dir(self) -> str:
        return os.path.join(settings.CHATBOT_STORAGE_ROOT, self.slug)

    @property
    def docs_dir(self) -> str:
        return os.path.join(self.storage_dir, "docs")

    @property
    def log_dir(self) -> str:
        return os.path.join(self.storage_dir, "logs")

    def persist_dir(self, version: int) -> str:
        """Directorio del docstore/index_store de LlamaIndex para una versión del índice."""
        return os.path.join(self.storage_dir, f"v{version}")

    @property
    def collection_alias(self) -> str:
        """Alias estable de Qdrant al que apuntan los workers."""
        return f"bot_{self.slug}"

    def collection_fisica(self, version: int) -> str:
        """Colección física versionada; el alias se swapea a esta al reindexar."""
        return f"bot_{self.slug}_v{version}"

    @property
    def cache_collection(self) -> str:
        return f"cache_{self.slug}"


# ------------------------------------------- nombre de archivo -> título real

# El indexador materializa cada documento como '{orden:02d}_dbdoc_{id}.md'
# (chatbot_indexer._materializar_docs), y ese nombre es el que queda en los
# metadatos de cada nodo. Mostrarlo tal cual ("04_dbdoc_19.md") no le dice nada a
# quien tiene que ir a buscar la documentación, así que se resuelve al título con
# el que se cargó.
_RE_DBDOC = re.compile(r"dbdoc_(\d+)")


def doc_id_de_archivo(nombre_archivo: Optional[str]) -> Optional[int]:
    """id de ChatbotDocMarkdown a partir del nombre materializado, o None si el
    documento viene de otro origen (Drive) y no sigue la convención."""
    if not nombre_archivo:
        return None
    m = _RE_DBDOC.search(nombre_archivo)
    return int(m.group(1)) if m else None


def titulos_por_archivo(conn, nombres) -> dict:
    """{nombre_archivo: titulo} para los que siguen la convención del indexador.

    Resuelve todos en UNA consulta: el detalle de una solicitud puede traer varias
    fuentes y no tiene sentido ir a la base por cada una.
    """
    from sqlalchemy import bindparam, text

    ids = {doc_id_de_archivo(n): n for n in (nombres or []) if doc_id_de_archivo(n)}
    if not ids:
        return {}

    filas = conn.execute(
        text("SELECT id, titulo FROM pagina_web.ChatbotDocMarkdown WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": list(ids.keys())},
    ).mappings().all()

    por_id = {f["id"]: f["titulo"] for f in filas if f["titulo"]}
    return {archivo: por_id[doc_id] for doc_id, archivo in ids.items() if doc_id in por_id}
