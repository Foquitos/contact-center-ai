/* ============================================================================
   Cambio de configuracion — Planificador: la malla mide solo campanas telefonicas
   Fecha: 2026-09-10 (posterior a 2026-09-09e_planificador_puestos_malla.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   Deja en planificacion.PoolOrigen SOLO las sub-campanas telefonicas. No agrega
   ni saca columnas ni tablas: borra 10 de las 14 filas del mapeo pool -> RRHH.

       quedan (4)                            salen (10)
       100  T1 - Telefono                     53  Digital
        54  T2T3 - Telefono                  165  Ajustes - Lecturas
       106  T1 - Emergencias                 131  BackOffice
       177  T1 - Consumo                     139  Agrupadas - Digital
                                              89  BackOffice Electro
                                             146  Gestion SVP
                                              58  Backoffice RRSS
                                              64  Artefactos Danados
                                             132  T2T3 - Digital
                                             178  Contingencia

   POR QUE
   -------
   Instruccion de la operacion (Ignacio, 2026-09-10): "en el planificador de
   operadores solo tienen que aparecer los que estan en una campana telefonica,
   ya que es lo que vamos a medir".

   Y ademas cierra una diferencia que se veia en pantalla. Contra el tablero
   intradia de la operacion del 2026-09-09, 00:00 a 14:00, solo operadores, con
   el doble conteo y el filtro de puesto YA corregidos:

       alcance                        citados   pico   conectados   pico
       las 14 sub-campanas del pool     1.400    116        1.587    138
       solo las telefonicas               572     50          751     71
       el tablero de la operacion         596    ~57          630    ~62

   O sea que el tablero que mira Planificacion SON las telefonicas, y el pool
   estaba mirando un universo 2,4 veces mas grande. Horas de piso de los ultimos
   30 dias cerrados: 9.544 h telefonicas contra 14.219 h que no lo son. Las otras
   diez sub-campanas se loguean en el ACD pero hacen back office.

   QUE SE PIERDE, DICHO EN VOZ ALTA
   --------------------------------
   El pool se habia ensanchado a proposito, y el motivo era real: gente de
   Digital y BackOffice tambien atiende telefono. Medido el 2026-09-09, en el
   pico habia 71 personas conectadas de sub-campanas telefonicas contra 50
   citadas, o sea que alguien mas esta atendiendo. Esas manos siguen contando
   donde corresponde —el factor de Disponibilidad se mide invirtiendo el NDS
   observado sobre el informe de skills, que cuenta a QUIEN ATENDIO sin mirar su
   sub-campana— pero ya no cuentan como dotacion citada. La brecha contra la
   malla va a verse algo peor que la realidad los dias en que el back office
   ayuda mucho. Es el precio de que el numero sea comparable con el tablero.

   OJO: LA JUSTIFICACION VIEJA ESTABA MEDIDA CON UN BUG ADENTRO
   ------------------------------------------------------------
   El comentario de `campanas_rrhh_del_pool` decia que filtrar por las
   telefonicas daba "42 planificados en el pico contra 57 personas realmente
   atendiendo". Ese 42 salio de `dotacion_planificada`, que hasta el 2026-09-10
   contaba cada turno pasado DOS VECES (payroll y payroll_futuro tienen todos los
   dias y el UNION ALL no tenia corte). O sea que la comparacion que ensancho el
   pool no es reproducible. Queda anotado para que nadie lo revierta citandola.

   EL SHRINKAGE CAMBIA CON ESTO, Y HAY QUE VOLVER A MEDIRLO
   --------------------------------------------------------
   Se mide sobre las mismas horas de payroll, asi que angostar el pool lo mueve.
   90 dias cerrados, solo operadores:

       alcance                        programadas   ausentismo   capac.   shrinkage
       las 14 sub-campanas                 70.836        3,73%    1,06%      4,79%
       solo las telefonicas                28.410        6,52%    2,50%      9,02%

   Los telefonicos faltan casi el doble que el back office, asi que el numero
   correcto para este pool es 9,0% y no 4,8%. El vigente sigue siendo 0,272, que
   no describe ninguno de los dos. Esta migracion NO lo toca: que un valor medido
   reemplace al vigente sigue siendo el boton "Dejar vigentes los valores
   medidos" de la pantalla de calibracion, con los dos numeros al lado.

   REVERSIBLE
   ----------
   Al final del script quedan comentados los INSERT que devuelven las diez filas
   con su nota original, por si hay que volver atras.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* --------------------------------------------------- como esta hoy (antes) */
PRINT '--- PoolOrigen ANTES ---';
SELECT o.PoolID, o.CampanaRRHHID, c.sub_campana, o.Nota
FROM planificacion.PoolOrigen o
LEFT JOIN dbo.campanas c ON c.id = o.CampanaRRHHID
ORDER BY o.PoolID, c.sub_campana;
GO

/* Las telefonicas se enumeran explicitamente y NO se filtra por el nombre.
   Filtrar por "%Telefono%" dejaria afuera a T1 - Emergencias y a T1 - Consumo, y
   dejaria entrar a sub-campanas telefonicas viejas que ya no tienen gente
   (17 Canales Telefonicos, 52 Telefono T1 - Emergencias, 111 Telefono T1 -
   Comercial, 94 T2-T3: cero horas de piso en los ultimos 30 dias cerrados). La
   lista es una decision, no una coincidencia de texto. */
DECLARE @sobran int = (SELECT COUNT(*) FROM planificacion.PoolOrigen
                       WHERE CampanaRRHHID NOT IN (100, 54, 106, 177));

IF @sobran = 0
    PRINT 'PoolOrigen ya tenia solo las telefonicas: no hay nada que hacer.';
ELSE IF @sobran <> 10
    /* Si el mapeo no es el que se midio, no se borra a ciegas: alguien lo edito
       desde la pantalla y hay que mirarlo antes. */
    PRINT 'ATENCION: sobran ' + CAST(@sobran AS varchar(10)) + ' filas y se esperaban 10. '
        + 'NO se borro nada. Revisar el listado de arriba antes de correr esto.';
ELSE
BEGIN
    BEGIN TRANSACTION;
    DELETE FROM planificacion.PoolOrigen
    WHERE CampanaRRHHID NOT IN (100, 54, 106, 177);
    /* La nota es lo unico que explica, dentro de la base, por que el mapeo es
       este. Sin esto queda una lista de cuatro ids sin procedencia. */
    UPDATE planificacion.PoolOrigen
    SET Nota = N'Campana telefonica. Alcance fijado el 2026-09-10 por instruccion '
             + N'de la operacion: la malla mide solo campanas telefonicas.'
    WHERE CampanaRRHHID IN (100, 54, 106, 177);
    COMMIT TRANSACTION;
    PRINT 'PoolOrigen recortado a las sub-campanas telefonicas.';
END
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
PRINT '--- PoolOrigen DESPUES ---';
SELECT o.PoolID, o.CampanaRRHHID, c.sub_campana, o.Nota
FROM planificacion.PoolOrigen o
LEFT JOIN dbo.campanas c ON c.id = o.CampanaRRHHID
ORDER BY o.PoolID, c.sub_campana;
GO

/* Horas de piso de los ultimos 30 dias cerrados, solo operadores, para ver el
   tamano del universo que queda. Necesita la 2026-09-09e aplicada. */
IF OBJECT_ID('planificacion.PuestoMalla', 'U') IS NULL
    PRINT 'Sin planificacion.PuestoMalla (falta la 2026-09-09e): se omite el resumen de horas.';
ELSE
SELECT c.sub_campana,
       COUNT(DISTINCT p.id_operadores) AS personas,
       SUM(CASE WHEN cp.Clase = 'piso' THEN ISNULL(p.horas_programadas, 0) ELSE 0 END) AS horas_piso
FROM dbo.payroll p
JOIN dbo.operadores o ON o.id = p.id_operadores
JOIN dbo.campanas c   ON c.id = o.campana_id
LEFT JOIN planificacion.CodigoPayroll cp ON cp.Codigo = p.codigo
WHERE p.fecha >= DATEADD(day, -30, CAST(GETDATE() AS date))
  AND p.fecha <  CAST(GETDATE() AS date)
  AND o.campana_id IN (SELECT CampanaRRHHID FROM planificacion.PoolOrigen)
  AND o.puesto_id IN (SELECT PuestoID FROM planificacion.PuestoMalla WHERE EnMalla = 1)
GROUP BY c.sub_campana
ORDER BY horas_piso DESC;
GO

/* ====================================================================
   PARA VOLVER ATRAS (no se ejecuta)
   ====================================================================
INSERT INTO planificacion.PoolOrigen (PoolID, CampanaRRHHID, Nota) VALUES
    (1,  53, N'Digital (13) - tambien atienden telefono'),
    (1,  58, N'Backoffice RRSS (8) - tambien atienden telefono'),
    (1,  64, N'Artefactos Danados (6)'),
    (1,  89, N'BackOffice Electro (7)'),
    (1, 131, N'BackOffice (3)'),
    (1, 132, N'T2T3 - Digital (4)'),
    (1, 139, N'Agrupadas - Digital (7)'),
    (1, 146, N'Gestion SVP (1)'),
    (1, 165, N'Ajustes - Lecturas (1)'),
    (1, 178, N'Contingencia (4)');
   ==================================================================== */
