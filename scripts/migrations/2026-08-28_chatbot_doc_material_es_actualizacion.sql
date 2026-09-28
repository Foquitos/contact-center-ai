/* ============================================================================
   Feature — Flag de actualización/reemplazo en material pendiente de chatbots
   Fecha: 2026-08-28
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Columna `es_actualizacion` (BIT, default 0) en pagina_web.ChatbotDocMaterial.
   Permite marcar explícitamente cuando el material cargado corresponde a una
   modificación de precios, actualización de procedimientos o baja de secciones,
   instruyendo a la IA a sobrescribir/reemplazar información previa en lugar de
   deduplicar o marcar conflicto.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme. Es idempotente.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF NOT EXISTS (
    SELECT 1
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'pagina_web'
      AND TABLE_NAME = 'ChatbotDocMaterial'
      AND COLUMN_NAME = 'es_actualizacion'
)
BEGIN
    ALTER TABLE pagina_web.ChatbotDocMaterial
    ADD es_actualizacion BIT NOT NULL CONSTRAINT DF_ChatbotDocMaterial_act DEFAULT 0;
END
GO

