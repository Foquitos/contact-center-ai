"""Genesys Cloud (Benefix): login sin navegador, consultas de analytics y audios.

LOGIN
-----
La cuenta es un usuario de la consola web (no un cliente OAuth: no tiene permiso para
crear uno), así que se entra como entra el navegador, pero con `requests`. Son tres
pedidos, medidos el 2026-09-21 contra login.sae1.pure.cloud:

    1. GET  /oauth/authorize (cliente de la app web /directory/) -> 302 a /?rid=<id>
       La página trae dos <meta>: `csrf` y `requestId`.
    2. POST /login  {"username", "password"}  con los headers ININ-CSRF-TOKEN e
       ININ-Auth-Request-Id. El campo es `username`, no `email`: con `email` da 401
       igual que una clave mala.
    3. PUT  /request/<rid>  {"state": "pending"} -> {"status": "approved",
       "redirect": ".../directory/#access_token=...&expires_in=691199"}

El token dura ~8 días; se guarda en memoria del proceso (un login por proceso, no por
auditoría) y ante un 401 se vuelve a entrar una vez. Si la org llega a exigir MFA o
aceptar reglas de uso, el login lo dice en vez de colgarse.

AUDIOS
------
La cuenta tiene acceso por SEGMENTO a las grabaciones: con el id que devuelve
`recordingmetadata` la API contesta 403 ("Recording view permission denied"). El id que
sirve es el que devuelve `GET /api/v2/conversations/{id}/recordings`: el mismo id más un
sufijo base32 con el tramo permitido (`<inicio_ms>-<fin_ms>`). Ese endpoint además
transcodifica (202 mientras trabaja) y devuelve `mediaUris` con una URL firmada de
api-downloads.<region>/MediaCache/... que se baja sin token. Es lo que hace la consola
por detrás del OPTIONS + MediaCache que se ve en el navegador, en un solo pedido.

El audio trae solo el tiempo hablado con el operador: ni el IVR (280 s de audio para un
llamado de 369 s con 88 s de IVR) ni las esperas (175 s para 171 s hablados + 81 s en
espera). Viene en WAV PCM 8 kHz mono (~1 MB/min); la compresión a Opus la hace después
`get_audio_bytes._comprimir_audios_en_memoria`, como con Vantix.
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests

# Cliente OAuth de la app web de Genesys (Collaborate/Communicate = /directory/). Es
# público: sale del HTML de https://apps.<region>/directory/ y es el mismo para todas
# las orgs de producción.
CLIENTE_APP_WEB = '496cceb2-2f90-4b6e-83b3-d7d97f5ef061'

# La consulta de detalle de conversaciones no acepta intervalos de más de 7 días.
MAX_DIAS_POR_CONSULTA = 7
TAM_PAGINA = 100  # máximo que acepta analytics

# Tokens por (región, usuario) compartidos por todas las instancias del proceso: el
# backend y el scheduler no hacen un login por auditoría.
_TOKENS: Dict[tuple, tuple] = {}
_LOCK_LOGIN = threading.Lock()
# Margen para no usar un token que vence en medio de una corrida.
_MARGEN_VENCIMIENTO = timedelta(hours=1)


class ErrorGenesys(RuntimeError):
    """Falla del login o de la API de Genesys que no se arregla reintentando."""


class Genesys:
    """Cliente de la API de Genesys Cloud con el login de la consola web."""

    def __init__(self, usuario: str, clave: str, region: str = 'sae1.pure.cloud',
                 timeout: int = 60):
        if not usuario or not clave:
            raise ErrorGenesys('Faltan GENESYS_USER / GENESYS_PASS en el .env')
        self.usuario = usuario
        self._clave = clave
        self.region = region
        self.timeout = timeout
        self.url_api = f'https://api.{region}'
        self.url_login = f'https://login.{region}'
        self.url_apps = f'https://apps.{region}'
        self.http = requests.Session()
        self.http.headers['User-Agent'] = (
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/153.0 Safari/537.36')

    def __enter__(self) -> 'Genesys':
        self.login()
        return self

    def __exit__(self, *exc) -> None:
        self.http.close()

    # ------------------------------------------------------------------ login
    def login(self, forzar: bool = False) -> str:
        """Token vigente; entra de nuevo solo si no hay uno o si `forzar`."""
        clave_cache = (self.region, self.usuario.lower())
        with _LOCK_LOGIN:
            guardado = _TOKENS.get(clave_cache)
            if guardado and not forzar and guardado[1] - _MARGEN_VENCIMIENTO > datetime.now(timezone.utc):
                return guardado[0]
            token, vence = self._login_web()
            _TOKENS[clave_cache] = (token, vence)
            return token

    def _login_web(self) -> tuple:
        s = requests.Session()
        s.headers.update(self.http.headers)
        try:
            r = s.get(f'{self.url_login}/oauth/authorize', allow_redirects=False,
                      timeout=self.timeout, params={
                          'client_id': CLIENTE_APP_WEB, 'response_type': 'token',
                          'redirect_uri': f'{self.url_apps}/directory/'})
            destino = r.headers.get('location')
            if r.status_code not in (301, 302, 303) or not destino:
                raise ErrorGenesys(f'Login Genesys: /oauth/authorize respondió {r.status_code} sin redirect')

            pagina = s.get(urllib.parse.urljoin(self.url_login, destino), timeout=self.timeout)
            csrf = re.search(r'<meta name="csrf" content="([^"]+)"', pagina.text)
            rid = re.search(r'<meta name="requestId" content="([^"]+)"', pagina.text)
            if not csrf or not rid:
                raise ErrorGenesys('Login Genesys: la página de login no trae csrf/requestId '
                                   '(¿cambió el login de Genesys?)')
            headers = {
                'ININ-CSRF-TOKEN': csrf.group(1),
                'ININ-Auth-Request-Id': rid.group(1),
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            }

            r = s.post(f'{self.url_login}/login', headers=headers, timeout=self.timeout,
                       data=json.dumps({'username': self.usuario, 'password': self._clave,
                                        'orgName': None, 'lang': 'es'}))
            if r.status_code == 401:
                raise ErrorGenesys('Login Genesys: usuario o contraseña incorrectos')
            if r.status_code == 429:
                raise ErrorGenesys('Login Genesys: cuenta bloqueada por intentos fallidos '
                                   '(Genesys la libera a los 5 minutos)')
            if r.status_code != 200:
                raise ErrorGenesys(f'Login Genesys: /login respondió {r.status_code}')
            sesion = r.json() if r.content else {}
            if sesion.get('verificationRequired'):
                raise ErrorGenesys('Login Genesys: la cuenta pide MFA; el login automático no lo soporta')
            if (sesion.get('rob') or {}).get('accepted') is False:
                raise ErrorGenesys('Login Genesys: hay que aceptar las reglas de uso entrando '
                                   'una vez a mano con la cuenta')

            r = s.put(f'{self.url_login}/request/{urllib.parse.quote(rid.group(1))}',
                      headers=headers, timeout=self.timeout,
                      data=json.dumps({'isEmbedded': False, 'state': 'pending'}))
            if r.status_code != 200:
                raise ErrorGenesys(f'Login Genesys: /request respondió {r.status_code}')
            aprobado = r.json()
            fragmento = urllib.parse.parse_qs(
                urllib.parse.urlparse(aprobado.get('redirect') or '').fragment)
            token = (fragmento.get('access_token') or [None])[0]
            if aprobado.get('status') != 'approved' or not token:
                raise ErrorGenesys(f"Login Genesys: el pedido quedó '{aprobado.get('status')}' sin token")
            segundos = int((fragmento.get('expires_in') or ['86400'])[0])
            return token, datetime.now(timezone.utc) + timedelta(seconds=segundos)
        except requests.RequestException as e:
            raise ErrorGenesys(f'Login Genesys: error de red ({type(e).__name__}: {e})') from e
        finally:
            s.close()

    # -------------------------------------------------------------- pedidos
    def _pedir(self, metodo: str, ruta: str, reintentos: int = 4, **kw) -> requests.Response:
        """Pedido a la API con el token. 401 -> relogin una vez; 429/5xx -> espera y
        reintenta (Genesys manda Retry-After en el 429)."""
        relogueado = False
        for intento in range(reintentos + 1):
            headers = {'Authorization': f'Bearer {self.login()}',
                       'Content-Type': 'application/json'}
            try:
                r = self.http.request(metodo, f'{self.url_api}{ruta}', headers=headers,
                                      timeout=self.timeout, **kw)
            except requests.RequestException:
                if intento == reintentos:
                    raise
                time.sleep(2 ** intento)
                continue
            if r.status_code == 401 and not relogueado:
                relogueado = True
                self.login(forzar=True)
                continue
            if (r.status_code == 429 or r.status_code >= 500) and intento < reintentos:
                espera = r.headers.get('Retry-After')
                time.sleep(float(espera) if espera and espera.isdigit() else 2 ** intento)
                continue
            return r
        return r

    def _get_json(self, ruta: str, **params) -> Any:
        r = self._pedir('GET', ruta, params=params)
        if r.status_code != 200:
            raise ErrorGenesys(f'GET {ruta} respondió {r.status_code}: {r.text[:200]}')
        return r.json()

    def _paginar(self, ruta: str, **params) -> List[dict]:
        entidades, pagina = [], 1
        while True:
            j = self._get_json(ruta, pageSize=100, pageNumber=pagina, **params)
            entidades += j.get('entities') or []
            if pagina >= (j.get('pageCount') or 1):
                return entidades
            pagina += 1

    # ------------------------------------------------------------ catálogos
    def usuarios(self) -> Dict[str, dict]:
        """id -> {name, email}, incluidos los usuarios dados de baja (un llamado viejo
        puede ser de alguien que ya no está)."""
        return {u['id']: {'name': u.get('name'), 'email': u.get('email')}
                for u in self._paginar('/api/v2/users', state='any')}

    def colas(self) -> Dict[str, str]:
        """id -> nombre. Por divisionviews: con /routing/queues la cuenta no ve las colas
        de la división ACME (Saliente_Acme da 403) y los salientes quedarían sin cola."""
        return {q['id']: q.get('name') for q in self._paginar('/api/v2/routing/queues/divisionviews/all')}

    def tipificaciones(self) -> Dict[str, str]:
        """id -> nombre de los códigos de wrap-up (la "Conclusión" del llamado)."""
        return {w['id']: w.get('name') for w in self._paginar('/api/v2/routing/wrapupcodes')}

    def skills(self) -> Dict[str, str]:
        return {s['id']: s.get('name') for s in self._paginar('/api/v2/routing/skills')}

    # ------------------------------------------------------------ analytics
    def conversaciones(self, desde: datetime, hasta: datetime,
                       filtros_segmento: Optional[List[dict]] = None) -> List[dict]:
        """Detalle de conversaciones del intervalo [desde, hasta) (datetimes con tz).

        Ojo: analytics devuelve las conversaciones que SE SUPERPONEN con el intervalo,
        no solo las que empiezan adentro (un llamado que cruza la medianoche aparece en
        los dos días). Quien carga por día tiene que filtrar por conversationStart.
        """
        if hasta - desde > timedelta(days=MAX_DIAS_POR_CONSULTA):
            raise ValueError(f'Genesys no acepta intervalos de más de {MAX_DIAS_POR_CONSULTA} días')
        intervalo = (f"{desde.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%S.000Z}/"
                     f"{hasta.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%S.000Z}")
        conversaciones, pagina = [], 1
        while True:
            cuerpo = {'order': 'asc', 'orderBy': 'conversationStart', 'interval': intervalo,
                      'paging': {'pageSize': TAM_PAGINA, 'pageNumber': pagina}}
            if filtros_segmento:
                cuerpo['segmentFilters'] = filtros_segmento
            r = self._pedir('POST', '/api/v2/analytics/conversations/details/query',
                            data=json.dumps(cuerpo))
            if r.status_code != 200:
                raise ErrorGenesys(f'analytics respondió {r.status_code}: {r.text[:300]}')
            lote = r.json().get('conversations') or []
            conversaciones += lote
            if len(lote) < TAM_PAGINA:
                return conversaciones
            pagina += 1

    # --------------------------------------------------------------- audios
    def descargar_audio(self, conversation_id: str, formato: str = 'WAV',
                        espera_max: int = 90) -> Optional[io.BytesIO]:
        """Audio de la conversación, o None si no tiene grabación o no se pudo bajar.

        Si la conversación tuviera más de una grabación de audio (no pasó en ninguna
        de las muestras) se queda con la más larga, que es la del tramo atendido.
        """
        limite = time.time() + espera_max
        while True:
            r = self._pedir('GET', f'/api/v2/conversations/{conversation_id}/recordings',
                            params={'formatId': formato, 'maxWaitMs': 10000})
            if r.status_code == 202 and time.time() < limite:
                time.sleep(2)  # Genesys todavía está transcodificando
                continue
            break
        if r.status_code != 200:
            if r.status_code not in (404,):
                print(f'Genesys: grabación de {conversation_id} respondió {r.status_code}: {r.text[:150]}')
            return None

        def _duracion(g: dict) -> float:
            try:
                ini = datetime.fromisoformat(g['startTime'].replace('Z', '+00:00'))
                fin = datetime.fromisoformat(g['endTime'].replace('Z', '+00:00'))
                return (fin - ini).total_seconds()
            except (KeyError, TypeError, ValueError):
                return 0.0

        grabaciones = [g for g in r.json() or []
                       if g.get('media') == 'audio' and g.get('mediaUris')]
        if not grabaciones:
            return None
        grabacion = max(grabaciones, key=_duracion)
        partes = grabacion['mediaUris']
        if len(partes) > 1:
            print(f'Genesys: {conversation_id} trae {len(partes)} partes de audio; se usa la primera')
        url = (partes.get('0') or next(iter(partes.values()))).get('mediaUri')
        if not url:
            return None
        for intento in range(3):
            try:
                archivo = requests.get(url, timeout=self.timeout)  # URL firmada: sin token
                if archivo.status_code == 200 and archivo.content:
                    return io.BytesIO(archivo.content)
            except requests.RequestException:
                pass
            time.sleep(2 ** intento)
        print(f'Genesys: no se pudo bajar el archivo de {conversation_id}')
        return None

    def descargar_audios(self, conversation_ids: Iterable[str], workers: int = 4) -> Dict[str, Optional[io.BytesIO]]:
        """conversationId -> BytesIO (None si falló). En paralelo: casi todo el tiempo
        es espera de la transcodificación de Genesys, no ancho de banda."""
        ids = list(dict.fromkeys(str(i) for i in conversation_ids if i))
        audios: Dict[str, Optional[io.BytesIO]] = {}
        if not ids:
            return audios
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(ids)))) as ex:
            futuros = {ex.submit(self.descargar_audio, cid): cid for cid in ids}
            for futuro in as_completed(futuros):
                cid = futuros[futuro]
                try:
                    audios[cid] = futuro.result()
                except Exception as e:  # noqa: BLE001 - un audio roto no tira la corrida
                    print(f'Genesys: error bajando {cid}: {e}')
                    audios[cid] = None
        return audios
