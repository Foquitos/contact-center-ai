/* ============================================================================
   Cambio de schema — Planificador: seguimiento de pedidos de refuerzo a RRHH
   Fecha: 2026-09-15 (posterior a 2026-09-14c_planificador_cortes_enre.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   1. Crea la tabla planificacion.RefuerzoPedido para registrar y hacer el
      seguimiento de los pedidos de refuerzo enviados a RRHH (mover la malla,
      horas extras programadas o convocatoria en el día).
   2. Guarda la foto al momento del pedido (FaltantePico, HorasOperador, Accion)
      y permite registrar su estado ('pedido', 'cubierto', 'cubierto_parcial',
      'no_cubierto', 'descartado') y las HorasCubiertas observadas al cierre.

   POR QUE
   -------
   Hasta hoy la pestaña Refuerzos calcula en cada carga los bloques de faltante
   contiguos, pero un bloque ya pedido a RRHH se ve exactamente igual que uno
   sin pedir y no hay registro de si efectivamente se cubrió o si quedó sin
   cubrir tras el cierre del día. Con esta tabla, cada pedido se identifica,
   se audita su ciclo de vida y se evalúa perezosamente contra el registro real
   (dbo.payroll y dbo.payroll_futuro).

   COMO CORRER
   -----------
   Contra la BD Acme, con un usuario con DDL. Idempotente y aditiva: crea la
   tabla y sus índices si faltan, y no altera datos existentes.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* ---------------------------------------------------- pedidos de refuerzo */
IF OBJECT_ID('planificacion.RefuerzoPedido', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.RefuerzoPedido (
        RefuerzoPedidoID INT           IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_Plan_RefuerzoPedido PRIMARY KEY,
        CampanaID        INT           NOT NULL,
        PoolID           INT           NOT NULL,
        Dia              DATE          NOT NULL,
        Desde            DATETIME2(0)  NOT NULL,
        Hasta            DATETIME2(0)  NOT NULL,
        FaltantePico     INT           NOT NULL,
        HorasOperador    DECIMAL(9,2)  NOT NULL,     -- foto al pedir
        Accion           VARCHAR(20)   NOT NULL,     -- malla, horas_extra, convocatoria
        Estado           VARCHAR(20)   NOT NULL
            CONSTRAINT DF_Plan_RefuerzoPedido_Estado DEFAULT ('pedido'),
        HorasCubiertas   DECIMAL(9,2)  NULL,
        Nota             NVARCHAR(400) NULL,
        CreadoPor        INT           NULL,
        CreadoEn         DATETIME2(0)  NOT NULL
            CONSTRAINT DF_Plan_RefuerzoPedido_CreadoEn DEFAULT (SYSUTCDATETIME()),
        ActualizadoPor   INT           NULL,
        ActualizadoEn    DATETIME2(0)  NOT NULL
            CONSTRAINT DF_Plan_RefuerzoPedido_ActualizadoEn DEFAULT (SYSUTCDATETIME()),

        CONSTRAINT CK_Plan_RefuerzoPedido_Estado
            CHECK (Estado IN ('pedido','cubierto','cubierto_parcial','no_cubierto','descartado')),
        CONSTRAINT CK_Plan_RefuerzoPedido_Rango
            CHECK (Hasta > Desde),
        CONSTRAINT CK_Plan_RefuerzoPedido_Faltante
            CHECK (FaltantePico >= 0),
        CONSTRAINT CK_Plan_RefuerzoPedido_Horas
            CHECK (HorasOperador >= 0),
        CONSTRAINT FK_Plan_RefuerzoPedido_Campana FOREIGN KEY (CampanaID)
            REFERENCES planificacion.Campana (CampanaID),
        CONSTRAINT FK_Plan_RefuerzoPedido_Pool FOREIGN KEY (PoolID)
            REFERENCES planificacion.Pool (PoolID)
    );

    PRINT 'planificacion.RefuerzoPedido creada.';
END
ELSE PRINT 'planificacion.RefuerzoPedido ya existia.';
GO

/* ----------------------------------------------------------------- indice */
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_Plan_RefuerzoPedido_Campana_Dia'
                 AND object_id = OBJECT_ID('planificacion.RefuerzoPedido'))
BEGIN
    CREATE INDEX IX_Plan_RefuerzoPedido_Campana_Dia
        ON planificacion.RefuerzoPedido (CampanaID, Dia)
        INCLUDE (PoolID, Desde, Hasta, Estado, HorasOperador, HorasCubiertas);
    PRINT 'Indice IX_Plan_RefuerzoPedido_Campana_Dia creado.';
END
ELSE PRINT 'Indice IX_Plan_RefuerzoPedido_Campana_Dia ya existia.';
GO
