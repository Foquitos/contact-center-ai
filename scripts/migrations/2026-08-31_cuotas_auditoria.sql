/* ============================================================================
   Feature — Cupo mensual de auditorías por campaña
   Fecha: 2026-08-31
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1. calidad.CuotaCampana  — configuración: cuántas auditorías por mes puede
      consumir cada campaña (bolsa compartida) y, opcional, cuántas como máximo
      un mismo usuario dentro de esa bolsa.
   2. calidad.CuotaConsumo  — libro mayor de consumo: 1 fila = 1 pedido
      (una corrida de auditoría o una tanda de transcripciones) con lo que
      RESERVÓ al enviarse y lo que realmente CONSUMIÓ al cerrar.
   3. Permisos:
        audit:cuotas       — gerente de operaciones: ver y editar los cupos.
        audit:cuota_exento — queda FUERA del sistema de cupos (Calidad).

   POR QUÉ
   -------
   Se le abre "Auditar", "Auditorías Realizadas" y el Dashboard a todos los
   supervisores de la empresa. Auditar cuesta plata por llamado (tokens de
   Gemini), así que el acceso masivo sin tope es un cheque en blanco. El cupo
   pone un techo por campaña que el gerente de operaciones sube o baja, y la
   pantalla de cupos traduce ese techo a plata (cupo x costo promedio por
   auditoría de la campaña) para que se vea cuánto puede gastar cada campaña
   como máximo en el mes.

   CÓMO CUENTA
   -----------
   - Período: MES CALENDARIO (hora Argentina). AnioMes = 'YYYY-MM'.
   - Una auditoría = un llamado auditado. Una transcripción pedida a demanda
     desde "Auditorías Realizadas" también cuenta 1 (se paga igual en Gemini).
   - Al ENVIAR se reserva lo pedido (si no, se pueden disparar diez lotes de
     100 antes de que cierre el primero) y al CERRAR la corrida se ajusta a las
     filas realmente auditadas (calidad.AuditExecutionLog.filas_auditadas).
     El ajuste lo hace el barrido `conciliar` (app/cuotas.py), que corre en el
     scheduler cada 15 min.
   - Con muestreo por operador/tipificación la cantidad pedida es POR GRUPO y el
     total real recién se sabe al descargar, así que se reserva el tope duro de
     200 (SQL_query.AuditLimitExceededError) y se ajusta al cerrar.

   A QUIÉN LIMITA
   --------------
   A todos los que NO tengan `audit:cuota_exento` (y no sean super admin). Los
   roles de Calidad y el super admin quedan afuera: ni consumen cupo ni se les
   bloquea nada.

   SIN CUPO CONFIGURADO = SIN TOPE (pero SE MIDE)
   ----------------------------------------------
   Una campaña sin fila en CuotaCampana (o con Activo = 0, o LimiteMensual NULL)
   no bloquea a nadie, pero el consumo se registra igual. Es a propósito: al
   desplegar, NADA cambia para nadie hasta que el gerente cargue el primer cupo,
   y para entonces ya tiene el consumo real de los meses anteriores en pantalla
   para elegir un número con fundamento.

   CÓMO CORRER
   -----------
   Contra la BD Acme, con un usuario con DDL sobre el schema calidad.
   Idempotente y aditiva: no toca datos existentes. Puede correrse ANTES del
   deploy (el código trata la tabla ausente como "sin cupo" y no rompe).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* ------------------------------------------------------------------ config */
IF OBJECT_ID('calidad.CuotaCampana', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.CuotaCampana (
        CampanaID            INT           NOT NULL
            CONSTRAINT PK_CuotaCampana PRIMARY KEY,

        -- Bolsa mensual COMPARTIDA por la campaña. NULL = sin tope (solo mide).
        LimiteMensual        INT           NULL,

        -- Sublímite opcional por usuario DENTRO de esa bolsa: evita que un solo
        -- supervisor se coma el cupo de toda la campaña. NULL = sin sublímite.
        LimiteMensualUsuario INT           NULL,

        -- Apagar el cupo sin perder ni el número cargado ni el historial.
        Activo               BIT           NOT NULL
            CONSTRAINT DF_CuotaCampana_Activo DEFAULT (1),

        Nota                 NVARCHAR(400) NULL,
        ActualizadoPor       INT           NULL,
        ActualizadoEn        DATETIME2(0)  NOT NULL
            CONSTRAINT DF_CuotaCampana_ActualizadoEn DEFAULT (SYSUTCDATETIME()),

        CONSTRAINT CK_CuotaCampana_Limite  CHECK (LimiteMensual IS NULL OR LimiteMensual >= 0),
        CONSTRAINT CK_CuotaCampana_LimiteU CHECK (LimiteMensualUsuario IS NULL OR LimiteMensualUsuario >= 0),
        CONSTRAINT FK_CuotaCampana_Campana FOREIGN KEY (CampanaID)
            REFERENCES calidad.Campanas (CampanaID)
    );
    PRINT 'calidad.CuotaCampana creada.';
END
ELSE
    PRINT 'calidad.CuotaCampana ya existía.';
GO

/* ----------------------------------------------------------------- consumo */
IF OBJECT_ID('calidad.CuotaConsumo', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.CuotaConsumo (
        ConsumoID    BIGINT        IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_CuotaConsumo PRIMARY KEY,

        CampanaID    INT           NOT NULL,
        -- 'YYYY-MM' en hora Argentina (el mes que ve el usuario en pantalla, no
        -- el UTC: si no, el cupo se renovaría a las 21:00 del último día del mes).
        AnioMes      CHAR(7)       NOT NULL,
        Documento    INT           NULL,          -- quién lo pidió (nomina.documento)

        -- 'auditoria' | 'transcripcion'
        Tipo         NVARCHAR(20)  NOT NULL,

        -- Con qué se ata este consumo a lo que realmente pasó:
        --   auditoria     -> 'task:<task_id>' o 'grupo:<upload_group_id>' (tandas
        --                    CSV/Voltara, que comparten UNA fila de AuditExecutionLog)
        --   transcripcion -> 'transcripcion:<uuid>' (se confirma en el acto)
        Referencia   NVARCHAR(120) NOT NULL,

        Reservado    INT           NOT NULL,      -- lo pedido al enviar
        Consumido    INT           NULL,          -- lo real, al cerrar la corrida

        -- RESERVADO -> pedido en vuelo, ocupa cupo por lo reservado.
        -- CONFIRMADO -> cerrado, ocupa cupo por lo consumido.
        -- LIBERADO   -> no llegó a auditarse nada (error o corrida abandonada): no ocupa.
        Estado       NVARCHAR(20)  NOT NULL
            CONSTRAINT DF_CuotaConsumo_Estado DEFAULT ('RESERVADO'),
        Motivo       NVARCHAR(300) NULL,

        CreadoEn     DATETIME2(0)  NOT NULL
            CONSTRAINT DF_CuotaConsumo_CreadoEn DEFAULT (SYSUTCDATETIME()),
        -- Se mueve cada vez que una tanda nueva suma sobre la misma reserva de
        -- grupo (CSV/Voltara suben de a 10 audios por request); el barrido no
        -- cierra una reserva de grupo hasta que deja de moverse.
        ActualizadoEn DATETIME2(0) NOT NULL
            CONSTRAINT DF_CuotaConsumo_ActualizadoEn DEFAULT (SYSUTCDATETIME()),
        CerradoEn    DATETIME2(0)  NULL,

        CONSTRAINT CK_CuotaConsumo_Estado CHECK (Estado IN ('RESERVADO', 'CONFIRMADO', 'LIBERADO')),
        CONSTRAINT CK_CuotaConsumo_Tipo   CHECK (Tipo IN ('auditoria', 'transcripcion'))
    );

    -- Lectura caliente: saldo de una campaña en el mes en curso.
    CREATE INDEX IX_CuotaConsumo_campana_mes
        ON calidad.CuotaConsumo (CampanaID, AnioMes)
        INCLUDE (Estado, Reservado, Consumido, Documento, Tipo);

    -- Barrido de conciliación: solo mira las reservas abiertas.
    CREATE INDEX IX_CuotaConsumo_abiertas
        ON calidad.CuotaConsumo (Estado, ActualizadoEn) INCLUDE (Referencia, Tipo);

    -- Una reserva por referencia: si el mismo POST se reintenta (o dos tandas de
    -- la misma subida entran a la vez), se acumula sobre la fila que ya existe en
    -- vez de duplicar el descuento.
    CREATE UNIQUE INDEX UX_CuotaConsumo_referencia
        ON calidad.CuotaConsumo (Referencia);

    PRINT 'calidad.CuotaConsumo creada.';
END
ELSE
    PRINT 'calidad.CuotaConsumo ya existía.';
GO

/* --------------------------------------------------------------- permisos */
IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'audit:cuotas')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('audit:cuotas',
            'Gestionar el cupo mensual de auditorías por campaña (pantalla "Cupos de Auditoría"): ver consumo, costo por auditoría y gasto máximo proyectado, y subir/bajar el cupo');
    PRINT 'Permiso audit:cuotas creado.';
END
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'audit:cuota_exento')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('audit:cuota_exento',
            'Exento del cupo mensual de auditorías: no consume cupo de ninguna campaña ni se le bloquea auditar (Calidad)');
    PRINT 'Permiso audit:cuota_exento creado.';
END
GO

/* ---------------------------------------------------------------------------
   OPCIONAL — asignar los permisos a roles concretos.
   Ambos nacen SIN ASIGNAR (mismo criterio que audit:sync / audit:scheduler).
   Mientras no haya cupos cargados no cambia nada para nadie; ANTES de cargar el
   primer cupo hay que darle `audit:cuota_exento` a los roles de Calidad, o
   pasarían a consumir cupo de la campaña que auditen.

DECLARE @exentos TABLE (name NVARCHAR(100));
INSERT INTO @exentos (name) VALUES ('Calidad'), ('Jefe Calidad');   -- ajustar

INSERT INTO pagina_web.RolePermissions (role_id, permission_id)
SELECT r.id, p.id
FROM pagina_web.Roles r
CROSS JOIN pagina_web.Permissions p
JOIN @exentos e ON e.name = r.name
WHERE p.code = 'audit:cuota_exento'
  AND NOT EXISTS (SELECT 1 FROM pagina_web.RolePermissions rp
                  WHERE rp.role_id = r.id AND rp.permission_id = p.id);

DECLARE @gerentes TABLE (name NVARCHAR(100));
INSERT INTO @gerentes (name) VALUES ('Gerente de Operaciones');      -- ajustar

INSERT INTO pagina_web.RolePermissions (role_id, permission_id)
SELECT r.id, p.id
FROM pagina_web.Roles r
CROSS JOIN pagina_web.Permissions p
JOIN @gerentes g ON g.name = r.name
WHERE p.code = 'audit:cuotas'
  AND NOT EXISTS (SELECT 1 FROM pagina_web.RolePermissions rp
                  WHERE rp.role_id = r.id AND rp.permission_id = p.id);
--------------------------------------------------------------------------- */

SELECT code, description FROM pagina_web.Permissions
WHERE code IN ('audit:cuotas', 'audit:cuota_exento', 'audit:execute', 'audit:sync');
GO

/* Diagnóstico (no modifica nada): cuántas auditorías consumió cada campaña por
   mes en los últimos 6 meses. Es el número con el que conviene calibrar el
   primer cupo de cada campaña. */
SELECT FORMAT(DATEADD(hour, -3, l.started_at), 'yyyy-MM') AS AnioMes,
       c.CampanaID,
       c.Nombre                                            AS Campana,
       SUM(ISNULL(l.filas_auditadas, 0))                   AS Auditorias
FROM calidad.AuditExecutionLog l
LEFT JOIN calidad.Campanas c
       ON TRY_CONVERT(INT, l.campana) = c.CampanaID
WHERE l.started_at >= DATEADD(month, -6, SYSUTCDATETIME())
GROUP BY FORMAT(DATEADD(hour, -3, l.started_at), 'yyyy-MM'), c.CampanaID, c.Nombre
ORDER BY AnioMes DESC, Auditorias DESC;
GO
