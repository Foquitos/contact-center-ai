/* ============================================================================
   Feature — Cambio de contraseña forzado en el primer ingreso
   Fecha: 2026-07-13
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Una columna [must_change_password] en pagina_web.passwords que marca cuándo
   un usuario debe cambiar su contraseña antes de poder seguir usando el sistema.

   COMPORTAMIENTO
   --------------
   - La contraseña VIEJA sigue siendo válida: el usuario inicia sesión normal,
     el backend emite el token y el frontend lo redirige a /change-password.
     Recién cuando setea una contraseña nueva (>= 8 caracteres) se limpia el flag.
   - DEFAULT 1: toda fila NUEVA de passwords (alta de usuario, alta masiva, reset
     de admin) nace con el flag encendido => contraseña temporal a cambiar.
     Al agregar la columna NOT NULL con DEFAULT 1, SQL Server tambien rellena con
     1 TODAS las filas ya existentes => todos los usuarios actuales quedan
     forzados a cambiarla en su proximo ingreso (decision de producto).
   - El self-service de cambio de contraseña (POST /update_password/) apaga el
     flag (must_change_password = 0). El reset de admin lo vuelve a encender.

   IDEMPOTENTE: si la columna ya existe no se hace nada (no se re-fuerza a quien
   ya cambio). Correr contra la BD Acme ANTES de deployar el código: el código
   nuevo selecciona esta columna y fallaría si todavia no existe.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.columns
    WHERE object_id = OBJECT_ID('pagina_web.passwords')
      AND name = 'must_change_password'
)
BEGIN
    ALTER TABLE pagina_web.passwords
        ADD must_change_password BIT NOT NULL
        CONSTRAINT DF_passwords_must_change_password DEFAULT (1);
END
GO

SELECT
    'passwords.must_change_password' AS objeto,
    COUNT(*)                          AS total_filas,
    SUM(CAST(must_change_password AS INT)) AS forzadas_a_cambiar
FROM pagina_web.passwords;
GO
