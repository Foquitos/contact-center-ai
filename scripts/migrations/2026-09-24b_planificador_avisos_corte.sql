/* ============================================================================
   Cambio de schema — Planificador: avisos de corte de Hidra
   Fecha: 2026-09-24 (posterior a 2026-09-24_planificador_voltara_nivel_persistencia.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   Crea planificacion.AvisoCorte: cada aviso de interrupción de servicio que
   publica Hidra (las notas de https://www.hidra.example/usuarios/Novedades, o un
   mail que se carga a mano), con lo que la IA leyó de él, cuántas llamadas de
   más se espera que traiga y el ajuste que se propone al planificador. La carga
   la hace scripts/cortes_hidra.py (cron horario). No siembra nada ni toca otra
   tabla.

   POR QUE
   -------
   Los cortes programados son el 19% del error diario absoluto del pronóstico de
   Hidra Técnico, y Hidra los anuncia uno o dos días antes. La propuesta NO se
   aplica sola: queda 'pendiente' hasta que alguien con planificador.edit la
   aplica (crea un planificacion.Ajuste) o la descarta desde la pantalla.

   Después de cada corte el mismo script mide cuántas llamadas tipificadas
   'Corte programado' trajo de más (LlamadasReales). Con eso la estimación se
   recalibra sola: la mediana de lo medido por escala reemplaza al valor semilla
   en cuanto hay cinco cortes medidos.

   Contenido compartido (no lleva Entorno): lo carga un solo cron y la clave
   única (CampanaID, Referencia) impide duplicados aunque corriera en dos lados.
   Las fechas van en hora argentina, igual que el resto del planificador.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF OBJECT_ID('planificacion.AvisoCorte', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.AvisoCorte (
        AvisoID             INT            IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_Plan_AvisoCorte PRIMARY KEY,
        CampanaID           INT            NOT NULL,
        Fuente              VARCHAR(10)    NOT NULL,      -- web | mail
        -- La URL de la nota o el Message-ID del mail: es lo que evita cargarlo dos veces.
        Referencia          NVARCHAR(400)  NOT NULL,
        Publicado           DATETIME2(0)   NULL,
        Titulo              NVARCHAR(300)  NULL,
        Texto               NVARCHAR(MAX)  NOT NULL,

        -- Lo que leyó la IA. NULL en todo si la lectura falló (Estado = 'error').
        EsCorte             BIT            NULL,
        Programado          BIT            NULL,
        Inicio              DATETIME2(0)   NULL,
        Fin                 DATETIME2(0)   NULL,
        Instalaciones       NVARCHAR(400)  NULL,
        Zonas               NVARCHAR(800)  NULL,
        AfectaCaba          BIT            NULL,
        -- Sólo si el aviso da un número; la IA no lo estima.
        UsuariosAfectados   INT            NULL,
        Escala              VARCHAR(10)    NULL,          -- rutina | grande
        Resumen             NVARCHAR(400)  NULL,
        ModeloIA            VARCHAR(60)    NULL,

        -- La propuesta al planificador.
        LlamadasExtra       INT            NULL,
        FactorPropuesto     DECIMAL(6,3)   NULL,
        AjusteDesde         DATETIME2(0)   NULL,
        AjusteHasta         DATETIME2(0)   NULL,

        Estado              VARCHAR(12)    NOT NULL
            CONSTRAINT DF_Plan_AvisoCorte_Estado DEFAULT ('pendiente'),
        AjusteID            INT            NULL,
        EventoID            INT            NULL,
        RevisadoPor         INT            NULL,
        RevisadoEn          DATETIME2(0)   NULL,

        -- Lo que el corte trajo de verdad: llamadas 'Corte programado' por encima
        -- de las habituales, medido después de que terminó.
        LlamadasReales      INT            NULL,
        MedidoEn            DATETIME2(0)   NULL,

        CargadoEn           DATETIME2(0)   NOT NULL
            CONSTRAINT DF_Plan_AvisoCorte_CargadoEn DEFAULT (SYSUTCDATETIME()),

        CONSTRAINT UQ_Plan_AvisoCorte_Referencia UNIQUE (CampanaID, Referencia),
        CONSTRAINT FK_Plan_AvisoCorte_Campana FOREIGN KEY (CampanaID)
            REFERENCES planificacion.Campana (CampanaID),
        CONSTRAINT FK_Plan_AvisoCorte_Ajuste FOREIGN KEY (AjusteID)
            REFERENCES planificacion.Ajuste (AjusteID),
        CONSTRAINT FK_Plan_AvisoCorte_Evento FOREIGN KEY (EventoID)
            REFERENCES planificacion.Evento (EventoID),
        CONSTRAINT CK_Plan_AvisoCorte_Fuente CHECK (Fuente IN ('web', 'mail')),
        CONSTRAINT CK_Plan_AvisoCorte_Escala CHECK (Escala IS NULL OR Escala IN ('rutina', 'grande')),
        CONSTRAINT CK_Plan_AvisoCorte_Estado CHECK (Estado IN
            ('pendiente', 'aplicado', 'descartado', 'sin_corte', 'vencido', 'error')),
        CONSTRAINT CK_Plan_AvisoCorte_Factor CHECK (FactorPropuesto IS NULL
            OR (FactorPropuesto > 0 AND FactorPropuesto <= 10)),
        CONSTRAINT CK_Plan_AvisoCorte_Rango CHECK (Fin IS NULL OR Inicio IS NULL OR Fin > Inicio)
    );
    CREATE INDEX IX_Plan_AvisoCorte_Inicio ON planificacion.AvisoCorte (CampanaID, Inicio)
        INCLUDE (Estado, Escala, LlamadasReales);
    PRINT 'planificacion.AvisoCorte creada.';
END
ELSE PRINT 'planificacion.AvisoCorte ya existia.';
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT CampanaID, Estado, COUNT(*) AS avisos, MAX(CargadoEn) AS ultimo
FROM planificacion.AvisoCorte
GROUP BY CampanaID, Estado
ORDER BY CampanaID, Estado;
GO
