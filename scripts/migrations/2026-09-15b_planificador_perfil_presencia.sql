/* ============================================================================
   Cambio de schema — Planificador: perfil de presencia por media hora
   Fecha: 2026-09-15 (posterior a 2026-09-15_planificador_refuerzo_pedido.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   Crea planificacion.PerfilPresencia: cuanto se aparta cada media hora del dia
   (habil / no habil) de la presencia media de la gente con turno. El
   dimensionamiento suma ese apartamiento al shrinkage del dia, asi que la
   jornada descuenta lo mismo que antes y solo cambia EN QUE MEDIA HORA.
   No siembra nada: la carga scripts/planificador_perfil_presencia.py (cron
   semanal, antes del recalculo automatico). Sin filas, el planificador
   dimensiona exactamente como antes.

   POR QUE
   -------
   Ignacio (2026-09-15) pidio aplicar el retraso al arrancar el turno. Medido
   sobre 8 semanas cerradas (gente del pool con turno en el registro contra
   conectados del pool, media hora por media hora), el faltante no es parejo:
   en habiles se concentra a primera hora de la mañana y a ultima de la noche,
   y a la tarde, donde se solapan los turnos, hay mas conectados que el
   promedio. El shrinkage parejo pedia gente de mas a la tarde y de menos
   cuando arranca el dia.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF OBJECT_ID('planificacion.PerfilPresencia', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.PerfilPresencia (
        CampanaID  INT          NOT NULL,
        TipoDia    VARCHAR(10)  NOT NULL,
        -- Minutos desde la medianoche en que arranca la media hora (0, 30, ..., 1410).
        Minuto     SMALLINT     NOT NULL,
        -- Lo que se suma al shrinkage del dia en esa media hora (puede ser negativo),
        -- ya encogido por muestras y topeado.
        Exceso     DECIMAL(6,4) NOT NULL,
        -- El faltante medido tal cual y las persona-intervalos que lo sostienen.
        Faltante   DECIMAL(7,4) NULL,
        Muestras   INT          NOT NULL,
        MedidoEn   DATETIME2(0) NOT NULL
            CONSTRAINT DF_Plan_PerfilPresencia_MedidoEn DEFAULT SYSDATETIME(),

        CONSTRAINT PK_Plan_PerfilPresencia PRIMARY KEY (CampanaID, TipoDia, Minuto),
        CONSTRAINT CK_Plan_PerfilPresencia_Tipo CHECK (TipoDia IN ('habil', 'no_habil')),
        CONSTRAINT CK_Plan_PerfilPresencia_Minuto CHECK (Minuto >= 0 AND Minuto < 1440),
        CONSTRAINT CK_Plan_PerfilPresencia_Exceso CHECK (Exceso BETWEEN -0.5 AND 0.5)
    );
    PRINT 'planificacion.PerfilPresencia creada.';
END
ELSE PRINT 'planificacion.PerfilPresencia ya existia.';
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT CampanaID, TipoDia, COUNT(*) AS medias_horas,
       MIN(Exceso) AS minimo, MAX(Exceso) AS maximo, MAX(MedidoEn) AS medido_en
FROM planificacion.PerfilPresencia
GROUP BY CampanaID, TipoDia
ORDER BY CampanaID, TipoDia;
GO
