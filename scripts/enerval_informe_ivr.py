"""
Informe IVR de Enerval — descarga sin navegador y carga a SQL Server.

QUÉ HACE
--------
Baja el informe IVR de la Consola LATAM de Enerval y lo carga en
dbo.[Voltara Enerval informe IVR] (ver scripts/migrations/2026-09-04_enerval_informe_ivr_historico.sql).

Tres modos:

    diario     — actualiza hoy (y ayer si es de madrugada). Reemplaza el día
                 entero, así que las correcciones que hace el portal sobre
                 llamadas ya bajadas entran solas.
    historico  — recorre un rango largo día por día. Es reanudable: anota cada
                 día en dbo.[Voltara Enerval informe IVR cargas] y, si se corta,
                 la corrida siguiente sigue donde quedó.
    estado     — muestra qué días están cargados y cuáles faltan.

POR QUÉ NO USA SELENIUM
-----------------------
El portal es JSF/ICEfaces, pero abajo del JavaScript hay tres pedidos HTTP y
nada más. Medido contra el portal el 2026-09-04:

    login (POST + redirect a Spring Security) .... 1 a 8 s, una sola vez por corrida
    generar el informe de un día (POST ajax) ..... 1 a 45 s según cuánto tarde el portal
    bajar el CSV (GET a /FileDownload/...) ....... 2 a 6 s

Con Selenium cada corrida arrancaba un Chrome (~10-15 s), y el script viejo
levantaba un Chrome nuevo por cada reintento. Además Chrome no hace falta en el
server: esto corre con `requests` y listo.

El detalle que hacía falta descubrir: el botón "Iniciar sesión" no postea las
credenciales a Spring Security. El bean de JSF las valida y responde un
<partial-response><redirect url="/ConsolaLATAM/j_spring_security_check?j_username=...&j_password=Habilitado">;
recién al seguir ESE redirect se crea la sesión autenticada. Sin seguirlo, el
POST parece exitoso y todo lo demás rebota al login.

POR QUÉ VA DÍA POR DÍA
----------------------
Lo que cuesta es la cantidad de filas, no la cantidad de pedidos: medido con la
cuenta que ve todas las BPO, un día de 41.562 filas tardó 33,5 s y un rango de
5 días con 100.364 filas tardó 83,7 s — 1.240 y 1.199 filas por segundo. Agrupar
días no acelera nada. Y estirar el rango encima rompe: 31 días no vuelven nunca
(corta por timeout a los 4 minutos).

Como agrupar no gana tiempo, gana lo otro: con un día por pedido, cada día que
entra queda commiteado por su cuenta y una corrida cortada no pierde nada. La
unidad de carga es el día también en SQL: se borra el día y se inserta de nuevo
entero, dentro de una transacción.

DÍAS SIN DATOS
--------------
El portal no avisa cuándo un rango no tiene llamadas: arma igual el diálogo de
descarga con un nombre de archivo inventado, y el archivo devuelve 404. Esos
días quedan anotados como VACIO. Si alguna vez un 404 tapa una falla real del
portal, `historico --rehacer-vacios` los vuelve a pedir.

UNA SOLA SESIÓN POR CUENTA
--------------------------
El portal admite una sesión simultánea por usuario: no se puede paralelizar con
la misma cuenta, y si quedó una sesión colgada de una corrida anterior este
script la cierra al entrar (botón "Cerrar otras sesiones"). Por eso también
hace logout hasta cuando falla.

ALCANCE DE LA CUENTA (importante)
---------------------------------
Hay cuentas que ven solo las llamadas de la propia BPO y cuentas que ven todas.
Como la carga reemplaza el día completo, correr esto con una cuenta acotada y
--alcance completo borraría del histórico las filas de las otras BPO. Por eso:

    --alcance completo  (default) el día se reemplaza entero. Usar con la cuenta
                        que ve todas las BPO.
    --alcance propio    se reemplazan únicamente las BPO que vinieron en el CSV.
                        Usar con una cuenta acotada.

El alcance queda anotado por día, y el "saltear lo ya cargado" lo tiene en
cuenta: un día bajado con una cuenta acotada se vuelve a pedir cuando la
corrida es --alcance completo. Al revés no, porque no agregaría nada.

Requisitos:
    pip install requests sqlalchemy pyodbc python-dotenv

Variables de entorno (.env junto al script, en scripts/../.env o en el ambiente):
    USER_ENERVAL          usuario del portal de Enerval
    PASS_ENERVAL          contraseña del portal de Enerval
    CONNECTION_STRING  cadena SQLAlchemy a la base Acme

CORRIDA DE MADRUGADA
--------------------
La cuenta del portal la usan personas durante el día y admite una sola sesión,
así que el backfill se hace de noche y tiene que devolver la cuenta antes de que
arranque la operación: `--hasta-las 06:00` corta entre días, cierra la sesión y
termina. Como el avance queda anotado día por día, la noche siguiente se corre
el MISMO comando y sigue donde quedó. Son ~1.340 días a 10-30 s cada uno: una o
dos madrugadas.

Ejemplos:
    python enerval_informe_ivr.py diario
    python enerval_informe_ivr.py historico --desde 2023-01-01 --hasta 2026-09-03
    python enerval_informe_ivr.py historico --desde 2023-01-01 --hasta-las 06:00
    python enerval_informe_ivr.py historico --desde 2023-01-01 --solo-csv --csv-dir D:/ivr
    python enerval_informe_ivr.py estado
"""

from __future__ import annotations

import argparse
import csv
import datetime
import html
import io
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


# ==========================================
# CONFIGURACIÓN
# ==========================================

BASE = 'http://informes.enerval.example:8080'
CTX = f'{BASE}/ConsolaLATAM'
URL_LOGIN = f'{CTX}/springSecurityLogin.jsf'
URL_WELCOME = f'{CTX}/secured/welcome.jsf'
URL_SESIONES = f'{CTX}/secured/multipleSessionPage.jsf'
URL_LOGOUT = f'{CTX}/j_spring_security_logout'
URL_IVR = f'{CTX}/secured/reporte/historico/informeIvr.jsf'
QS_IVR = {'c': 'Argentina', 'm': 'L'}

TABLA = 'Voltara Enerval informe IVR'
TABLA_CARGAS = 'Voltara Enerval informe IVR cargas'

# Columnas del CSV, en orden, con el tipo con el que entran a SQL.
# El CSV cierra cada fila con ';' así que trae una columna extra sin nombre: se ignora.
COLUMNAS = [
    ('ConnID', 'txt', 32),
    ('ANI', 'txt', 32),
    ('Fecha de Inicio', 'fecha', None),
    ('IVR', 'txt', 64),
    ('Tiempo IVR', 'int', None),
    ('Resultado IVR', 'txt', 64),
    ('Tiempo (IVR-Total)', 'int', None),
    ('Línea telefónica', 'txt', 32),
    ('Tiempo Total', 'int', None),
    ('Numero de caso', 'txt', 32),
    ('Documento', 'txt', 32),
    ('Tipo de Documento', 'txt', 32),
    ('Suministro', 'txt', 32),
    ('Cola', 'txt', 64),
    ('Duración Cola', 'int', None),
    ('Skill', 'txt', 64),
    ('BPO', 'txt', 32),
    ('Nombre de Agente', 'txt', 128),
    ('Agente', 'txt', 32),
    ('Duración Ring', 'int', None),
    ('Duración Talk', 'int', None),
    ('Duración Hold', 'int', None),
    ('Duración ACW', 'int', None),
    ('Duración POS', 'int', None),
    ('Servicio <= 20 segundos', 'txt', 8),
    ('Duración Encuesta', 'int', None),
    ('Puntos de control IVR', 'txt', 4000),
    ('Trazabilidad de Llamadas', 'txt', 64),
    ('Status Servicio', 'txt', 128),
    ('Fecha de Agente', 'fecha', None),
]
NOMBRES = [c[0] for c in COLUMNAS]

# El informe tiene datos desde 2022, pero flacos: 2022-01-17 devolvió 115
# llamadas y 2022-06-15 apenas 234, contra ~9.000 de 2022-12-15. El IVR se fue
# encendiendo durante ese año. Por eso el default arranca en 2023 (que ya viene
# completo) y 2022 queda disponible pidiéndolo con --desde.
PISO_HISTORICO = datetime.date(2022, 1, 1)
DESDE_DEFAULT = datetime.date(2023, 1, 1)

FILAS_POR_LOTE = 5000        # filas por executemany
PAUSA_ENTRE_DIAS = 0.5       # segundos, para no martillar el portal
REINTENTOS_DIA = 3


class ErrorConsola(RuntimeError):
    """Falla del lado del portal (login, generación del informe, descarga)."""


class SesionCaida(ErrorConsola):
    """El portal nos devolvió al login: hay que volver a autenticarse."""


# ==========================================
# CLIENTE HTTP DEL PORTAL
# ==========================================

def _hidden(html_txt: str, nombre: str) -> str | None:
    """Valor de un <input type=hidden> del HTML (ViewState, ice.window, ice.view)."""
    m = re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(nombre), html_txt)
    return html.unescape(m.group(1)) if m else None


def _viewstate_ajax(xml: str) -> str | None:
    """ViewState nuevo que viene dentro de la respuesta parcial de JSF."""
    m = re.search(r'<update id="[^"]*ViewState[^"]*"><!\[CDATA\[(.*?)\]\]></update>', xml, re.S)
    return m.group(1) if m else None


def _texto_plano(xml: str, limite: int = 300) -> str:
    """Saca los tags de una respuesta parcial para poder mostrar el mensaje de error."""
    t = re.sub(r'<[^>]+>', ' ', xml)
    t = t.replace('<![CDATA[', ' ').replace(']]>', ' ')
    return re.sub(r'\s+', ' ', html.unescape(t)).strip()[:limite]


class ConsolaEnerval:
    """Sesión HTTP contra la Consola LATAM, acotada al informe IVR."""

    # 240 s es holgadísimo para un día (medido: 1-3 s de generación, 2 s de
    # descarga) y a la vez corta rápido cuando el portal se queda colgado, que
    # pasa: ahí conviene reintentar antes que esperar diez minutos.
    def __init__(self, usuario: str, clave: str, timeout: int = 240):
        self.usuario = usuario
        self.clave = clave
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })
        self.form: dict | None = None   # contexto del formulario del informe

    # ---------- ciclo de vida ----------

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *exc):
        self.logout()

    def _post_ajax(self, url: str, data, **kw):
        cabeceras = {
            'Faces-Request': 'partial/ajax',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        }
        return self.s.post(url, data=data, headers=cabeceras, timeout=self.timeout, **kw)

    def login(self):
        pagina = self.s.get(URL_LOGIN, timeout=self.timeout).text
        win = _hidden(pagina, 'ice.window')
        view = _hidden(pagina, 'ice.view')
        vs = _hidden(pagina, 'javax.faces.ViewState')
        if not vs:
            raise ErrorConsola('la pantalla de login no trajo ViewState (¿portal caído?)')

        # Los ice.event.* son los que manda el navegador al clickear el botón; el
        # bean de ICEfaces no valida sin ellos.
        datos = [
            ('ice.window', win), ('ice.view', view),
            ('loginForm:username_input', self.usuario),
            ('loginForm:password_input', self.clave),
            ('loginForm_SUBMIT', '1'),
            ('javax.faces.ViewState', vs), ('javax.faces.ClientWindow', win),
            ('com.sun.faces.namingContainerId', ''),
            ('ice.focus', ''), ('ice.event.target', ''),
            ('ice.event.captured', 'loginForm:button_login'),
            ('ice.event.type', 'onclick'),
            ('ice.event.alt', 'false'), ('ice.event.ctrl', 'false'),
            ('ice.event.shift', 'false'), ('ice.event.meta', 'false'),
            ('ice.event.left', 'true'), ('ice.event.right', 'false'),
            ('loginForm:button_login', 'loginForm:button_login'),
            ('javax.faces.source', 'loginForm:button_login'),
            ('javax.faces.partial.ajax', 'true'),
            ('javax.faces.partial.execute', '@all'),
            ('javax.faces.partial.render', '@all'),
            ('loginForm', 'loginForm'),
        ]
        respuesta = self._post_ajax(URL_LOGIN, datos).text

        # El bean valida las credenciales y devuelve el redirect a Spring Security.
        # Sin seguirlo no queda sesión autenticada.
        m = re.search(r'<redirect url="([^"]+)"', respuesta)
        if not m:
            raise ErrorConsola(f'usuario o clave rechazados: {_texto_plano(respuesta)}')
        destino = self.s.get(BASE + html.unescape(m.group(1)), timeout=self.timeout)

        if 'multipleSessionPage' in destino.url or 'notAccessForm' in destino.text:
            self._cerrar_otras_sesiones(destino.text)

        w = self.s.get(URL_WELCOME, timeout=self.timeout, allow_redirects=False)
        if w.status_code != 200:
            raise ErrorConsola(f'login rechazado: welcome.jsf devolvió {w.status_code}')
        self.form = None
        return self

    def _cerrar_otras_sesiones(self, pagina: str):
        """Cierra la sesión que quedó abierta en otro lado (el portal admite una sola)."""
        print('  [portal] había otra sesión abierta con esta cuenta: la cierro')
        self.s.post(URL_SESIONES, timeout=self.timeout, data={
            'ice.window': _hidden(pagina, 'ice.window'),
            'ice.view': _hidden(pagina, 'ice.view'),
            'notAccessForm:realLogoutOtherBtn': '',
            'notAccessForm_SUBMIT': '1',
            'javax.faces.ViewState': _hidden(pagina, 'javax.faces.ViewState'),
            'javax.faces.ClientWindow': _hidden(pagina, 'ice.window'),
        })

    def logout(self) -> bool:
        """Cierra la sesión. Devuelve True si el portal efectivamente la cerró."""
        try:
            self.s.get(URL_LOGOUT, timeout=60)
            w = self.s.get(URL_WELCOME, timeout=60, allow_redirects=False)
            return w.status_code != 200
        except Exception:
            return False

    # ---------- informe IVR ----------

    def abrir_informe(self) -> dict:
        """Carga la pantalla del informe y guarda los ids del formulario.

        Los ids llevan un prefijo JSF autogenerado ('j_id_8:') que puede cambiar
        entre versiones del portal, así que se leen de la página en vez de
        hardcodearse.
        """
        r = self.s.get(URL_IVR, params=QS_IVR, timeout=self.timeout)
        pagina = r.text
        if 'springSecurityLogin' in r.url or 'loginForm:username_input' in pagina:
            raise SesionCaida('la sesión se cayó al abrir el informe')

        m = re.search(r'id="([^"]+):dataInizio_input"', pagina)
        if not m:
            raise ErrorConsola('no se encontró el formulario del informe IVR')
        prefijo = m.group(1)
        boton = re.search(r'<input[^>]*id="([^"]+)"[^>]*value="Descargar Informe"', pagina)

        self.form = {
            'prefijo': prefijo,
            'boton': boton.group(1) if boton else f'{prefijo}:__18',
            'win': _hidden(pagina, 'ice.window'),
            'view': _hidden(pagina, 'ice.view'),
            'vs': _hidden(pagina, 'javax.faces.ViewState'),
        }
        return self.form

    def _campos_form(self, desde: str, hasta: str) -> list[tuple[str, str]]:
        p = self.form['prefijo']
        return [
            ('ice.window', self.form['win']), ('ice.view', self.form['view']),
            (f'{p}:dataInizio_input', desde), (f'{p}:dataFine_input', hasta),
            (f'{p}:ura_input', 'Todos'),      # filtro de IVR: todos
            (f'{p}:numDaInstal', ''),         # N° suministro
            (f'{p}:numDeProt', ''),           # N° de caso
            (f'{p}:numTelCliente', ''),       # teléfono
            (f'{p}:pontoControle', ''),       # punto de control
            (f'{p}:principale_SUBMIT', '1'),
            ('javax.faces.ViewState', self.form['vs']),
            ('javax.faces.ClientWindow', self.form['win']),
            ('javax.faces.behavior.event', 'action'),
            ('javax.faces.partial.event', 'click'),
        ]

    def descargar(self, desde: datetime.date, hasta: datetime.date) -> dict:
        """Genera el informe del rango y devuelve el CSV en memoria.

        Devuelve {'contenido': bytes, 'bytes': int, 'seg_generar': float,
                  'seg_descargar': float, 'vacio': bool}.
        """
        # La pantalla se reabre SIEMPRE, y no es opcional: si se reusa el
        # ViewState después de una descarga, el segundo informe de la sesión
        # vuelve sin archivo y del tercero en adelante el portal directamente
        # se cuelga (medido el 2026-09-04: 1° día OK, 2° "no generó archivo",
        # 3° y 4° timeout). Con el GET de más, cuatro días seguidos salieron
        # en ~9 s cada uno. El GET además sale más barato que el ViewState
        # viejo: el mismo día tardó 1,9 s en generar con la pantalla fresca
        # contra 14,6 s reusando la anterior.
        self.abrir_informe()

        p = self.form['prefijo']
        d1, d2 = desde.strftime('%d/%m/%Y'), hasta.strftime('%d/%m/%Y')

        t0 = time.time()
        datos = self._campos_form(d1, d2) + [
            ('javax.faces.source', self.form['boton']),
            ('javax.faces.partial.ajax', 'true'),
            ('javax.faces.partial.execute', f'{p}:principale'),
            ('javax.faces.partial.render', f'{p}:dialogSection'),
            (f'{p}:principale', f'{p}:principale'),
        ]
        r = self._post_ajax(URL_IVR, datos, params=QS_IVR)
        xml = r.text
        if 'loginForm:username_input' in xml or 'springSecurityLogin' in r.url:
            raise SesionCaida('la sesión se cayó al generar el informe')

        vs = _viewstate_ajax(xml)
        if vs:
            self.form['vs'] = vs

        seg_generar = time.time() - t0
        m = re.search(r"downloadFile\('([^']+)'\)", xml)
        if not m:
            mensaje = _texto_plano(xml)
            # El portal no distingue "no hay datos" con un código: manda el
            # diálogo de mensaje en vez del de descarga.
            vacio = bool(re.search(r'no (hay|se encontraron|existen)|sin datos|nessun', mensaje, re.I))
            if vacio:
                return {'contenido': b'', 'bytes': 0, 'seg_generar': seg_generar,
                        'seg_descargar': 0.0, 'vacio': True, 'mensaje': mensaje}
            raise ErrorConsola(f'el portal no generó el archivo: {mensaje or "(respuesta vacía)"}')

        t1 = time.time()
        url_csv = BASE + m.group(1)
        buf = io.BytesIO()
        with self.s.get(url_csv, timeout=self.timeout, stream=True) as g:
            # Cuando el rango no tiene ni una llamada, el portal igual arma el
            # diálogo de descarga y hasta inventa un nombre de archivo, pero el
            # archivo no existe: el GET vuelve 404. Es la única forma que da de
            # distinguir "no hay datos" (probado con un día futuro y con 2021,
            # anterior al informe).
            if g.status_code == 404:
                return {'contenido': b'', 'bytes': 0, 'seg_generar': seg_generar,
                        'seg_descargar': time.time() - t1, 'vacio': True,
                        'mensaje': 'el portal no dejó archivo: el rango no tiene llamadas'}
            g.raise_for_status()
            for pedazo in g.iter_content(1 << 20):
                buf.write(pedazo)
        seg_descargar = time.time() - t1

        contenido = buf.getvalue()
        return {'contenido': contenido, 'bytes': len(contenido),
                'seg_generar': seg_generar, 'seg_descargar': seg_descargar, 'vacio': False}


# ==========================================
# PARSEO DEL CSV
# ==========================================

class ErrorCsv(RuntimeError):
    pass


def _a_int(valor: str):
    v = (valor or '').strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return int(float(v.replace(',', '.')))
        except ValueError:
            return None


def _a_fecha(valor: str):
    v = (valor or '').strip()
    if not v:
        return None
    for formato in ('%Y-%m-%d %H:%M:%S', '%d/%m/%Y %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(v, formato)
        except ValueError:
            continue
    return None


def parsear_csv(contenido: bytes) -> tuple[list[tuple], dict]:
    """Convierte el CSV del portal en filas listas para insertar.

    Valida el encabezado: si Enerval le agrega o le saca una columna al informe,
    esto corta acá y no mete datos corridos en la tabla.
    """
    if not contenido.strip():
        return [], {'largos': 0}

    texto = contenido.decode('latin1')
    lector = csv.reader(io.StringIO(texto), delimiter=';')
    encabezado = [c.strip() for c in next(lector)]
    # El CSV termina cada fila en ';', así que la última columna viene vacía.
    if encabezado and encabezado[-1] == '':
        encabezado = encabezado[:-1]
    if encabezado != NOMBRES:
        faltan = [c for c in NOMBRES if c not in encabezado]
        sobran = [c for c in encabezado if c not in NOMBRES]
        raise ErrorCsv(
            'el informe cambió de formato. '
            f'Faltan {faltan or "ninguna"}; sobran {sobran or "ninguna"}. '
            'Hay que actualizar COLUMNAS y la tabla antes de seguir cargando.'
        )

    filas: list[tuple] = []
    largos: list[str] = []
    for n, cruda in enumerate(lector, start=2):
        if not cruda or all(not c.strip() for c in cruda):
            continue
        fila = []
        for i, (nombre, tipo, largo) in enumerate(COLUMNAS):
            valor = cruda[i] if i < len(cruda) else ''
            if tipo == 'int':
                fila.append(_a_int(valor))
            elif tipo == 'fecha':
                fila.append(_a_fecha(valor))
            else:
                v = (valor or '').strip()
                if not v:
                    fila.append(None)
                else:
                    if largo and len(v) > largo:
                        largos.append(f'linea {n}, columna "{nombre}": {len(v)} caracteres')
                    fila.append(v)
        filas.append(tuple(fila))

    if largos:
        raise ErrorCsv(
            'hay valores más largos que la columna de SQL, no se carga nada para no truncar:\n  '
            + '\n  '.join(largos[:5])
            + (f'\n  ... y {len(largos) - 5} más' if len(largos) > 5 else '')
        )

    return filas, {'largos': 0}


# ==========================================
# CARGA A SQL
# ==========================================

_COLS_SQL = ', '.join(f'[{c}]' for c in NOMBRES)
_PLACEHOLDERS = ', '.join(['?'] * len(NOMBRES))
_IDX_FECHA = NOMBRES.index('Fecha de Inicio')
_IDX_BPO = NOMBRES.index('BPO')


def cargar_dia(engine, dia: datetime.date, filas: list[tuple], alcance: str) -> dict:
    """Reemplaza en SQL el día completo por lo que acaba de bajar el portal.

    Todo va en una transacción: o queda el día entero nuevo, o queda el viejo.
    """
    desde = datetime.datetime.combine(dia, datetime.time.min)
    hasta = desde + datetime.timedelta(days=1)

    # Filas que el portal devolvió fuera del día pedido (no debería pasar):
    # se descartan para no pisar días vecinos que ya están cargados.
    dentro = [f for f in filas if f[_IDX_FECHA] and desde <= f[_IDX_FECHA] < hasta]
    fuera = len(filas) - len(dentro)

    bpos = sorted({f[_IDX_BPO] for f in dentro if f[_IDX_BPO]})

    # Un CSV sin filas no borra nada. Es a propósito: si alguna vez el portal
    # contesta vacío por un hipo suyo, preferimos dejar el día como está antes
    # que vaciar en SQL un día que sí tenía llamadas.
    if not dentro:
        return {'filas': 0, 'fuera_de_rango': fuera, 'bpos': [], 'seg_insertar': 0.0}

    t0 = time.time()
    with engine.begin() as cx:
        crudo = cx.connection.driver_connection
        cursor = crudo.cursor()
        cursor.fast_executemany = True

        if alcance == 'propio':
            # Cuenta acotada: solo se reemplazan las BPO que la cuenta ve. Las
            # llamadas sin BPO (las que no llegaron a ningún operador) se tratan
            # como una BPO más, si es que el CSV trajo alguna.
            condiciones, params = [], [desde, hasta]
            if bpos:
                condiciones.append(f'[BPO] IN ({", ".join(["?"] * len(bpos))})')
                params += bpos
            if any(f[_IDX_BPO] is None for f in dentro):
                condiciones.append('[BPO] IS NULL')
            cursor.execute(
                f'DELETE FROM [{TABLA}] WHERE [Fecha de Inicio] >= ? AND [Fecha de Inicio] < ? '
                f'AND ({" OR ".join(condiciones)})',
                params)
        else:
            cursor.execute(
                f'DELETE FROM [{TABLA}] WHERE [Fecha de Inicio] >= ? AND [Fecha de Inicio] < ?',
                [desde, hasta])

        sql_insert = f'INSERT INTO [{TABLA}] ({_COLS_SQL}) VALUES ({_PLACEHOLDERS})'
        for i in range(0, len(dentro), FILAS_POR_LOTE):
            cursor.executemany(sql_insert, dentro[i:i + FILAS_POR_LOTE])
        cursor.close()

    return {'filas': len(dentro), 'fuera_de_rango': fuera, 'bpos': bpos,
            'seg_insertar': time.time() - t0}


def anotar_carga(engine, dia: datetime.date, estado: str, **campos):
    """Deja constancia del día en la tabla de cargas (upsert)."""
    with engine.begin() as cx:
        cx.execute(text(f"""
            MERGE [{TABLA_CARGAS}] AS destino
            USING (SELECT :dia AS Dia) AS origen ON destino.Dia = origen.Dia
            WHEN MATCHED THEN UPDATE SET
                Estado = :estado, Filas = :filas, Bytes = :bytes, Alcance = :alcance,
                Usuario = :usuario, SegGenerar = :gen, SegDescargar = :baj,
                SegInsertar = :ins, Intentos = destino.Intentos + 1,
                Error = :error, FechaCarga = SYSDATETIME()
            WHEN NOT MATCHED THEN INSERT
                (Dia, Estado, Filas, Bytes, Alcance, Usuario, SegGenerar, SegDescargar,
                 SegInsertar, Intentos, Error)
                VALUES (:dia, :estado, :filas, :bytes, :alcance, :usuario, :gen, :baj,
                        :ins, 1, :error);
        """), {
            'dia': dia, 'estado': estado,
            'filas': campos.get('filas'), 'bytes': campos.get('bytes'),
            'alcance': campos.get('alcance'), 'usuario': campos.get('usuario'),
            'gen': campos.get('seg_generar'), 'baj': campos.get('seg_descargar'),
            'ins': campos.get('seg_insertar'),
            'error': (campos.get('error') or None) and campos['error'][:1000],
        })


def dias_ya_cargados(engine, desde: datetime.date, hasta: datetime.date,
                     alcance: str, incluir_vacios: bool = True) -> set:
    """Días que no hace falta volver a pedir.

    El alcance importa: un día que se bajó con una cuenta acotada ('propio')
    tiene solo las BPO de esa cuenta, así que para una corrida 'completo' está
    incompleto y hay que rehacerlo. Al revés no: si el día ya se bajó completo,
    una corrida acotada no agrega nada.
    """
    estados = "('OK', 'VACIO')" if incluir_vacios else "('OK')"
    with engine.connect() as cx:
        filas = cx.execute(text(
            f"SELECT Dia FROM [{TABLA_CARGAS}] WHERE Estado IN {estados} "
            f"AND Dia BETWEEN :d AND :h "
            f"AND (Alcance = 'completo' OR :alcance = 'propio')"),
            {'d': desde, 'h': hasta, 'alcance': alcance}).fetchall()
    return {f[0] for f in filas}


# ==========================================
# PROCESO
# ==========================================

def procesar_dia(consola: ConsolaEnerval, engine, dia: datetime.date, args) -> dict:
    """Baja un día y lo carga. Devuelve el resumen para el log."""
    descarga = consola.descargar(dia, dia)

    if args.csv_dir:
        os.makedirs(args.csv_dir, exist_ok=True)
        destino = os.path.join(args.csv_dir, f'ivr_{dia:%Y-%m-%d}.csv')
        with open(destino, 'wb') as f:
            f.write(descarga['contenido'])

    if descarga['vacio']:
        return {'estado': 'VACIO', 'filas': 0, 'bytes': 0,
                'seg_generar': descarga['seg_generar'], 'seg_descargar': 0.0,
                'seg_insertar': 0.0, 'bpos': []}

    filas, _ = parsear_csv(descarga['contenido'])
    if args.solo_csv:
        return {'estado': 'CSV', 'filas': len(filas), 'bytes': descarga['bytes'],
                'seg_generar': descarga['seg_generar'],
                'seg_descargar': descarga['seg_descargar'], 'seg_insertar': 0.0, 'bpos': []}

    carga = cargar_dia(engine, dia, filas, args.alcance)
    return {'estado': 'OK' if carga['filas'] else 'VACIO',
            'filas': carga['filas'], 'bytes': descarga['bytes'],
            'seg_generar': descarga['seg_generar'],
            'seg_descargar': descarga['seg_descargar'],
            'seg_insertar': carga['seg_insertar'],
            'fuera_de_rango': carga['fuera_de_rango'], 'bpos': carga['bpos']}


def calcular_limite(args) -> datetime.datetime | None:
    """Momento en que la corrida tiene que cortar, o None si corre hasta terminar.

    Existe para el backfill de madrugada: la cuenta del portal la usan personas
    durante el día y solo admite una sesión, así que la corrida tiene que
    devolverla antes de que arranque la operación.
    """
    limites = []
    if getattr(args, 'max_horas', None):
        limites.append(datetime.datetime.now() + datetime.timedelta(hours=args.max_horas))
    if getattr(args, 'hasta_las', None):
        ahora = datetime.datetime.now()
        corte = ahora.replace(hour=args.hasta_las.hour, minute=args.hasta_las.minute,
                              second=0, microsecond=0)
        # Arrancar 23:30 con --hasta-las 06:00 tiene que cortar a la mañana
        # siguiente, no cinco minutos después de empezar.
        if corte <= ahora:
            corte += datetime.timedelta(days=1)
        limites.append(corte)
    return min(limites) if limites else None


def recorrer(dias: list, consola: ConsolaEnerval, engine, args) -> dict:
    total = {'ok': 0, 'vacios': 0, 'error': 0, 'filas': 0, 'sin_procesar': 0}
    t_inicio = time.time()
    limite = calcular_limite(args)
    if limite:
        print(f'Ventana: corto a las {limite:%Y-%m-%d %H:%M} y dejo la cuenta libre.')

    for n, dia in enumerate(dias, start=1):
        # El corte se evalúa entre días: un día tarda menos de un minuto, así que
        # no hace falta abortar uno a la mitad y dejarlo cargado por la mitad.
        if limite and datetime.datetime.now() >= limite:
            total['sin_procesar'] = len(dias) - n + 1
            print(f'Se terminó la ventana: corto acá. Quedan {total["sin_procesar"]} días '
                  f'({dia:%Y-%m-%d} .. {dias[-1]:%Y-%m-%d}) para la próxima corrida.', flush=True)
            break

        ultimo_error = None
        for intento in range(1, REINTENTOS_DIA + 1):
            try:
                r = procesar_dia(consola, engine, dia, args)
                fuera = r.get('fuera_de_rango') or 0
                aviso = f' ({fuera} filas fuera del día, descartadas)' if fuera else ''
                bpos = ','.join(r['bpos']) if r['bpos'] else '-'
                print(f'[{n}/{len(dias)}] {dia:%Y-%m-%d}  {r["estado"]:5} '
                      f'{r["filas"]:7,} filas  gen {r["seg_generar"]:5.1f}s  '
                      f'baj {r["seg_descargar"]:4.1f}s  sql {r["seg_insertar"]:4.1f}s  '
                      f'BPO {bpos}{aviso}', flush=True)
                if not args.solo_csv:
                    campos = {k: v for k, v in r.items() if k != 'estado'}
                    anotar_carga(engine, dia, r['estado'], usuario=args.usuario,
                                 alcance=args.alcance, **campos)
                total['ok' if r['estado'] in ('OK', 'CSV') else 'vacios'] += 1
                total['filas'] += r['filas']
                ultimo_error = None
                break
            except SesionCaida as e:
                ultimo_error = e
                print(f'  la sesión se cayó ({e}); vuelvo a entrar', flush=True)
                try:
                    consola.login()
                    consola.abrir_informe()
                except Exception as e2:
                    ultimo_error = e2
                    time.sleep(5)
            except Exception as e:
                ultimo_error = e
                print(f'  intento {intento}/{REINTENTOS_DIA} falló: {type(e).__name__}: '
                      f'{str(e)[:200]}', flush=True)
                time.sleep(3 * intento)

        if ultimo_error is not None:
            print(f'[{n}/{len(dias)}] {dia:%Y-%m-%d}  ERROR  {type(ultimo_error).__name__}: '
                  f'{str(ultimo_error)[:200]}', flush=True)
            if not args.solo_csv:
                anotar_carga(engine, dia, 'ERROR', usuario=args.usuario, alcance=args.alcance,
                             error=f'{type(ultimo_error).__name__}: {ultimo_error}')
            total['error'] += 1
            if args.cortar_en_error:
                break

        time.sleep(PAUSA_ENTRE_DIAS)

    total['segundos'] = time.time() - t_inicio
    return total


# ==========================================
# UTILIDADES
# ==========================================

def cargar_credenciales(necesita_portal: bool = True):
    """Busca el .env junto al script, en la raíz del repo, o usa el ambiente.

    `estado` solo lee SQL, así que no exige las credenciales del portal.
    """
    aca = os.path.dirname(os.path.abspath(__file__))
    for ruta in (os.path.join(aca, '.env'),
                 os.path.join(os.path.dirname(aca), '.env'),
                 os.path.join(aca, 'Modulos', '.env')):
        if os.path.exists(ruta):
            load_dotenv(dotenv_path=ruta)
            break
    else:
        load_dotenv()

    pedidas = ('USER_ENERVAL', 'PASS_ENERVAL', 'CONNECTION_STRING') if necesita_portal else ('CONNECTION_STRING',)
    faltantes = [v for v in pedidas if not os.getenv(v)]
    if faltantes:
        raise SystemExit(f'Faltan variables de entorno: {", ".join(faltantes)}')
    return os.getenv('USER_ENERVAL'), os.getenv('PASS_ENERVAL'), os.getenv('CONNECTION_STRING')


def fecha(txt: str) -> datetime.date:
    return datetime.datetime.strptime(txt, '%Y-%m-%d').date()


def hora(txt: str) -> datetime.time:
    return datetime.datetime.strptime(txt, '%H:%M').time()


def mostrar_estado(engine):
    with engine.connect() as cx:
        resumen = cx.execute(text(f"""
            SELECT Estado, COUNT(*) AS dias, SUM(CAST(Filas AS bigint)) AS filas,
                   MIN(Dia) AS desde, MAX(Dia) AS hasta
            FROM [{TABLA_CARGAS}] GROUP BY Estado ORDER BY Estado
        """)).fetchall()
        tabla = cx.execute(text(f"""
            SELECT COUNT(*) AS filas, MIN([Fecha de Inicio]) AS desde,
                   MAX([Fecha de Inicio]) AS hasta FROM [{TABLA}]
        """)).fetchone()
        errores = cx.execute(text(f"""
            SELECT TOP 10 Dia, Intentos, Error FROM [{TABLA_CARGAS}]
            WHERE Estado = 'ERROR' ORDER BY Dia
        """)).fetchall()

    print(f'\n{TABLA}: {tabla[0]:,} filas  ({tabla[1]} .. {tabla[2]})\n')
    print(f'{"Estado":8} {"días":>6} {"filas":>12}  rango')
    for r in resumen:
        print(f'{r[0]:8} {r[1]:6,} {(r[2] or 0):12,}  {r[3]} .. {r[4]}')
    if errores:
        print('\nDías con error:')
        for r in errores:
            print(f'  {r[0]}  intentos={r[1]}  {(r[2] or "")[:120]}')


# ==========================================
# MAIN
# ==========================================

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='modo', required=True)

    comunes = argparse.ArgumentParser(add_help=False)
    comunes.add_argument('--alcance', choices=('completo', 'propio'), default='completo',
                         help='completo: reemplaza el día entero (cuenta que ve todas las BPO). '
                              'propio: reemplaza solo las BPO que vinieron en el CSV.')
    comunes.add_argument('--csv-dir', default=None,
                         help='guarda además cada CSV crudo en esta carpeta')
    comunes.add_argument('--solo-csv', action='store_true',
                         help='descarga sin tocar SQL (sirve para probar)')
    comunes.add_argument('--hasta-las', type=hora, default=None, metavar='HH:MM',
                         help='corta la corrida a esta hora y cierra la sesión del portal '
                              '(para que la cuenta quede libre a la mañana). Ej: --hasta-las 06:00')
    comunes.add_argument('--max-horas', type=float, default=None,
                         help='corta después de estas horas de corrida')

    d = sub.add_parser('diario', parents=[comunes], help='hoy (y ayer si es de madrugada)')
    d.add_argument('--dias-atras', type=int, default=None,
                   help='cuántos días hacia atrás refrescar (default: 1 antes de las 6 AM, 0 después)')

    h = sub.add_parser('historico', parents=[comunes], help='rango largo, día por día, reanudable')
    h.add_argument('--desde', type=fecha, default=DESDE_DEFAULT)
    h.add_argument('--hasta', type=fecha, default=datetime.date.today() - datetime.timedelta(days=1))
    h.add_argument('--forzar', action='store_true',
                   help='rehace también los días ya cargados')
    h.add_argument('--rehacer-vacios', action='store_true',
                   help='vuelve a pedir los días anotados como VACIO (por si algún '
                        '404 tapó una falla del portal)')
    h.add_argument('--cortar-en-error', action='store_true',
                   help='frena en el primer día que falla en vez de seguir')

    sub.add_parser('estado', help='qué días están cargados')

    args = p.parse_args(argv)
    for atributo, valor in (('csv_dir', None), ('solo_csv', False), ('alcance', 'completo'),
                            ('cortar_en_error', False), ('forzar', False),
                            ('rehacer_vacios', False), ('hasta_las', None), ('max_horas', None)):
        if not hasattr(args, atributo):
            setattr(args, atributo, valor)

    usuario, clave, conexion = cargar_credenciales(necesita_portal=args.modo != 'estado')
    args.usuario = usuario
    engine = create_engine(conexion, fast_executemany=True)

    if args.modo == 'estado':
        mostrar_estado(engine)
        return 0

    hoy = datetime.date.today()
    if args.modo == 'diario':
        atras = args.dias_atras if args.dias_atras is not None else (1 if datetime.datetime.now().hour < 6 else 0)
        dias = [hoy - datetime.timedelta(days=n) for n in range(atras, -1, -1)]
    else:
        if args.desde < PISO_HISTORICO:
            print(f'El portal no tiene nada antes de {PISO_HISTORICO}; arranco desde ahí.')
            args.desde = PISO_HISTORICO
        dias = [args.desde + datetime.timedelta(days=n)
                for n in range((args.hasta - args.desde).days + 1)]
        if not args.forzar and not args.solo_csv:
            ya = dias_ya_cargados(engine, args.desde, args.hasta, args.alcance,
                                  incluir_vacios=not args.rehacer_vacios)
            antes = len(dias)
            dias = [d for d in dias if d not in ya]
            print(f'{antes} días en el rango, {antes - len(dias)} ya cargados, '
                  f'quedan {len(dias)} por bajar.')

    if not dias:
        print('No hay días para procesar.')
        return 0

    print(f'Cuenta {usuario} | alcance {args.alcance} | {len(dias)} día(s): '
          f'{dias[0]:%Y-%m-%d} .. {dias[-1]:%Y-%m-%d}')

    consola = ConsolaEnerval(usuario, clave)
    t0 = time.time()
    consola.login()
    print(f'Login OK en {time.time() - t0:.1f}s')
    try:
        consola.abrir_informe()
        total = recorrer(dias, consola, engine, args)
    finally:
        print('Logout:', 'OK' if consola.logout() else 'no confirmado')

    minutos = total['segundos'] / 60
    pendientes = total.get('sin_procesar', 0)
    print(f'\nListo en {minutos:.1f} min — {total["ok"]} días con datos, '
          f'{total["vacios"]} vacíos, {total["error"]} con error, '
          f'{total["filas"]:,} filas cargadas.')
    if pendientes:
        print(f'Quedaron {pendientes} días sin bajar por la ventana horaria: '
              f'volvé a correr el mismo comando y sigue desde ahí.')
    return 1 if total['error'] else 0


if __name__ == '__main__':
    sys.exit(main())
