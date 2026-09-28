/* ============================================================================
   Cambio de configuración — Planificador: un solo pool para Voltara
   Fecha: 2026-09-09 (posterior a 2026-09-09_planificador_reparto_cliente.sql)
   Autor: equipo Acme

   QUÉ HACE
   --------
   Manda los tres pools de Voltara a uno. No agrega ni saca columnas ni tablas:
   sólo mueve los skills y desactiva los pools que quedan vacíos.

       antes                                          después
       Pool 1 «General»              skills 1..7      Pool 1 «General» 1..9 y 13
       Pool 2 «Reclamos y GC»        skills 8, 9      Pool 2 desactivado
       Pool 3 «Comercial Consumo»    skill 13         Pool 3 desactivado

   POR QUÉ
   -------
   Instrucción de la operación (Ignacio, 2026-09-09): los operadores de Voltara
   tienen TODOS los skills, así que no hay que separarlos al asignar. Dimensionar
   tres colas por separado tira a la basura la economía de escala de la cola
   compartida: la misma cantidad de tráfico repartida en tres Erlang C
   independientes pide más gente que en uno, y encima cada pool arrastra su propia
   cobertura mínima de madrugada.

   CUÁNTO CAMBIA — MEDIDO sobre el pronóstico de 14 días del 2026-09-09
   ---------------------------------------------------------------------
                        pools   horas-operador   pico de operadores
       hoy                 3            11.760            118
       pool único          1             9.669            110
       diferencia                        -2.092 (-17,8%)   -8

   Los 2.092 turnos-hora que se ahorran no son un ajuste de método: es dotación
   que se estaba pidiendo por partir la cola en tres.

   QUÉ DICE HOY EL ACD, PARA QUE EL NÚMERO SE PUEDA DISCUTIR
   ---------------------------------------------------------
   Conviene dejarlo escrito porque no coincide con la instrucción y en algún
   momento alguien va a preguntar. Semana del 2026-09-01 al 07:

       agentes distintos que atendieron                    149
       skills por agente (promedio de los atendidos)       2,6
       agentes que atendieron llamadas de más de un pool    19
       agentes logueados por intervalo (10 a 18 h):
           EMERGENCIAS 39,6 · COMERCIAL 34,1 · TOC 15,8
           RECLAMO-DANO 3,1 · GRANDES-CUENTAS 2,5 · COMERCIAL-CONSUMO 0,8

   O sea que en el ruteo de hoy RECLAMO-DANO y GRANDES-CUENTAS tienen dos o tres
   personas logueadas, no ciento cincuenta. La instrucción es que la habilidad la
   tienen todos y que la planificación no los separe; si mañana se quiere volver a
   dimensionar el grupo chico aparte, el camino es el de abajo.

   CÓMO SE VUELVE ATRÁS
   --------------------
   Los pools se DESACTIVAN, no se borran: planificacion.Requerimiento tiene
   corridas viejas que apuntan a PoolID 2 y 3, y borrarlos perdería el histórico.
   Para revertir:

       UPDATE planificacion.Skill SET PoolID = 2
        WHERE CampanaID = 20 AND SkillID IN (8, 9);
       UPDATE planificacion.Skill SET PoolID = 3
        WHERE CampanaID = 20 AND SkillID = 13;
       UPDATE planificacion.Pool SET Activo = 1
        WHERE CampanaID = 20 AND PoolID IN (2, 3);
       -- y sacar de PoolOrigen del pool 1 la campaña de RRHH 177, que era del 3.

   LA COBERTURA MÍNIMA
   -------------------
   Queda la del pool 1 (MinOperadores = 2). Antes eran 2 + 1 + 1 = 4 personas de
   madrugada por el solo hecho de tener tres pools, y de esas cuatro, dos estaban
   para colas que a las 4 de la mañana no reciben nada.

   LOS OBJETIVOS NO SE PIERDEN
   ---------------------------
   Cada skill conserva su ObjetivoNds, UmbralSeg, MaxAbandono y las restricciones
   de la planilla. El motor toma el objetivo de ESPERA más estricto entre los
   skills con volumen en el intervalo y después verifica el abandono skill por
   skill con la paciencia propia de cada uno, así que ELECTRODEPENDIENTES sigue
   exigiendo lo suyo dentro del pool grande. Eso ya estaba y no lo cambia esta
   migración.

   CÓMO CORRER
   -----------
   Idempotente. Después hay que recalcular: la corrida vigente quedó armada con
   tres pools y la pantalla la muestra hasta que se rehaga.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

BEGIN TRANSACTION;

/* 1. Los skills de los pools 2 y 3 pasan al 1. Se toma el pool 1 por nombre y no
      por id fijo para no depender de que la siembra haya dado ese número. */
DECLARE @pool1 INT = (SELECT MIN(PoolID) FROM planificacion.Pool
                      WHERE CampanaID = 20 AND Nombre = 'General');
IF @pool1 IS NULL
BEGIN
    ROLLBACK TRANSACTION;
    RAISERROR('No existe el pool «General» de la campaña 20: revisar la siembra.', 16, 1);
    RETURN;
END

UPDATE s SET PoolID = @pool1
FROM planificacion.Skill s
JOIN planificacion.Pool p ON p.PoolID = s.PoolID AND p.CampanaID = s.CampanaID
WHERE s.CampanaID = 20 AND s.PoolID <> @pool1;
PRINT CONCAT('Skills movidos al pool General: ', @@ROWCOUNT);

/* 2. Las sub-campañas de RRHH de los pools que se vacían pasan al 1, sin
      duplicar las que ya estaban. Sin esto el pool único perdería la gente que
      RRHH tiene citada en esas campañas y la brecha saldría inventada. */
INSERT INTO planificacion.PoolOrigen (PoolID, CampanaRrhhId)
SELECT DISTINCT @pool1, po.CampanaRrhhId
FROM planificacion.PoolOrigen po
JOIN planificacion.Pool p ON p.PoolID = po.PoolID
WHERE p.CampanaID = 20 AND po.PoolID <> @pool1
  AND NOT EXISTS (SELECT 1 FROM planificacion.PoolOrigen x
                  WHERE x.PoolID = @pool1 AND x.CampanaRrhhId = po.CampanaRrhhId);
PRINT CONCAT('Campanas de RRHH agregadas al pool General: ', @@ROWCOUNT);

DELETE po
FROM planificacion.PoolOrigen po
JOIN planificacion.Pool p ON p.PoolID = po.PoolID
WHERE p.CampanaID = 20 AND po.PoolID <> @pool1;

/* 3. Los pools vacíos se desactivan. `cargar_config` sólo lee los activos, así
      que dejan de dimensionar; las corridas viejas siguen apuntando a ellos. */
UPDATE planificacion.Pool SET Activo = 0
WHERE CampanaID = 20 AND PoolID <> @pool1 AND Activo = 1;
PRINT CONCAT('Pools desactivados: ', @@ROWCOUNT);

COMMIT TRANSACTION;
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   Tiene que quedar un solo pool activo, con los diez skills adentro.
   ==================================================================== */
SELECT p.PoolID, p.Nombre, p.Activo, p.MinOperadores,
       COUNT(s.SkillID) AS skills,
       (SELECT COUNT(*) FROM planificacion.PoolOrigen po
         WHERE po.PoolID = p.PoolID) AS campanas_rrhh
FROM planificacion.Pool p
LEFT JOIN planificacion.Skill s ON s.PoolID = p.PoolID AND s.CampanaID = p.CampanaID
WHERE p.CampanaID = 20
GROUP BY p.PoolID, p.Nombre, p.Activo, p.MinOperadores
ORDER BY p.PoolID;

SELECT SkillID, Nombre, PoolID, Activo FROM planificacion.Skill
WHERE CampanaID = 20 ORDER BY SkillID;
GO
