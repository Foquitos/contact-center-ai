"""Crea la base de demostración `Acme` en el SQL Server de db/docker-compose.yml.

Pasos: crea la base, aplica el esquema (db/esquema/, un archivo por objeto), las migraciones
(scripts/migrations/, en orden) y carga los datos ficticios (db/datos_demo.py).

    python db/crear_base_demo.py            # crea o completa la base (todo es idempotente)
    python db/crear_base_demo.py --reset    # la borra y la arma de cero

Conexión: DEMO_SQL_SERVER (127.0.0.1), DEMO_SQL_PORT (1433), DEMO_SA_PASSWORD (la del compose).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pyodbc

RAIZ = Path(__file__).resolve().parent.parent
ESQUEMA = RAIZ / "db" / "esquema"
MIGRACIONES = RAIZ / "scripts" / "migrations"
BASE = "Acme"

# Orden de creación por tipo de objeto (el sufijo del archivo lo dice).
ORDEN_TIPOS = ["Schema", "Table", "UserDefinedFunction", "View", "StoredProcedure"]

# Migraciones que no se aplican en la base de demostración, con el motivo.
MIGRACIONES_OMITIDAS: dict[str, str] = {
    "2026-06-20_vantix_orion_backfill_duracion.sql": "backfill desde el linked server ORION_LINK",
    "2026-06-20_vantix_orion_backfill_extras_comentario.sql": "backfill desde el linked server ORION_LINK",
    "2026-06-20_vantix_orion_backfill_tipificacion.sql": "backfill desde el linked server ORION_LINK",
    "2026-07-22_vantix_motivo_finalizacion_backfill.sql": "backfill desde el linked server ORION_LINK",
    "2026-07-27_vista_llamadas_rebotadas_agente.sql": "vista sobre el linked server MYSQL_LINK",
    "2026-08-18b_incidencias_auditoria.sql": "ya incluida en el esquema de partida (reescribe un SP que ya tiene el cambio)",
    "2026-09-02c_vantix_tabla_claves_y_terminos.sql": "depende de una migración con datos de contacto que no se publica",
}

# SQL que corre justo antes de una migración, para dejar la base como la esperaba en producción.
ANTES_DE_MIGRACION: dict[str, str] = {
    # 2026-07-08 recrea ChatbotDocs (en el esquema de partida ya no existe) y le carga los docs de Drive;
    # en producción esa tabla ya estaba vacía cuando se la dio de baja.
    "2026-07-30d_baja_chatbot_docs_google.sql":
        "IF OBJECT_ID('pagina_web.ChatbotDocs', 'U') IS NOT NULL DELETE FROM pagina_web.ChatbotDocs",
}

_GO = re.compile(r"^\s*GO\s*;?\s*$", re.IGNORECASE | re.MULTILINE)


def conectar(base: str = "master") -> pyodbc.Connection:
    servidor = os.getenv("DEMO_SQL_SERVER", "127.0.0.1")
    puerto = os.getenv("DEMO_SQL_PORT", "1433")
    clave = os.getenv("DEMO_SA_PASSWORD", "Demo_ContactCenter_2026")
    cadena = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={servidor},{puerto};DATABASE={base};UID=sa;PWD={clave};"
        "TrustServerCertificate=yes;Encrypt=yes"
    )
    ultimo_error: Exception | None = None
    for _ in range(30):  # el contenedor tarda unos segundos en aceptar conexiones
        try:
            return pyodbc.connect(cadena, autocommit=True, timeout=5)
        except pyodbc.Error as e:
            ultimo_error = e
            time.sleep(2)
    raise SystemExit(f"No se pudo conectar a SQL Server en {servidor}:{puerto}: {ultimo_error}")


def lotes(sql: str) -> list[str]:
    """Separa en lotes por las líneas GO que no estén dentro de un comentario /* ... */ ni de un string."""
    resultado, actual = [], []
    en_comentario = en_string = en_corchete = False
    for linea in sql.splitlines():
        if not en_comentario and not en_string and _GO.fullmatch(linea):
            resultado.append("\n".join(actual))
            actual = []
            continue
        actual.append(linea)
        i = 0
        while i < len(linea):
            par = linea[i:i + 2]
            if en_comentario:
                if par == "*/":
                    en_comentario, i = False, i + 1
            elif en_string:
                if linea[i] == "'":
                    en_string = False  # '' (comilla escapada) cierra y reabre: da lo mismo
            elif en_corchete:
                if linea[i] == "]":
                    en_corchete = False
            elif par == "--":
                break
            elif par == "/*":
                en_comentario, i = True, i + 1
            elif linea[i] == "'":
                en_string = True
            elif linea[i] == "[":
                en_corchete = True
            i += 1
    resultado.append("\n".join(actual))
    return [b for b in resultado if b.strip()]


def ejecutar_archivo(cn: pyodbc.Connection, archivo: Path) -> str | None:
    """Ejecuta los lotes del archivo. Devuelve el primer error o None."""
    cur = cn.cursor()
    for lote in lotes(archivo.read_text(encoding="utf-8-sig")):
        try:
            cur.execute(lote)
            while cur.nextset():  # consumir los result sets de los PRINT/SELECT de control
                pass
        except pyodbc.Error as e:
            return str(e).split("(SQLExecDirectW)")[0][-400:]
    return None


def crear_base(reset: bool) -> None:
    cn = conectar()
    cur = cn.cursor()
    if reset:
        cur.execute(f"IF DB_ID('{BASE}') IS NOT NULL BEGIN "
                    f"ALTER DATABASE [{BASE}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{BASE}]; END")
    cur.execute(f"IF DB_ID('{BASE}') IS NULL CREATE DATABASE [{BASE}] COLLATE Modern_Spanish_CI_AS")
    cur.execute(f"ALTER DATABASE [{BASE}] SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE")
    cn.close()


def aplicar_esquema(cn: pyodbc.Connection) -> None:
    archivos = sorted(ESQUEMA.glob("*.sql"),
                      key=lambda p: (ORDEN_TIPOS.index(p.name.rsplit(".", 2)[-2]), p.name))
    # Unidad de trabajo = un lote de un archivo. Las vistas y los procedimientos pueden depender de
    # otros objetos: los lotes que fallan se reintentan (solo ellos: los ALTER TABLE ... ADD FOREIGN KEY
    # sin nombre no son idempotentes) hasta que una pasada no avance.
    pendientes = [(a, i, lote) for a in archivos
                  for i, lote in enumerate(lotes(a.read_text(encoding="utf-8-sig")))]
    total = len(pendientes)
    errores: dict[tuple[str, int], str] = {}
    cur = cn.cursor()
    while pendientes:
        fallaron = []
        for archivo, i, lote in pendientes:
            try:
                cur.execute(lote)
                while cur.nextset():
                    pass
            except pyodbc.Error as e:
                fallaron.append((archivo, i, lote))
                errores[(archivo.name, i)] = str(e).split("(SQLExecDirectW)")[0][-400:]
        if len(fallaron) == len(pendientes):
            break
        pendientes = fallaron
    print(f"Esquema: {len(archivos)} objetos, {total - len(pendientes)} de {total} lotes aplicados.")
    for archivo, i, _ in pendientes:
        print(f"  ERROR {archivo.name} (lote {i + 1}): {errores[(archivo.name, i)]}")
    if pendientes:
        raise SystemExit("El esquema no se pudo crear completo.")


def aplicar_migraciones(cn: pyodbc.Connection) -> None:
    fallidas = []
    archivos = sorted(MIGRACIONES.glob("*.sql"))
    for archivo in archivos:
        if archivo.name in MIGRACIONES_OMITIDAS:
            continue
        if archivo.name in ANTES_DE_MIGRACION:
            cn.cursor().execute(ANTES_DE_MIGRACION[archivo.name])
        error = ejecutar_archivo(cn, archivo)
        if error:
            fallidas.append((archivo.name, error))
    print(f"Migraciones: {len(archivos) - len(fallidas) - len(MIGRACIONES_OMITIDAS)} aplicadas, "
          f"{len(MIGRACIONES_OMITIDAS)} omitidas (ver MIGRACIONES_OMITIDAS).")
    for nombre, error in fallidas:
        print(f"  ERROR {nombre}: {error}")
    if fallidas:
        raise SystemExit("Hubo migraciones con error.")


def recalcular_planificador() -> None:
    """Primera corrida del planificador para Gasur, con el mismo script que corre el cron.

    El backend lee la conexión del .env; acá se la pasa por variable de entorno (que manda sobre el
    .env) para que apunte a esta base aunque el .env todavía no exista.
    """
    servidor = os.getenv("DEMO_SQL_SERVER", "127.0.0.1")
    puerto = os.getenv("DEMO_SQL_PORT", "1433")
    clave = os.getenv("DEMO_SA_PASSWORD", "Demo_ContactCenter_2026")
    conexion = (f"mssql+pyodbc://sa:{clave}@{servidor}:{puerto}/{BASE}"
                "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes")
    entorno = dict(os.environ, CONNECTION_STRING=conexion, CONNECTION_STRING_chatbot=conexion)
    # Las variables obligatorias de config.py, por si no hay .env: el recálculo no las usa.
    for obligatoria in ("SECRET_KEY", "GEMINI_AUDITORIA_API_KEY", "GEMINI_CHATBOT_API_KEY", "DEEPGRAM_API_KEY",
                        "ANTHROPIC_API_KEY", "OPENROUTESERVICE_API_KEY", "VERINT_USER", "VERINT_PASS", "CXONE_USER",
                        "CXONE_PASS", "CXONE_TOPC", "YOIZEN_USER", "YOIZEN_PASS", "MITROL_USER", "MITROL_PASS",
                        "HERMES_USER", "HERMES_PASS", "CYT_USER", "CYT_PASS", "ASTERVOIP_USER", "ASTERVOIP_PASS",
                        "RRHH_MAIL_PASS", "ENVIOS_OPERACIONES_PASS", "CALIDAD1_PASS"):
        entorno.setdefault(obligatoria, "SIN_CONFIGURAR")
    script = RAIZ / "scripts" / "planificador_recalcular_diario.py"
    r = subprocess.run([sys.executable, str(script), "--campana", "30", "--dias", "30"], env=entorno,
                       capture_output=True, text=True)
    if r.returncode == 0:
        print("Planificador: primera corrida de Gasur calculada.")
    else:
        print("Planificador: no se pudo calcular la primera corrida (se puede hacer desde la pantalla con "
              "\"Recalcular\"):\n" + (r.stderr or r.stdout)[-1500:])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="borra la base y la arma de cero")
    parser.add_argument("--sin-datos", action="store_true", help="solo esquema y migraciones")
    args = parser.parse_args()

    crear_base(args.reset)
    cn = conectar(BASE)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import datos_demo
    aplicar_esquema(cn)
    datos_demo.cargar_catalogos(cn)
    aplicar_migraciones(cn)
    if not args.sin_datos:
        datos_demo.cargar(cn)
    cn.close()
    if not args.sin_datos:
        recalcular_planificador()
    print(f"Base {BASE} lista. Usuario 11111111, clave {datos_demo.CLAVE_DEMO} (ver db/README.md).")


if __name__ == "__main__":
    main()
