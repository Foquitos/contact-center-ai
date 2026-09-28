/* ============================================================================
   Feature — Logs de Login/Logout y análisis de uso de la página
   Fecha: 2026-07-23
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1) pagina_web.LoginAudit — 1 fila = 1 evento de sesión (login / logout /
      intento fallido). Hoy NADA registra los inicios/cierres de sesión: el
      login pasa por FastAPI POST /token/ y el logout solo limpia la sesión de
      Flask (el backend ni se entera). Esta tabla es la fuente de la nueva
      pantalla "Logs de Ingreso": historial, quién está conectado ahora, desde
      qué IP, y los gráficos de evolución del uso (logins por día/semana,
      usuarios únicos, horas pico, intentos fallidos).

      created_at usa SYSDATETIME() (hora LOCAL del servidor SQL, ya en horario
      Argentina — mismo criterio verificado para pagina_web.RbacAuditLog) → se
      sirve y se muestra tal cual, SIN el ajuste UTC que necesita
      calidad.AuditExecutionLog.

      last_seen es un heartbeat de presencia: solo se actualiza en las filas
      'login', vía el GET /me que el frontend ya llama cada ~5 min. Es necesario
      porque el token JWT dura 6 h (ACCESS_TOKEN_EXPIRE_MINUTES=360): inferir
      "conectado ahora" solo por expiración del token no serviría. "Conectado
      ahora" = última fila 'login' del usuario con COALESCE(last_seen,created_at)
      dentro de los últimos ~15 min y sin 'logout' posterior.

   2) Permiso pagina_web.Permissions 'logs:login' — habilita ver la pantalla
      "Logs de Ingreso" (menú Administración > Usuarios y accesos) y sus
      endpoints backend (/session/…). NACE SIN ASIGNAR (estilo
      uso_ia.chatbot / audit:scheduler): hasta que se asigne rol por rol desde
      Gestionar Roles, SOLO el super admin ve la página.

   NOTA (defensivo): el código Python nunca rompe login/logout si esta tabla o
   este permiso todavía no existen (los helpers de session_log.py loguean y
   siguen). Igual conviene correr esta migración ANTES del deploy del código.

   CÓMO CORRER
   -----------
   Contra la BD Acme con un usuario con DDL sobre el schema pagina_web.
   Idempotente: tabla, índices y permiso se crean solo si no existen.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

-- 1) Tabla de eventos de sesión -------------------------------------------------
IF OBJECT_ID('pagina_web.LoginAudit', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.LoginAudit (
        id          BIGINT IDENTITY(1,1) NOT NULL
                    CONSTRAINT PK_LoginAudit PRIMARY KEY,
        documento   INT            NULL,   -- DNI del usuario (o el intentado, en fallidos)
        event       VARCHAR(20)    NOT NULL,-- 'login' | 'logout' | 'login_failed'
        ip_address  VARCHAR(64)    NULL,
        user_agent  NVARCHAR(400)  NULL,
        created_at  DATETIME2(0)   NOT NULL
                    CONSTRAINT DF_LoginAudit_created_at DEFAULT (SYSDATETIME()),
        last_seen   DATETIME2(0)   NULL     -- heartbeat de presencia (solo filas 'login')
    );
END
GO

-- Índices para el historial (por fecha), el filtro por usuario y los agregados.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_LoginAudit_created_at'
               AND object_id = OBJECT_ID('pagina_web.LoginAudit'))
    CREATE INDEX IX_LoginAudit_created_at   ON pagina_web.LoginAudit (created_at);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_LoginAudit_documento'
               AND object_id = OBJECT_ID('pagina_web.LoginAudit'))
    CREATE INDEX IX_LoginAudit_documento    ON pagina_web.LoginAudit (documento, created_at);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_LoginAudit_event'
               AND object_id = OBJECT_ID('pagina_web.LoginAudit'))
    CREATE INDEX IX_LoginAudit_event        ON pagina_web.LoginAudit (event, created_at);
GO

-- 2) Permiso dedicado (nace SIN asignar) ---------------------------------------
IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'logs:login')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('logs:login',
            'Ver el log de inicios/cierres de sesión y el análisis de uso (Logs de Ingreso)');
END
GO

-- Verificación rápida
SELECT COUNT(*) AS filas_login_audit FROM pagina_web.LoginAudit;
SELECT code, description FROM pagina_web.Permissions WHERE code = 'logs:login';
GO
