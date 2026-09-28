import io
import os
import re
import logging
import threading
import unicodedata
from typing import Union, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
from bs4 import BeautifulSoup
from app.config import settings
from sqlalchemy import Engine

logger = logging.getLogger(__name__)

# Cómo llamamos, en el JSON que lee la IA, a cada rol de los que Mitrol marca en el
# encabezado de cada segmento del chat ("Nombre [ Normal / Agent ]").
ROLES_MITROL = {
    'agent': 'OPERADOR',
    'client': 'CLIENTE',
    'ivr': 'BOT_IVR',
    'supervisor': 'SUPERVISOR',
}

# "Nombre Apellido [ Normal / Agent ]" -> ("Nombre Apellido", "Agent")
_RE_PARTE = re.compile(r'^(.*?)\s*\[\s*[^/\]]*/\s*([A-Za-z]+)\s*\]\s*$')
# El encabezado de cada tabla es "<idInteraccion>:<segmento>" (sin espacios, así no
# lo confundimos con un evento que termine en hora, tipo "... terminado a las 21:52:24")
_RE_SEGMENTO = re.compile(r'^\S+:(\d+)$')


def _normalizar_nombre(nombre: Union[str, None]) -> str:
    """Nombre comparable: sin acentos, sin dobles espacios y en minúsculas.

    El nombre del operador viaja por dos caminos distintos (la columna
    [Nombre Agente] de la base y el texto del encabezado del chat), y difieren en
    mayúsculas/acentos ('RODRIGO BLANCO' vs 'Roman Barreiro').
    """
    if not nombre:
        return ""
    sin_acentos = unicodedata.normalize('NFKD', str(nombre))
    sin_acentos = "".join(c for c in sin_acentos if not unicodedata.combining(c))
    return " ".join(sin_acentos.lower().split())


def _fecha_a_texto(valor) -> Union[str, None]:
    """Fecha legible para el JSON del chat, o None si no hay dato."""
    if valor is None or pd.isna(valor):
        return None
    try:
        return pd.Timestamp(valor).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return str(valor)


def _texto(valor) -> str:
    """Valor de la base como texto limpio ('' si viene nulo)."""
    if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
        return ""
    return str(valor).strip()


class Mitrol:
    def __init__(self, usuario: str, clave: str, carpeta_descarga: str) -> None:
        """Inicio de la clase para utilizar Mitrol con soporte de re-login y multi-threading."""
        self.session = requests.Session()
        self._lock = threading.Lock()
        
        # Configurar pool para ThreadPoolExecutor y reintentos a nivel red
        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=Retry(total=2, backoff_factor=0.3, status_forcelist=[502, 503, 504])
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        self.carpeta_descarga = carpeta_descarga
        if self.carpeta_descarga:
            os.makedirs(self.carpeta_descarga, exist_ok=True)
        self.username = usuario
        self.password = clave
        
        self.login_session()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, 'session') and self.session:
            self.session.close()

    def login_session(self) -> bool:
        """Autentica o reautentica la sesión en Mitrol de forma thread-safe."""
        with self._lock:
            url = settings.MITROL_URL
            payload = {
                'TboxUser': self.username,
                'TboxPass': self.password,
                '__LASTFOCUS': '',
                '__EVENTTARGET': 'Btn_LogIn$lnk_Button'
            }
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
            }
            try:
                response = self.session.post(url, headers=headers, data=payload, timeout=30)
                if response.status_code == 200:
                    logger.info("Login exitoso en Mitrol.")
                    return True
                else:
                    logger.error(f"Error en login Mitrol: {response.status_code}")
                    return False
            except Exception as e:
                logger.error(f"Excepción durante login en Mitrol: {e}")
                return False

    def _es_respuesta_login_o_invalida(self, response: requests.Response, esperar_audio: bool = False) -> bool:
        """Verifica si la respuesta HTTP corresponde a una redirección a Login o expiración."""
        if response.status_code in (401, 403):
            return True
        if "login" in (response.url or "").lower():
            return True
        content_type = response.headers.get("content-type", "").lower()
        raw = response.content if hasattr(response, "content") and response.content else getattr(response, "text", "").encode("utf-8", errors="ignore")
        if "text/html" in content_type:
            content_lower = raw[:8000].lower()
            if b"btn_login" in content_lower or b"tboxuser" in content_lower:
                return True
            if esperar_audio:
                # Si esperábamos audio pero vino una página HTML (login o error ASP.NET), es inválido
                return True
        elif esperar_audio:
            if raw and not raw.startswith(b"RIFF"):
                return True
        return False
        
    
    def _parse_partes_del_segmento(self, fila) -> Union[dict, None]:
        """Del encabezado de un segmento devuelve quién está a cada lado.

        La fila trae dos celdas del estilo 'Bella Luz Ind_2754... [ Normal / Client ]'
        y 'Carla Romero [ Normal / Agent ]': la primera es la parte de la
        izquierda y la segunda la de la derecha.
        """
        celdas = fila.find_all('td', recursive=False) or fila.find_all('td')
        if len(celdas) != 2:
            return None

        partes = []
        for celda in celdas:
            match = _RE_PARTE.match(celda.get_text(strip=True))
            if not match:
                return None
            etiqueta, rol = match.group(1).strip(), match.group(2).strip().lower()
            # El cliente viene como "Nombre_idDeLaRedSocial": el id no aporta nada.
            nombre = etiqueta.split('_')[0].strip() if rol == 'client' else etiqueta
            partes.append({"nombre": nombre, "quien": ROLES_MITROL.get(rol, rol.upper())})

        return {"izquierda": partes[0], "derecha": partes[1]}

    def _parse_chat_html(self, html_string, segmento_auditado: Union[int, None] = None, operador_auditado: Union[str, None] = None, operadores_por_segmento: Union[dict, None] = None) -> dict:
        """Convierte el HTML de GetChat.aspx en una transcripción estructurada.

        Cada `<table>` del HTML es un segmento de la conversación: el primero suele
        ser el bot/IVR y después hay uno por cada operador que la atendió. El
        encabezado del segmento dice qué parte está de cada lado ('Nombre
        [ Normal / Client ]', '... / Agent', '... / Ivr') y cada burbuja se alinea
        del lado de quien la escribió.

        Los roles se resuelven por segmento y no por alineación sola: qué lado es el
        operador cambia de un segmento a otro (en el del IVR la derecha es el
        cliente; en varios segmentos con operador, también). Deducirlo solo de la
        alineación —como se hacía antes— daba vuelta cliente y operador justo en los
        segmentos que se auditan, que son los que tienen operador.

        `segmento_auditado` y `operador_auditado` marcan, mensaje por mensaje, cuál
        es el operador bajo auditoría (`es_operador_auditado`), para que la IA no
        tenga que adivinarlo entre los varios operadores que pueden pasar por la
        misma conversación.

        `operadores_por_segmento` ({segmento: nombre del operador según la base}) se
        usa para detectar los segmentos que Mitrol dibuja mal: cuando el operador
        del encabezado no es el que realmente atendió el segmento, la página manda
        todas las burbujas al mismo lado (las del operador real quedan del lado del
        cliente). Ahí no se puede saber quién escribió cada mensaje, y se marcan
        como INDETERMINADO en vez de atribuirlos mal.
        """
        soup = BeautifulSoup(html_string, 'html.parser')

        objetivo = _normalizar_nombre(operador_auditado)
        operadores_por_segmento = operadores_por_segmento or {}
        transcripcion = []
        operadores_del_chat = []
        segmentos_dudosos = []
        cliente = ""
        partes = None  # se arrastra entre segmentos por si alguno viene sin encabezado
        atribucion_dudosa = False
        aviso_pendiente = None

        for tabla in soup.find_all('table'):
            segmento = None

            for fila in tabla.find_all('tr'):
                # 1. Encabezado del segmento: "<idInteraccion>:<segmento>"
                if segmento is None:
                    match_seg = _RE_SEGMENTO.match(fila.get_text(strip=True))
                    if match_seg:
                        segmento = int(match_seg.group(1))
                        continue

                # 2. Encabezado con las dos partes del segmento
                partes_fila = self._parse_partes_del_segmento(fila)
                if partes_fila:
                    partes = partes_fila
                    operador_del_encabezado = ""
                    for lado in partes.values():
                        if lado["quien"] == "CLIENTE" and lado["nombre"]:
                            cliente = lado["nombre"]
                        elif lado["quien"] == "OPERADOR":
                            operador_del_encabezado = lado["nombre"]
                            if lado["nombre"] not in operadores_del_chat:
                                operadores_del_chat.append(lado["nombre"])

                    operador_real = operadores_por_segmento.get(segmento)
                    atribucion_dudosa = bool(
                        operador_del_encabezado and operador_real
                        and _normalizar_nombre(operador_del_encabezado) != _normalizar_nombre(operador_real)
                    )
                    # El aviso se agrega recién si el tramo tiene mensajes: los segmentos
                    # que solo registran una transferencia no tienen nada que atribuir.
                    aviso_pendiente = {
                        "tipo": "aviso",
                        "segmento": segmento,
                        "texto": (
                            f"Mitrol no distingue el emisor en este tramo: los mensajes son del "
                            f"operador {operador_real} o del cliente{f' {cliente}' if cliente else ''}, "
                            "pero vienen todos del mismo lado. Deducilos por el contenido."
                        ),
                    } if atribucion_dudosa else None
                    continue

                # 3. Mensajes del sistema (transferencias, cierres, etc.)
                td_sistema = fila.find('td', style=re.compile(r'text-align:center;color:#B0B0B0'))
                if td_sistema:
                    transcripcion.append({
                        "tipo": "evento",
                        "segmento": segmento,
                        "texto": td_sistema.get_text(strip=True),
                    })
                    continue

                # 4. Burbujas de chat
                div_chat = fila.find('div', style=re.compile(r'border-radius'))
                if not div_chat:
                    continue

                span_hora = div_chat.find('span')
                hora = span_hora.get_text(strip=True) if span_hora else ""
                if span_hora:
                    # Sacamos la hora del div para quedarnos solo con el mensaje.
                    span_hora.extract()

                estilo_fila = fila.get('style', '') or ''
                lado = 'derecha' if 'text-align:right' in estilo_fila else 'izquierda'
                emisor = (partes or {}).get(lado) or {"nombre": "", "quien": "DESCONOCIDO"}

                if atribucion_dudosa:
                    emisor = {"nombre": "", "quien": "INDETERMINADO"}
                    if aviso_pendiente:
                        transcripcion.append(aviso_pendiente)
                        segmentos_dudosos.append(segmento)
                        aviso_pendiente = None

                es_operador_auditado = emisor["quien"] == "OPERADOR" and (
                    (objetivo and _normalizar_nombre(emisor["nombre"]) == objetivo)
                    or (segmento_auditado is not None and segmento == segmento_auditado)
                )

                transcripcion.append({
                    "tipo": "mensaje",
                    "segmento": segmento,
                    "quien": emisor["quien"],
                    "nombre": emisor["nombre"],
                    "es_operador_auditado": bool(es_operador_auditado),
                    "hora": hora,
                    "texto": div_chat.get_text(separator='\n', strip=True),
                })

        return {
            "cliente": cliente,
            "operadores_que_intervinieron": operadores_del_chat,
            "segmentos_con_emisor_indeterminado": segmentos_dudosos,
            "transcripcion": transcripcion,
        }

    def Grabaciones(self, FilePath: str, FileName: str) -> Union[str, None]:
        """Descarga de archivos usando la cuenta de Mitrol, con auto-reautenticación ante expiración."""
        if not FilePath or not FileName or pd.isna(FilePath) or pd.isna(FileName):
            return None
        if str(FilePath).strip().lower() in ("", "nan", "none") or str(FileName).strip().lower() in ("", "nan", "none"):
            return None

        url = f"https://apps.acme-solutions.example/reportes/GetWave.ashx?sPath={FilePath}&sFile={FileName}.wav"
        
        try:
            response = self.session.get(url, stream=True, timeout=60)
            
            # Si la sesión expiró o redirigió al login, reautenticar y reintentar
            if self._es_respuesta_login_o_invalida(response, esperar_audio=True):
                logger.warning(f"Sesión Mitrol expirada al descargar {FileName}, reautenticando...")
                if self.login_session():
                    response = self.session.get(url, stream=True, timeout=60)

            if response.status_code == 200:
                if self._es_respuesta_login_o_invalida(response, esperar_audio=True):
                    logger.error(f"Respuesta inválida (login) tras reautenticación para audio {FileName}")
                    return None

                if self.carpeta_descarga:
                    os.makedirs(self.carpeta_descarga, exist_ok=True)
                audio_bytes = io.BytesIO(response.content)
                save_path = os.path.join(self.carpeta_descarga, f"{FileName}.wav")

                # Guardar el archivo en disco
                with open(save_path, 'wb') as f:
                    f.write(audio_bytes.getbuffer())

                if not os.path.exists(save_path):
                    logger.error(f"Error al guardar el archivo: {save_path}")
                    return None

                return save_path
            else:
                logger.error(f"Error al descargar el audio. Código de estado: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Excepción al descargar audio Mitrol {FileName}: {e}")
            return None
    
    def Chat(self, idInteraccion: str, segmento_auditado: Union[int, None] = None, operador_auditado: Union[str, None] = None, operadores_por_segmento: Union[dict, None] = None) -> Union[dict, None]:
        if not idInteraccion or pd.isna(idInteraccion) or str(idInteraccion).strip().lower() in ("", "nan", "none"):
            return None

        url = f'https://apps.acme-solutions.example/reportes/GetChat.aspx?idInteraccion={idInteraccion}&segmento=99&ChatCompleto=1||'

        try:
            response = self.session.get(url, timeout=45)

            if self._es_respuesta_login_o_invalida(response, esperar_audio=False):
                logger.warning(f"Sesión Mitrol expirada al consultar chat {idInteraccion}, reautenticando...")
                if self.login_session():
                    response = self.session.get(url, timeout=45)

            if response.status_code == 200:
                if self._es_respuesta_login_o_invalida(response, esperar_audio=False):
                    logger.error(f"Respuesta inválida (login) tras reautenticación para chat {idInteraccion}")
                    return None

                return self._parse_chat_html(
                    response.text,
                    segmento_auditado=segmento_auditado,
                    operador_auditado=operador_auditado,
                    operadores_por_segmento=operadores_por_segmento,
                )
            else:
                logger.error(f"Error al obtener el chat. Código de estado: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Excepción al obtener chat Mitrol {idInteraccion}: {e}")
            return None

    def _gestiones_del_historial(self, engine:Engine, idInteraccion:str, dias_atras:int) -> pd.DataFrame:
        """Segmentos (gestiones) de todos los chats del mismo cliente en la ventana.

        Un chat de Mitrol se parte en segmentos y cada segmento es una gestión con su
        propio operador y su propia tipificación (una misma conversación puede pasar
        por tres operadores y tipificarse distinto en cada uno). Por eso se traen
        todos los segmentos y no solo el idInteraccion.
        """
        id_sql = str(idInteraccion).replace("'", "''")
        query = f"""
        -- 1. Definimos los parámetros de búsqueda
        DECLARE @IdInteraccionBuscado VARCHAR(100) = '{id_sql}';
        DECLARE @DiasHaciaAtras INT = {int(dias_atras)};

        -- 2. Variables para almacenar los datos del cliente
        DECLARE @ClienteBuscado VARCHAR(150);
        DECLARE @FechaBase DATETIME;

        -- 3. Obtenemos el Cliente y la Fecha (Inicio) rapidísimo
        SELECT TOP 1
            @ClienteBuscado = ddicl.Cliente,
            @FechaBase = ddicl.Inicio
        FROM [Acme].[dbo].[detalle de grabaciones] ddg
        JOIN [Acme].[dbo].[detalle_de_interacciones_por_campana_lote] ddicl
            ON ddicl.idInteraccion = ddg.idInteraccion
        AND ddicl.segmento = ddg.segmento
        WHERE ddg.idInteraccion = @IdInteraccionBuscado
        AND ddg.chat = 1
        ORDER BY ddicl.Inicio;

        -- 4. Calculamos los límites exactos de tiempo
        DECLARE @InicioDiaBase DATETIME = CAST(CAST(@FechaBase AS DATE) AS DATETIME);
        DECLARE @FinDiaBase DATETIME = DATEADD(DAY, 1, @InicioDiaBase);
        DECLARE @FechaInicioBusqueda DATETIME = DATEADD(DAY, -@DiasHaciaAtras, @InicioDiaBase);

        -- 5. Consulta final: un renglón por gestión (chat + segmento). El OR final
        -- garantiza que el chat que se está auditando esté siempre, aunque el
        -- cliente venga nulo o la conversación haya arrancado fuera de la ventana.
        SELECT DISTINCT
            ddg.idInteraccion,
            ddicl.Segmento,
            ddicl.Inicio,
            ddicl.LoginId,
            ddicl.[Nombre Agente] AS NombreAgente,
            ddicl.[Tipificación] AS Tipificacion,
            ddicl.Cliente
        FROM [Acme].[dbo].[detalle de grabaciones] ddg
        JOIN [Acme].[dbo].[detalle_de_interacciones_por_campana_lote] ddicl
            ON ddicl.idInteraccion = ddg.idInteraccion
        AND ddicl.segmento = ddg.segmento
        WHERE ddg.chat = 1
        AND (
                (
                    ddicl.Cliente = @ClienteBuscado
                AND ddicl.Inicio >= @FechaInicioBusqueda
                AND ddicl.Inicio < @FinDiaBase
                )
            OR ddg.idInteraccion = @IdInteraccionBuscado
        )
        ORDER BY ddicl.Inicio, ddicl.Segmento;"""

        return pd.read_sql(query, engine)

    def Chat_historico(self, engine:Engine, idInteraccion:str, segmento: Union[int, None] = None, dias_atras:int=14):
        """Historial de chats del cliente, con el operador auditado ya identificado.

        Devuelve el JSON que se le adjunta a la IA: la ficha de la gestión que se
        audita (operador, tipificación, cliente), la ventana de `dias_atras` días
        que se trajo, y todas las conversaciones de ese cliente en ese período
        ordenadas cronológicamente, con cada mensaje marcado como del OPERADOR, del
        CLIENTE o del BOT_IVR.
        """
        df = self._gestiones_del_historial(engine, idInteraccion, dias_atras)
        if df.empty:
            return None

        # Ficha de la gestión auditada: la del segmento pedido y, si no vino segmento,
        # la última gestión con operador de esa conversación.
        del_chat = df[df['idInteraccion'] == idInteraccion]
        auditada = del_chat[del_chat['Segmento'] == segmento] if segmento is not None else del_chat.iloc[0:0]
        if auditada.empty:
            con_operador = del_chat[del_chat['NombreAgente'].notna()]
            auditada = con_operador.tail(1) if not con_operador.empty else del_chat.tail(1)
        fila_auditada = auditada.iloc[0] if not auditada.empty else None

        operador_auditado = _texto(fila_auditada['NombreAgente']) if fila_auditada is not None else ""
        segmento_auditado = segmento if segmento is not None else (
            int(fila_auditada['Segmento']) if fila_auditada is not None and not pd.isna(fila_auditada['Segmento']) else None
        )

        conversaciones = []
        # groupby con sort=False respeta el ORDER BY Inicio de la consulta: el
        # historial le llega a la IA en orden cronológico.
        for id_chat, gestiones in df.groupby('idInteraccion', sort=False):
            es_auditada = id_chat == idInteraccion
            operadores_por_segmento = {
                int(g['Segmento']): _texto(g['NombreAgente'])
                for _, g in gestiones.iterrows()
                if not pd.isna(g['Segmento']) and _texto(g['NombreAgente'])
            }
            # La marca de operador auditado solo se pone en la conversación que se
            # audita: en las de contexto el mismo operador puede haber atendido antes
            # y esos mensajes no se le califican.
            chat_data = self.Chat(
                id_chat,
                segmento_auditado=segmento_auditado if es_auditada else None,
                operador_auditado=operador_auditado if es_auditada else None,
                operadores_por_segmento=operadores_por_segmento,
            )
            if not chat_data:
                continue

            dudosos = set(chat_data.get("segmentos_con_emisor_indeterminado") or [])
            conversaciones.append({
                "idInteraccion": id_chat,
                "es_la_conversacion_a_auditar": es_auditada,
                "inicio": _fecha_a_texto(gestiones['Inicio'].min()),
                "cliente": chat_data.get("cliente") or _texto(gestiones['Cliente'].iloc[0]),
                "gestiones": [
                    {
                        "segmento": int(g['Segmento']) if not pd.isna(g['Segmento']) else None,
                        "inicio": _fecha_a_texto(g['Inicio']),
                        "operador": _texto(g['NombreAgente']),
                        "loginId": _texto(g['LoginId']),
                        "tipificacion": _texto(g['Tipificacion']),
                        "es_la_gestion_a_auditar": bool(
                            es_auditada and segmento_auditado is not None and g['Segmento'] == segmento_auditado
                        ),
                        # Mitrol dibujó este tramo sin distinguir emisor (ver el aviso
                        # en la transcripción); solo aparece cuando pasa.
                        **({"emisor_de_los_mensajes": "indeterminado"} if g['Segmento'] in dudosos else {}),
                    }
                    for _, g in gestiones.iterrows()
                ],
                "transcripcion": chat_data.get("transcripcion", []),
            })

        if not conversaciones:
            return None

        gestion_auditada = None
        if fila_auditada is not None:
            gestion_auditada = {
                "idInteraccion": idInteraccion,
                "segmento": segmento_auditado,
                "operador_a_auditar": operador_auditado,
                "loginId": _texto(fila_auditada['LoginId']),
                "tipificacion": _texto(fila_auditada['Tipificacion']),
                "cliente": _texto(fila_auditada['Cliente']),
                "inicio": _fecha_a_texto(fila_auditada['Inicio']),
            }

        return {
            "gestion_a_auditar": gestion_auditada,
            "ventana_historial": {
                "dias_hacia_atras": dias_atras,
                "conversaciones_incluidas": len(conversaciones),
            },
            "como_leer_este_archivo": [
                "Se audita SOLO la gestión descripta en 'gestion_a_auditar'; el resto de las "
                "conversaciones son historial del mismo cliente y sirven de contexto.",
                "En 'transcripcion', 'quien' indica quién escribió cada mensaje: OPERADOR, "
                "CLIENTE o BOT_IVR (respuestas automáticas, no puntúan al operador). "
                "'INDETERMINADO' significa que Mitrol no informó el emisor de ese tramo: "
                "deducilo por el contenido y, si queda duda, no lo cuentes como error.",
                "Los mensajes con 'es_operador_auditado': true son los del operador bajo "
                "auditoría; los de otros operadores no se le computan.",
                "Cada conversación se divide en 'gestiones' (segmentos): cada una tiene su "
                "operador y su tipificación, y la auditada está marcada con "
                "'es_la_gestion_a_auditar': true.",
            ],
            "conversaciones": conversaciones,
        }

