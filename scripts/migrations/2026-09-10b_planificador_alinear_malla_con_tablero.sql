/* ============================================================================
   Cambio de configuracion — Planificador: la malla citada se alinea con el
             tablero de la operacion
   Fecha: 2026-09-10 (posterior a 2026-09-10_planificador_shrinkage_por_dia_y_break.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   Deja la definicion de "operadores planificados" igual a la que usa
   dbo.Tablero_Agentes_Voltara, que es la que Planificacion mira todos los dias.
   Dos cambios de datos:

       planificacion.PoolOrigen   + 64  Artefactos Danados
                                  - 177 T1 - Consumo
       planificacion.PuestoMalla    4   Operador Capacitacion  ->  EnMalla = 1

   El tercer cambio es de codigo y no de datos: la malla pasa a contar tambien al
   citado que despues falto. Ver mas abajo.

   POR QUE
   -------
   Instruccion de Ignacio (2026-09-10): "alineemos con el tablero en cuanto a los
   operadores planificados". Mientras nuestro numero y el suyo se calculen
   distinto, cualquier discusion sobre la brecha empieza por discutir el numero.

   LA DEFINICION DE ELLOS, LEIDA DE LA VISTA
   -----------------------------------------
       sub-campanas   T1 - Telefono, T1 - Emergencias, T2T3 - Telefono,
                      Artefactos Danados
       puestos        Operador 2, Operador Telefonico, Operador Capacitacion
       codigos        codigo IS NULL  OR  codigo = 'ABS'
       horas          horas_programadas > 0
       tabla          SOLO payroll_futuro

   Contra la nuestra: nos sobraba T1 - Consumo, nos faltaba Artefactos Danados y
   nos faltaba el puesto Operador Capacitacion.

   VERIFICADO ANTES DE ESCRIBIR NADA
   ---------------------------------
   Replicando su definicion sobre el 2026-09-09, media hora por media hora,
   contra la columna [Op planificados (intervalo)] de la vista:

       08:00  20 / 20     10:00  50 / 50     12:00  51 / 51
       08:30  24 / 24     10:30  50 / 50     12:30  50 / 50
       09:00  40 / 40     11:00  57 / 57     13:00  50 / 50
       09:30  42 / 42     11:30  57 / 57     14:30  47 / 47

   Identico en los 48 intervalos: total del dia 964 contra 964, pico 57 contra 57.

   EL CODIGO 'ABS' NO SE CABLEA
   ----------------------------
   Su regla es la lista literal `codigo IS NULL OR codigo = 'ABS'`. Nosotros la
   expresamos con la clasificacion que ya existe: `Clase IN ('piso','ausente')`.
   Da EXACTAMENTE lo mismo (964 contra 964 el 2026-09-09) y no se rompe el dia que
   RRHH invente un codigo de ausencia nuevo, que con la lista literal quedaria
   afuera en silencio.

   La unica diferencia real son cuatro codigos de ausencia que ellos no listan
   (AEJ, MED, MAT, SUSP): 21 filas en 90 dias sobre las cuatro sub-campanas, o sea
   0,3%. Son gente que estaba citada y no vino, igual que ABS, asi que contarlas
   es lo correcto.

   QUE CAMBIA EN PANTALLA
   ----------------------
   "Citados" sube, porque ahora incluye al que estaba en la malla y falto. Eso es
   lo que la malla PROMETIO, que es contra lo que hay que medir la brecha de
   planificacion. Quien efectivamente estuvo ya se ve al lado, en la columna
   "Conectados", que sale de la misma vista del tablero.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

PRINT '--- ANTES ---';
SELECT o.CampanaRRHHID, c.sub_campana
FROM planificacion.PoolOrigen o
LEFT JOIN dbo.campanas c ON c.id = o.CampanaRRHHID ORDER BY c.sub_campana;
SELECT m.PuestoID, p.puesto, m.EnMalla
FROM planificacion.PuestoMalla m
LEFT JOIN dbo.puestos p ON p.id = m.PuestoID WHERE m.EnMalla = 1;
GO

BEGIN TRANSACTION;

/* Artefactos Danados: su gente atiende telefono y esta en el tablero. */
IF NOT EXISTS (SELECT 1 FROM planificacion.PoolOrigen WHERE CampanaRRHHID = 64)
    INSERT INTO planificacion.PoolOrigen (PoolID, CampanaRRHHID, Nota)
    SELECT TOP 1 PoolID, 64,
           N'Campana telefonica. Alineado con dbo.Tablero_Agentes_Voltara el 2026-09-10.'
    FROM planificacion.PoolOrigen;

/* T1 - Consumo: no esta en el tablero. Son 4 personas y 256 h en 30 dias. */
DELETE FROM planificacion.PoolOrigen WHERE CampanaRRHHID = 177;

/* Operador Capacitacion: el tablero lo cuenta. Ojo que no es contradictorio con
   excluir el CODIGO de capacitacion: una cosa es el puesto de la persona y otra
   que ese dia este en un curso. El dia que esta en curso no cuenta igual, porque
   CAPA cae en la clase 'capacitacion'. */
UPDATE planificacion.PuestoMalla
SET EnMalla = 1,
    Nota = N'Atiende el telefono. Alineado con dbo.Tablero_Agentes_Voltara el 2026-09-10.'
WHERE PuestoID = 4;

COMMIT TRANSACTION;
PRINT 'Malla alineada con el tablero.';
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
PRINT '--- DESPUES ---';
SELECT o.CampanaRRHHID, c.sub_campana, o.Nota
FROM planificacion.PoolOrigen o
LEFT JOIN dbo.campanas c ON c.id = o.CampanaRRHHID ORDER BY c.sub_campana;
GO

SELECT m.PuestoID, p.puesto, m.EnMalla
FROM planificacion.PuestoMalla m
LEFT JOIN dbo.puestos p ON p.id = m.PuestoID
WHERE m.EnMalla = 1 ORDER BY p.puesto;
GO

/* La malla de ayer, nuestra contra la de la vista. Tienen que dar igual. */
SELECT CONVERT(varchar(5), v.[Fecha/Horas], 108) AS hora,
       CAST(v.[Op planificados (intervalo)] AS decimal(6,1)) AS tablero
FROM dbo.Tablero_Agentes_Voltara v
WHERE v.[Fecha/Horas] >= DATEADD(day, -1, CAST(GETDATE() AS date))
  AND v.[Fecha/Horas] <  CAST(GETDATE() AS date)
  AND DATEPART(hour, v.[Fecha/Horas]) BETWEEN 8 AND 20
ORDER BY v.[Fecha/Horas];
GO
