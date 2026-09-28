/* ============================================================================
   Cambio de schema y datos — Planificador: Digital como refuerzo de la linea
   Fecha: 2026-09-14 (posterior a 2026-09-11_planificador_sin_minimo_pool.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   1. Crea planificacion.PoolRefuerzo: las sub-campanas de RRHH cuya gente se
      puede PASAR a la linea de un pool cuando no alcanza con la malla. No son
      parte del pool —no cuentan como citados ni se les mide el shrinkage—: son
      la palanca.
   2. Agrega a planificacion.Requerimiento dos columnas, para que cada corrida
      guarde con que refuerzo contaba:
          RefuerzoDisponible   gente de esas sub-campanas en la malla del intervalo
          RefuerzoCubre        cuanto del faltante contra la malla podian cubrir
   3. Siembra el pool General de Voltara con las sub-campanas de Digital.

   POR QUE
   -------
   Ignacio (2026-09-14), con la respuesta de la operacion: Digital es back office
   sin SLA, asi que su gente se puede mover al telefono "en caso de que no
   lleguemos". Las que NO pasan son Agrupadas - Digital y Ajustes - Lecturas.

   YA PASA HOY, medido 17/08-10/09 clasificando a cada conectado por la
   sub-campana de su fila de payroll de ese dia:
       ~620 h de Digital en el telefono = 15% de sus horas trabajadas
       dia habil 10-12 h: 4,5 equivalentes en la linea de ~13 citados de Digital
       y es reactivo: con mas llamadas por telefonico, mas Digital en la linea
       (1,7 -> 2,5 -> 4,2 equivalentes mientras el NDS cae de 88% a 51%)

   QUE CAMBIA EN PANTALLA
   ----------------------
   "A planificar" NO cambia: lo que pide la cola es lo mismo, lo que cambia es
   quien lo puede cubrir. El plan suma "Lo cubre Digital" = lo menor entre el
   faltante contra la malla y la gente de Digital en la malla, y "Falta tras
   Digital". En la comparacion, Conectados se parte en telefonicas / Digital /
   otras. Hay que RECALCULAR EL PLAN para que la corrida vigente lo traiga.

   LA LISTA SE ENUMERA A MANO. No se filtra por nombre: LIKE '%Digital%' metería
   Agrupadas - Digital, que la operacion dijo que no pasa.

       53   Digital                29 operadores con turno en 30 dias
       132  T2T3 - Digital          6
       48   Canales Digitales       sin turnos hoy, se deja para cuando los tenga
       85   DIGITAL - ENRE          sin turnos hoy, idem

       NO   139 Agrupadas - Digital  /  165 Ajustes - Lecturas
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* ------------------------------------------------------ sub-campanas palanca */
IF OBJECT_ID('planificacion.PoolRefuerzo', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.PoolRefuerzo (
        PoolID         INT      NOT NULL,
        -- dbo.campanas.id (el catalogo de RRHH, que NO es calidad.Campanas).
        CampanaRRHHID  SMALLINT NOT NULL,
        Nota           NVARCHAR(200) NULL,

        CONSTRAINT PK_Plan_PoolRefuerzo PRIMARY KEY (PoolID, CampanaRRHHID),
        CONSTRAINT FK_Plan_PoolRefuerzo_Pool FOREIGN KEY (PoolID)
            REFERENCES planificacion.Pool (PoolID)
    );
    PRINT 'planificacion.PoolRefuerzo creada.';
END
ELSE PRINT 'planificacion.PoolRefuerzo ya existia.';
GO

/* ------------------------------------------- lo que cada corrida daba por hecho */
IF COL_LENGTH('planificacion.Requerimiento', 'RefuerzoDisponible') IS NULL
BEGIN
    ALTER TABLE planificacion.Requerimiento ADD
        -- Gente de las sub-campanas de PoolRefuerzo con turno en el intervalo,
        -- contada con la misma definicion que OperadoresPlanificados.
        RefuerzoDisponible  SMALLINT NULL,
        -- MIN(faltante contra la malla, RefuerzoDisponible). NULL sin malla.
        RefuerzoCubre       SMALLINT NULL;
    PRINT 'RefuerzoDisponible y RefuerzoCubre agregados a planificacion.Requerimiento.';
END
ELSE PRINT 'planificacion.Requerimiento ya tenia las columnas de refuerzo.';
GO

/* ------------------------------------------------- siembra: Digital de Voltara */
BEGIN TRANSACTION;

DECLARE @pool INT = (SELECT PoolID FROM planificacion.Pool
                     WHERE CampanaID = 20 AND Nombre = N'General');

IF @pool IS NULL
    PRINT 'No existe el pool General de Voltara: no se siembra nada.';
ELSE
BEGIN
    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolRefuerzo
                   WHERE PoolID = @pool AND CampanaRRHHID = 53)
        INSERT INTO planificacion.PoolRefuerzo (PoolID, CampanaRRHHID, Nota)
        VALUES (@pool, 53, N'Digital. Back office sin SLA: pasa al telefono si no se llega (operacion, 2026-09-14).');

    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolRefuerzo
                   WHERE PoolID = @pool AND CampanaRRHHID = 132)
        INSERT INTO planificacion.PoolRefuerzo (PoolID, CampanaRRHHID, Nota)
        VALUES (@pool, 132, N'T2T3 - Digital. Pasa al telefono si no se llega (operacion, 2026-09-14).');

    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolRefuerzo
                   WHERE PoolID = @pool AND CampanaRRHHID = 48)
        INSERT INTO planificacion.PoolRefuerzo (PoolID, CampanaRRHHID, Nota)
        VALUES (@pool, 48, N'Canales Digitales. Sin turnos al 2026-09-14.');

    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolRefuerzo
                   WHERE PoolID = @pool AND CampanaRRHHID = 85)
        INSERT INTO planificacion.PoolRefuerzo (PoolID, CampanaRRHHID, Nota)
        VALUES (@pool, 85, N'DIGITAL - ENRE. Sin turnos al 2026-09-14.');

    PRINT 'Digital sembrado como refuerzo del pool General.';
END

COMMIT TRANSACTION;
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT r.PoolID, p.Nombre, r.CampanaRRHHID, c.sub_campana, r.Nota
FROM planificacion.PoolRefuerzo r
JOIN planificacion.Pool p ON p.PoolID = r.PoolID
LEFT JOIN dbo.campanas c ON c.id = r.CampanaRRHHID
ORDER BY r.PoolID, c.sub_campana;
GO

/* Ninguna sub-campana puede ser a la vez del pool y su refuerzo: contaria dos
   veces a la misma gente. Tiene que dar vacio. */
SELECT o.PoolID, o.CampanaRRHHID
FROM planificacion.PoolOrigen o
JOIN planificacion.PoolRefuerzo r
  ON r.PoolID = o.PoolID AND r.CampanaRRHHID = o.CampanaRRHHID;
GO
