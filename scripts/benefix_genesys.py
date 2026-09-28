"""
Llamados de Benefix (Genesys Cloud) — carga a SQL Server para las auditorías.

QUÉ HACE
--------
Baja de la API de analytics de Genesys (`POST /api/v2/analytics/conversations/details/query`)
las conversaciones de cada día y guarda en Benefix.Interacciones una fila por TRAMO DE
AGENTE (conversación + participante agente), con operador, cola, tipificación (wrap-up),
tiempos y si quedó grabada. Es la base de la que el builder de auditorías
(AuditorIA/SQL_query.get_filtered_data_Benefix) elige los llamados; el audio se baja
recién al auditar (AuditorIA/downloads/Genesys.py). Migración:
scripts/migrations/2026-09-21_benefix_genesys.sql.

    diario     — recarga hoy y los N días anteriores (default 1: ayer + hoy). Reemplaza el
                 día entero, así que los wrap-ups que se cargan tarde y los llamados que
                 seguían en curso en la corrida anterior entran solos.
    historico  — recorre un rango día por día; reanudable (anota cada día en
                 Benefix.InteraccionesCargas y saltea los que ya están OK).
    estado     — qué días hay cargados.

POR QUÉ DÍA POR DÍA
-------------------
Analytics no acepta intervalos de más de 7 días, y un día de Benefix son ~400
conversaciones (4-5 páginas de 100, ~1,5 s). Con la unidad en el día, cada día queda
commiteado solo y una corrida cortada no pierde nada.

Ojo con el borde: analytics devuelve las conversaciones que SE SUPERPONEN con el
intervalo, no solo las que empiezan adentro (13 de 2.583 en la semana medida cruzaban la
medianoche). Cada conversación se asigna al día local de su conversationStart.

QUÉ QUEDA AFUERA
----------------
Las conversaciones sin agente (IVR, abandonos): no hay nada que auditar. Se guardan voz
y mensajería (los WhatsApp los atiende gente de Benefix, no de Acme, pero cuesta nada
tenerlos); el builder de auditorías filtra voz de operadores de Acme.

Credenciales: GENESYS_USER / GENESYS_PASS en el .env (ver app/config.py).

Ejemplos:
    python scripts/benefix_genesys.py diario
    python scripts/benefix_genesys.py diario --dias-atras 3
    python scripts/benefix_genesys.py historico --desde 2026-07-01 --hasta 2026-09-20
    python scripts/benefix_genesys.py estado
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend'))

TZ_LOCAL = ZoneInfo('America/Argentina/Buenos_Aires')
TABLA = 'Benefix.Interacciones'
TABLA_CARGAS = 'Benefix.InteraccionesCargas'
REINTENTOS_DIA = 3

SENTIDO = {'inbound': 'Entrante', 'outbound': 'Saliente'}

# Columna -> largo máximo (None = no es texto). Mismo orden que el INSERT.
COLUMNAS: List[tuple] = [
    ('ConversationId', 36), ('ParticipantId', 36), ('Fecha', None),
    ('InicioConversacion', None), ('FinConversacion', None),
    ('InicioAgente', None), ('FinAgente', None),
    ('Canal', 20), ('Sentido', 10), ('UserId', 36), ('Operador', 200), ('Email', 200),
    ('QueueId', 36), ('Cola', 200), ('Skills', 1000),
    ('WrapUpCodeId', 36), ('Tipificacion', 300), ('NotaWrapUp', 4000),
    ('TelefonoCliente', 64), ('ANI', 200), ('DNIS', 200),
    ('SegundosAlerta', None), ('SegundosHablados', None), ('SegundosEspera', None),
    ('CantidadEsperas', None), ('SegundosACW', None), ('SegundosManejo', None),
    ('Transferido', None), ('DesconexionAgente', 30), ('Grabada', None),
    ('AgentesEnConversacion', None),
    # Migración 2026-09-21b: lo que el cliente marcó en el IVR (dato de la conversación).
    ('ExternalTag', 64),
]
NOMBRES = [c for c, _ in COLUMNAS]


# ==========================================
# TRANSFORMACIÓN (sin red ni base: la cubre tests/test_benefix_genesys.py)
# ==========================================

def _instante(txt: Optional[str]) -> Optional[dt.datetime]:
    if not txt:
        return None
    return dt.datetime.fromisoformat(txt.replace('Z', '+00:00'))


def _local(instante: Optional[dt.datetime]) -> Optional[dt.datetime]:
    """Hora local sin tz y sin milisegundos, que es como la guarda SQL."""
    if instante is None:
        return None
    return instante.astimezone(TZ_LOCAL).replace(tzinfo=None, microsecond=0)


def _sin_prefijo(direccion: Optional[str]) -> Optional[str]:
    if not direccion:
        return None
    for prefijo in ('tel:', 'sip:'):
        if direccion.startswith(prefijo):
            return direccion[len(prefijo):]
    return direccion


def _tramo_agente(participante: dict) -> Dict[str, Any]:
    """Resume las sesiones de un participante agente (casi siempre una sola)."""
    sesiones = participante.get('sessions') or []
    # Si un agente tuvo voz y mensaje en la misma conversación, manda la voz.
    principal = next((s for s in sesiones if s.get('mediaType') == 'voice'), sesiones[0] if sesiones else {})
    segmentos = sorted((sg for s in sesiones for sg in (s.get('segments') or [])),
                       key=lambda sg: sg.get('segmentStart') or '')

    segundos = {'alert': 0.0, 'interact': 0.0, 'hold': 0.0, 'wrapup': 0.0}
    esperas = 0
    for sg in segmentos:
        ini, fin = _instante(sg.get('segmentStart')), _instante(sg.get('segmentEnd'))
        tipo = sg.get('segmentType')
        if tipo == 'hold':
            esperas += 1
        if tipo in segundos and ini and fin:
            segundos[tipo] += (fin - ini).total_seconds()

    cola = next((sg['queueId'] for sg in segmentos if sg.get('queueId')), None)
    wrapups = [sg for sg in segmentos if sg.get('wrapUpCode')]
    notas = [sg['wrapUpNote'] for sg in segmentos if sg.get('wrapUpNote')]
    interacciones = [sg for sg in segmentos if sg.get('segmentType') == 'interact']
    skills: List[str] = []
    for sg in segmentos:
        for sk in (sg.get('requestedRoutingSkillIds') or []):
            if sk not in skills:
                skills.append(sk)
    if not skills:
        for s in sesiones:
            for sk in (s.get('activeSkillIds') or []):
                if sk not in skills:
                    skills.append(sk)

    transferido = any(
        m.get('name') in ('nTransferred', 'nBlindTransferred', 'nConsultTransferred') and m.get('value')
        for s in sesiones for m in (s.get('metrics') or [])
    ) or any(sg.get('disconnectType') == 'transfer' for sg in interacciones)

    inicios = [_instante(sg.get('segmentStart')) for sg in segmentos if sg.get('segmentStart')]
    fines = [_instante(sg.get('segmentEnd')) for sg in segmentos if sg.get('segmentEnd')]
    return {
        'canal': principal.get('mediaType'),
        'sentido': SENTIDO.get(principal.get('direction'), principal.get('direction')),
        'ani': principal.get('ani'),
        'dnis': principal.get('dnis'),
        'queue_id': cola,
        'skill_ids': skills,
        'wrapup_id': wrapups[-1]['wrapUpCode'] if wrapups else None,
        'nota': ' | '.join(notas) if notas else None,
        'inicio': min(inicios) if inicios else None,
        'fin': max(fines) if fines else None,
        'alerta': round(segundos['alert']),
        'hablado': round(segundos['interact']),
        'espera': round(segundos['hold']),
        'esperas': esperas,
        'acw': round(segundos['wrapup']),
        'transferido': transferido,
        'desconexion': interacciones[-1].get('disconnectType') if interacciones else None,
    }


def filas_del_dia(conversaciones: List[dict], dia: dt.date, catalogos: Dict[str, dict]) -> List[tuple]:
    """Filas de Benefix.Interacciones (en el orden de COLUMNAS) de las conversaciones que
    EMPIEZAN el día local `dia`. `catalogos` = {'usuarios', 'colas', 'tipificaciones',
    'skills'} tal como los devuelve el cliente Genesys."""
    usuarios = catalogos.get('usuarios') or {}
    colas = catalogos.get('colas') or {}
    tipificaciones = catalogos.get('tipificaciones') or {}
    skills = catalogos.get('skills') or {}

    filas, vistas = [], set()
    for c in conversaciones:
        inicio = _local(_instante(c.get('conversationStart')))
        if inicio is None or inicio.date() != dia:
            continue
        agentes = [p for p in (c.get('participants') or []) if p.get('purpose') == 'agent']
        if not agentes:
            continue
        grabada = any(s.get('recording') for p in c.get('participants') or [] for s in p.get('sessions') or [])
        fin = _local(_instante(c.get('conversationEnd')))

        for p in agentes:
            clave = (c['conversationId'], p.get('participantId'))
            if clave in vistas:
                continue
            vistas.add(clave)
            t = _tramo_agente(p)
            usuario = usuarios.get(p.get('userId')) or {}
            telefono = t['ani'] if t['sentido'] == 'Entrante' else t['dnis']
            valores = {
                'ConversationId': c['conversationId'],
                'ParticipantId': p.get('participantId'),
                'Fecha': dia,
                'InicioConversacion': inicio,
                'FinConversacion': fin,
                'InicioAgente': _local(t['inicio']),
                'FinAgente': _local(t['fin']),
                'Canal': t['canal'] or 'desconocido',
                'Sentido': t['sentido'],
                'UserId': p.get('userId'),
                'Operador': usuario.get('name'),
                'Email': usuario.get('email'),
                'QueueId': t['queue_id'],
                'Cola': colas.get(t['queue_id']) if t['queue_id'] else None,
                'Skills': ', '.join(skills.get(s, s) for s in t['skill_ids']) or None,
                'WrapUpCodeId': t['wrapup_id'],
                'Tipificacion': tipificaciones.get(t['wrapup_id']) if t['wrapup_id'] else None,
                'NotaWrapUp': t['nota'],
                'TelefonoCliente': _sin_prefijo(telefono),
                'ANI': t['ani'],
                'DNIS': t['dnis'],
                'SegundosAlerta': t['alerta'],
                'SegundosHablados': t['hablado'],
                'SegundosEspera': t['espera'],
                'CantidadEsperas': t['esperas'],
                'SegundosACW': t['acw'],
                'SegundosManejo': t['hablado'] + t['espera'] + t['acw'],
                'Transferido': bool(t['transferido']),
                'DesconexionAgente': t['desconexion'],
                'Grabada': bool(grabada),
                'AgentesEnConversacion': min(len(agentes), 255),
                'ExternalTag': c.get('externalTag'),
            }
            fila = []
            for nombre, largo in COLUMNAS:
                v = valores[nombre]
                if largo and isinstance(v, str):
                    v = v[:largo]
                fila.append(v)
            filas.append(tuple(fila))
    return filas


def intervalo_del_dia(dia: dt.date) -> tuple:
    """[00:00, 24:00) local del día, con tz (analytics trabaja en UTC)."""
    desde = dt.datetime.combine(dia, dt.time.min, tzinfo=TZ_LOCAL)
    return desde, desde + dt.timedelta(days=1)


# ==========================================
# BASE
# ==========================================

def cargar_dia(engine, dia: dt.date, filas: List[tuple]) -> float:
    """Reemplaza el día entero en una transacción. Sin filas no borra nada: si la API
    contesta vacío por un hipo suyo, mejor dejar el día como estaba."""
    if not filas:
        return 0.0
    t0 = time.time()
    with engine.begin() as cx:
        cursor = cx.connection.driver_connection.cursor()
        cursor.fast_executemany = True
        cursor.execute(f'DELETE FROM {TABLA} WHERE Fecha = ?', [dia])
        cursor.executemany(
            f'INSERT INTO {TABLA} ({", ".join(NOMBRES)}) VALUES ({", ".join("?" * len(NOMBRES))})',
            filas)
        cursor.close()
    return time.time() - t0


def anotar_carga(engine, dia: dt.date, estado: str, conversaciones: Optional[int] = None,
                 filas: Optional[int] = None, segundos: Optional[float] = None,
                 error: Optional[str] = None) -> None:
    from sqlalchemy import text
    with engine.begin() as cx:
        cx.execute(text(f"""
            MERGE {TABLA_CARGAS} AS destino
            USING (SELECT :dia AS Dia) AS origen ON destino.Dia = origen.Dia
            WHEN MATCHED THEN UPDATE SET
                Estado = :estado, Conversaciones = :conv, Filas = :filas, Segundos = :seg,
                Intentos = destino.Intentos + 1, Error = :error, FechaCarga = SYSDATETIME()
            WHEN NOT MATCHED THEN INSERT (Dia, Estado, Conversaciones, Filas, Segundos, Error)
                VALUES (:dia, :estado, :conv, :filas, :seg, :error);
        """), {'dia': dia, 'estado': estado, 'conv': conversaciones, 'filas': filas,
               'seg': round(segundos, 1) if segundos is not None else None,
               'error': error[:1000] if error else None})


def dias_ya_cargados(engine, desde: dt.date, hasta: dt.date) -> set:
    from sqlalchemy import text
    with engine.connect() as cx:
        return {f[0] for f in cx.execute(text(
            f"SELECT Dia FROM {TABLA_CARGAS} WHERE Estado IN ('OK', 'VACIO') "
            f"AND Dia BETWEEN :d AND :h"), {'d': desde, 'h': hasta}).fetchall()}


def mostrar_estado(engine) -> None:
    from sqlalchemy import text
    with engine.connect() as cx:
        for f in cx.execute(text(f"""
            SELECT Estado, COUNT(*) dias, SUM(Filas) filas, MIN(Dia) desde, MAX(Dia) hasta
            FROM {TABLA_CARGAS} GROUP BY Estado ORDER BY Estado""")).fetchall():
            print(f'{f.Estado:6} {f.dias:5} días  {f.filas or 0:>8} filas  {f.desde} .. {f.hasta}')
        t = cx.execute(text(f"""
            SELECT COUNT(*) filas, MIN(Fecha) desde, MAX(Fecha) hasta,
                   SUM(CASE WHEN Canal = 'voice' AND Grabada = 1 THEN 1 ELSE 0 END) voz_grabada
            FROM {TABLA}""")).first()
        print(f'{TABLA}: {t.filas} filas ({t.voz_grabada} de voz grabadas), {t.desde} .. {t.hasta}')


# ==========================================
# PROCESO
# ==========================================

def procesar_dia(genesys, engine, dia: dt.date, catalogos: Dict[str, dict]) -> Dict[str, Any]:
    desde, hasta = intervalo_del_dia(dia)
    t0 = time.time()
    conversaciones = genesys.conversaciones(desde, hasta)
    filas = filas_del_dia(conversaciones, dia, catalogos)
    seg_insertar = cargar_dia(engine, dia, filas)
    return {'estado': 'OK' if filas else 'VACIO', 'conversaciones': len(conversaciones),
            'filas': len(filas), 'segundos': time.time() - t0, 'seg_insertar': seg_insertar}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description='Llamados de Benefix (Genesys Cloud) a SQL Server',
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='modo', required=True)
    d = sub.add_parser('diario', help='recarga hoy y los últimos días')
    d.add_argument('--dias-atras', type=int, default=1)
    h = sub.add_parser('historico', help='rango largo, reanudable')
    h.add_argument('--desde', type=dt.date.fromisoformat, required=True)
    h.add_argument('--hasta', type=dt.date.fromisoformat,
                   default=dt.date.today() - dt.timedelta(days=1))
    h.add_argument('--forzar', action='store_true', help='rehace también los días ya cargados')
    sub.add_parser('estado', help='qué días están cargados')
    args = p.parse_args(argv)

    from sqlalchemy import create_engine
    from app.config import settings
    engine = create_engine(settings.connection_string, fast_executemany=True)

    if args.modo == 'estado':
        mostrar_estado(engine)
        return 0

    hoy = dt.datetime.now(TZ_LOCAL).date()
    if args.modo == 'diario':
        dias = [hoy - dt.timedelta(days=n) for n in range(args.dias_atras, -1, -1)]
    else:
        dias = [args.desde + dt.timedelta(days=n) for n in range((args.hasta - args.desde).days + 1)]
        if not args.forzar:
            ya = dias_ya_cargados(engine, args.desde, args.hasta)
            print(f'{len(dias)} días en el rango, {len(ya)} ya cargados.')
            dias = [x for x in dias if x not in ya]
    if not dias:
        print('No hay días para procesar.')
        return 0

    from AuditorIA.downloads.Genesys import Genesys
    t0 = time.time()
    total = {'ok': 0, 'vacios': 0, 'error': 0, 'filas': 0}
    with Genesys(settings.GENESYS_USER, settings.GENESYS_PASS, settings.GENESYS_REGION) as genesys:
        catalogos = {'usuarios': genesys.usuarios(), 'colas': genesys.colas(),
                     'tipificaciones': genesys.tipificaciones(), 'skills': genesys.skills()}
        print(f'Login y catálogos OK en {time.time() - t0:.1f}s '
              f'({len(catalogos["usuarios"])} usuarios, {len(catalogos["tipificaciones"])} wrap-ups). '
              f'{len(dias)} día(s): {dias[0]} .. {dias[-1]}')
        for dia in dias:
            for intento in range(1, REINTENTOS_DIA + 1):
                try:
                    r = procesar_dia(genesys, engine, dia, catalogos)
                    anotar_carga(engine, dia, r['estado'], r['conversaciones'], r['filas'], r['segundos'])
                    total['ok' if r['estado'] == 'OK' else 'vacios'] += 1
                    total['filas'] += r['filas']
                    print(f'{dia}  {r["estado"]:5}  {r["conversaciones"]:4} conversaciones  '
                          f'{r["filas"]:4} tramos de agente  {r["segundos"]:.1f}s')
                    break
                except Exception as e:  # noqa: BLE001 - se anota y sigue con el día siguiente
                    if intento < REINTENTOS_DIA:
                        time.sleep(5 * intento)
                        continue
                    total['error'] += 1
                    anotar_carga(engine, dia, 'ERROR', error=f'{type(e).__name__}: {e}')
                    print(f'{dia}  ERROR  {type(e).__name__}: {e}')

    print(f'Listo en {time.time() - t0:.0f}s — {total["ok"]} días con datos, {total["vacios"]} vacíos, '
          f'{total["error"]} con error, {total["filas"]} filas.')
    return 1 if total['error'] else 0


if __name__ == '__main__':
    sys.exit(main())
