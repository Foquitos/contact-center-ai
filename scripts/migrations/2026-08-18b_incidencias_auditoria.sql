/* ============================================================================
   Feature — INCIDENCIAS de auditoría (calidad.Auditorias.Incidencia)
   Fecha: 2026-08-18
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Una columna que explica por qué NO hay que confiar en una auditoría:

     audio_mudo                  el audio no tiene voz audible
     audio_mayormente_silencio   casi todo silencio, revisar antes de usarla
     audio_incompleto            cortado / falta parte de la conversación
     operador_no_coincide        el que se escucha no es el que dice el sistema
     duracion_no_coincide        el archivo no dura lo que dice el sistema

   NULL = sin incidencia, que es el caso normal.

   POR QUÉ
   -------
   El 2026-08-10 una grabación de Voltara de 82 segundos prácticamente muda llegó
   a Gemini junto con toda la metadata del llamado (nombre de la agente, skill,
   documento del titular, motivo). El modelo devolvió una conversación completa
   y verosímil —armada con esos datos, no con el audio— y una auditoría con
   todos los atributos en "Cumple". En la misma corrida, dos audios llegaron
   cruzados entre sí: cada uno se auditó bajo el ConnID del otro, con lo cual
   cuatro "No cumple" quedaron en el legajo de quien no era. La IA incluso
   detectó ese segundo problema y lo escribió en su razonamiento, donde nadie lo
   ve.

   Hasta ahora una auditoría solo podía terminar guardada (con todos sus
   atributos) o FALLIDA por error técnico. No había forma de decir "esto se
   auditó pero no hay que creerle". Esta columna es esa tercera salida.

   El código la llena desde tres lugares (ver backend/AuditorIA/incidencias.py):
     1. el gate de audio, que mide el archivo con ffmpeg ANTES de gastar tokens;
     2. la propia IA, que ahora tiene un campo en el schema para declararla;
     3. los chequeos post-auditoría (operador que no coincide, duración que no
        cierra contra el sistema de origen).

   NO HACE FALTA TOCAR PROMEDIOS NI TABLEROS: una auditoría con incidencia
   bloqueante se guarda SIN filas en AuditoriaDetalles y con PuntajeFinal NULL,
   así que ya queda fuera de todo promedio de atributos y de puntaje — el mismo
   criterio que un atributo opcional sin evidencia.

   El código funciona con o sin esta migración aplicada (detecta la columna antes
   de escribir, igual que PlantillaVersionID), pero sin ella la incidencia se
   pierde al guardar.

   CÓMO CORRER: contra la BD Acme. Idempotente. Se puede correr antes o después
   del deploy del código.
   ============================================================================ */

USE Acme;
GO

/* ---------------------------------------------------------------------------
   1) La columna
   --------------------------------------------------------------------------- */
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'calidad' AND TABLE_NAME = 'Auditorias'
      AND COLUMN_NAME = 'Incidencia'
)
BEGIN
    ALTER TABLE calidad.Auditorias ADD Incidencia NVARCHAR(40) NULL;
    PRINT 'OK: calidad.Auditorias.Incidencia creada.';
END
ELSE
    PRINT 'Sin cambios: calidad.Auditorias.Incidencia ya existía.';
GO

/* Índice filtrado: las auditorías con incidencia son una minoría y se las busca
   como "mostrame lo que hay que revisar". El filtro deja el índice chico. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Auditorias_Incidencia'
               AND object_id = OBJECT_ID('calidad.Auditorias'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_Auditorias_Incidencia
        ON calidad.Auditorias (Incidencia)
        INCLUDE (PlantillaID, FechaAuditoria)
        WHERE Incidencia IS NOT NULL;
    PRINT 'OK: IX_Auditorias_Incidencia creado.';
END
ELSE
    PRINT 'Sin cambios: IX_Auditorias_Incidencia ya existía.';
GO

/* ---------------------------------------------------------------------------
   2) Exponerla en el listado de "Auditorías Realizadas"

   El SP arma su SELECT como SQL dinámico y mide ~18 KB, así que en vez de
   pegarlo entero acá (y arriesgarse a pisar cambios previos, como la migración
   de performance 2026-08-14b) se lo reescribe a partir de su propia definición
   actual: se buscan dos patrones exactos y se insertan las líneas nuevas.

   Si algún patrón no aparece la cantidad de veces esperada, el script FALLA sin
   tocar nada: significa que el SP cambió y hay que revisar el reemplazo a mano.
   --------------------------------------------------------------------------- */
DECLARE @def       NVARCHAR(MAX) = OBJECT_DEFINITION(OBJECT_ID('calidad.sp_ObtenerAuditoriasFiltradas'));
DECLARE @nl        NVARCHAR(2)   = CHAR(13) + CHAR(10);

IF @def IS NULL
BEGIN
    RAISERROR('No se encontró calidad.sp_ObtenerAuditoriasFiltradas.', 16, 1);
    RETURN;
END

IF CHARINDEX('A_Ext.Incidencia', @def) > 0
BEGIN
    PRINT 'Sin cambios: el SP ya devuelve Incidencia.';
    RETURN;
END

/* Patrón 1: el SELECT real (dentro del SQL dinámico). Debe aparecer 1 vez. */
DECLARE @patSel   NVARCHAR(400) = 'A_Ext.PuntajeFinal,' + @nl + '        A_Ext.EsErrorCritico,';
DECLARE @newSel   NVARCHAR(400) = 'A_Ext.PuntajeFinal,' + @nl + '        A_Ext.Incidencia,' + @nl + '        A_Ext.EsErrorCritico,';

/* Patrón 2: las ramas "sin resultados" (CAST(NULL...) WHERE 1=0), para que el
   resultset tenga las mismas columnas haya o no filas. Debe aparecer 2 veces. */
DECLARE @patVac   NVARCHAR(400) = 'CAST(NULL AS DECIMAL(5,2)) AS PuntajeFinal,';
DECLARE @newVac   NVARCHAR(400) = 'CAST(NULL AS DECIMAL(5,2)) AS PuntajeFinal,' + @nl + '            CAST(NULL AS NVARCHAR(40)) AS Incidencia,';

DECLARE @vecesSel INT = (LEN(@def) - LEN(REPLACE(@def, @patSel, ''))) / LEN(@patSel);
DECLARE @vecesVac INT = (LEN(@def) - LEN(REPLACE(@def, @patVac, ''))) / LEN(@patVac);

IF @vecesSel <> 1 OR @vecesVac <> 2
BEGIN
    RAISERROR('El SP no tiene la forma esperada (SELECT x%d, ramas vacías x%d). No se modificó nada: revisar el reemplazo a mano.', 16, 1, @vecesSel, @vecesVac);
    RETURN;
END

SET @def = REPLACE(@def, @patSel, @newSel);
SET @def = REPLACE(@def, @patVac, @newVac);

/* CREATE -> ALTER (el SP se creó con 'CREATE   PROCEDURE', con espacios de más). */
DECLARE @posCreate INT = CHARINDEX('CREATE', @def);
IF @posCreate = 0 OR CHARINDEX('PROCEDURE', @def) < @posCreate
BEGIN
    RAISERROR('No se pudo ubicar el CREATE PROCEDURE en la definición. No se modificó nada.', 16, 1);
    RETURN;
END
SET @def = STUFF(@def, @posCreate, 6, 'ALTER');

EXEC sp_executesql @def;
PRINT 'OK: sp_ObtenerAuditoriasFiltradas ahora devuelve Incidencia.';
GO

/* ---------------------------------------------------------------------------
   3) Verificación
   --------------------------------------------------------------------------- */
SELECT
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_SCHEMA = 'calidad' AND TABLE_NAME = 'Auditorias' AND COLUMN_NAME = 'Incidencia') AS columna_ok,
    (SELECT COUNT(*) FROM sys.indexes WHERE name = 'IX_Auditorias_Incidencia') AS indice_ok,
    (SELECT CASE WHEN CHARINDEX('A_Ext.Incidencia', OBJECT_DEFINITION(OBJECT_ID('calidad.sp_ObtenerAuditoriasFiltradas'))) > 0
                 THEN 1 ELSE 0 END) AS sp_ok;
GO
