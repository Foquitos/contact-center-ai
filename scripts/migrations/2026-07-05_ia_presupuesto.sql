/* ============================================================================
   Feature — Presupuesto mensual de IA con alertas por umbral de porcentaje
   Fecha: 2026-07-05
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1) pagina_web.IA_Presupuesto — configuración (una sola fila, id=1): monto
      mensual en USD, umbrales de aviso como porcentajes separados por coma
      (ej. '50,75,90,100') y destinatarios de las alertas (';'-separados;
      NULL = solo ADMIN_EMAIL). Se edita desde la pestaña Resumen de
      "Gastos y Logs de IA" (POST /uso-ia/presupuesto, permiso uso_ia.view).

   2) pagina_web.IA_Presupuesto_Alertas — 1 fila = 1 aviso enviado (mes +
      umbral). El UNIQUE (anio_mes, umbral) hace idempotente el chequeo: cada
      umbral avisa UNA sola vez por mes aunque el job corra cada hora
      (backend/app/presupuesto_ia.py::verificar_presupuesto_ia, programado en
      run_scheduler.py). El gasto del mes se calcula contra
      pagina_web.vw_IA_Uso_Costos (todas las features).

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema pagina_web.
   Idempotente: las tablas se crean solo si no existen. El código es best-effort:
   si las tablas no están, el tablero muestra "sin presupuesto configurado" y el
   job solo loguea, sin romper nada.
   ============================================================================ */

SET XACT_ABORT ON;
GO

IF OBJECT_ID('pagina_web.IA_Presupuesto', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.IA_Presupuesto (
        id            INT           NOT NULL CONSTRAINT PK_IA_Presupuesto PRIMARY KEY
                                    CONSTRAINT CK_IA_Presupuesto_singleton CHECK (id = 1),
        monto_usd     DECIMAL(12,2) NOT NULL,
        umbrales      NVARCHAR(100) NOT NULL CONSTRAINT DF_IA_Presupuesto_umbrales DEFAULT('50,75,90,100'),
        destinatarios NVARCHAR(500) NULL,      -- ';'-separados; NULL = solo ADMIN_EMAIL
        activo        BIT           NOT NULL CONSTRAINT DF_IA_Presupuesto_activo DEFAULT(1),
        updated_by    NVARCHAR(50)  NULL,
        updated_at    DATETIME2     NOT NULL CONSTRAINT DF_IA_Presupuesto_updated DEFAULT(SYSUTCDATETIME())
    );
END
GO

IF OBJECT_ID('pagina_web.IA_Presupuesto_Alertas', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.IA_Presupuesto_Alertas (
        id         INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_IA_Presupuesto_Alertas PRIMARY KEY,
        anio_mes   CHAR(7)       NOT NULL,      -- 'YYYY-MM' (UTC, igual que vw_IA_Uso_Costos)
        umbral     INT           NOT NULL,      -- porcentaje del presupuesto (ej. 75)
        gasto_usd  DECIMAL(18,6) NOT NULL,      -- gasto al momento del aviso
        pct        DECIMAL(6,2)  NOT NULL,      -- % del presupuesto al momento del aviso
        enviado_at DATETIME2     NOT NULL CONSTRAINT DF_IA_Presupuesto_Alertas_enviado DEFAULT(SYSUTCDATETIME()),
        CONSTRAINT UX_IA_Presupuesto_Alertas_mes_umbral UNIQUE (anio_mes, umbral)
    );
END
GO
