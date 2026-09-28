/* ============================================================================
   Fix — pagina_web.ChatbotTablaFila: marcar las filas corregidas a mano
   Fecha: 2026-09-02 (posterior a 2026-09-02_chatbot_tablas.sql)
   Autor: equipo Acme

   POR QUÉ EXISTE ESTA MIGRACIÓN APARTE
   ------------------------------------
   Las dos columnas de abajo se agregaron al CREATE TABLE de la migración
   2026-09-02_chatbot_tablas.sql DESPUÉS de que esa migración ya se había
   aplicado. Como el CREATE está detrás de un `IF NOT EXISTS (tabla)`, volver a
   correrla es un no-op: la tabla ya existía y las columnas nunca se crearon.

   El síntoma no fue un error visible sino la desaparición de las tablas de la
   pantalla: `chatbot_tablas_admin.listar()` y `obtener_filas()` referencian
   `editada_at`, la consulta falla con "Invalid column name", y el frontend
   —que esconde la sección de tablas cuando no puede cargarla, para no tapar la
   pantalla de documentos— la oculta sin decir nada. Los datos nunca se tocaron.

   Regla que esto deja escrita: una migración ya aplicada NO se edita. Los
   cambios posteriores van SIEMPRE en un archivo nuevo y aditivo. La versión
   corregida del CREATE se deja igual en la migración original para que una
   instalación limpia nazca bien; las dos rutas convergen porque ambas son
   idempotentes.

   QUÉ AGREGA
   ----------
   editada_por / editada_at — quién y cuándo tocó la fila A MANO.

   Importa porque una tabla tiene dos caminos de escritura que se pisan: la
   carga masiva desde la fuente oficial (que reemplaza todo) y la corrección
   puntual del analista que ve un dato mal. Sin esta marca, la próxima recarga se
   lleva puestas las correcciones y nadie se entera; con ella, el listado muestra
   cuántas hay y el diff de una planilla nueva avisa cuáles está por pisar.

   NULL = la fila entró por una carga masiva y nadie la tocó después. Es el
   estado correcto para las 2.243 filas ya cargadas (Benefix + Vantix), así que no
   hace falta backfill.

   IDEMPOTENTE: se puede correr más de una vez.
   ============================================================================ */

SET NOCOUNT ON;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('pagina_web.ChatbotTablaFila')
                 AND name = 'editada_por')
BEGIN
    ALTER TABLE pagina_web.ChatbotTablaFila ADD editada_por INT NULL;
    PRINT 'ChatbotTablaFila.editada_por agregada.';
END
ELSE
    PRINT 'ChatbotTablaFila.editada_por ya existía.';
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('pagina_web.ChatbotTablaFila')
                 AND name = 'editada_at')
BEGIN
    ALTER TABLE pagina_web.ChatbotTablaFila ADD editada_at DATETIME2(3) NULL;
    PRINT 'ChatbotTablaFila.editada_at agregada.';
END
ELSE
    PRINT 'ChatbotTablaFila.editada_at ya existía.';
GO

/* Control: las dos columnas tienen que estar y todas las filas en NULL. */
SELECT
    (SELECT COUNT(*) FROM sys.columns
     WHERE object_id = OBJECT_ID('pagina_web.ChatbotTablaFila')
       AND name IN ('editada_por', 'editada_at'))            AS columnas_creadas,
    (SELECT COUNT(*) FROM pagina_web.ChatbotTablaFila)        AS filas_totales,
    (SELECT COUNT(*) FROM pagina_web.ChatbotTablaFila
     WHERE editada_at IS NOT NULL)                            AS filas_marcadas;
GO

PRINT 'Migración 2026-09-02b_chatbot_tabla_fila_editada COMPLETA.';
GO
