/* ============================================================================
   Ajuste de datos — Tabla "Bases y Talleres de Instalación Vantix"
   Fecha: 2026-09-02
   Autor: equipo Acme

   NO es un cambio de esquema: corrige la CONFIGURACIÓN de una tabla ya cargada.
   Lo mismo se puede hacer desde la pantalla (Gestor de Chatbots › la tabla › ⚙);
   este script existe para aplicarlo de una y dejar registro de por qué.

   QUÉ CORRIGE
   -----------
   1) LAS COLUMNAS CLAVE. Quedaron marcadas como clave cinco columnas:
      `Base / Taller`, `Tipo de Base`, `Nombre Comercial`, `Vehículos Aptos` y
      `Pago en Base`. Las últimas cuatro son ATRIBUTOS, no identificadores: nadie
      nombra una base diciendo "la que acepta pago" o "la Propia".

      Hoy el daño está contenido porque la tabla tiene 17 filas y entra entera en
      el prompt (modo efectivo 'completa'), así que el bot ve todo igual. El
      problema es a futuro: `Nombre Comercial` vale literalmente "Sucursal" en 5
      filas y "Taller" en otras. Si la tabla crece y pasa a modo 'lookup', la
      consulta "sucursal de palermo" —que es una consulta REAL del log— va a
      identificar esas 5 filas y el bot va a responder con seguridad la base
      equivocada. Se deja como clave únicamente `Base / Taller`.

      `Nombre Comercial` también sale de claves aunque contenga "Taller Autocentro":
      los nombres Autocentro ya están en `Base / Taller` ("Autocentro Tigre", "Base
      Móvil Autocentro JBJ"), así que no se pierde nada y se evita que "Sucursal"
      y "Taller" a secas identifiquen filas.

   2) LAS PALABRAS QUE LLEVAN A LA TABLA. Se agregan `direccion`, `mail`,
      `correo`, `suc` y `entrecalles`. Medido contra las 34 consultas históricas
      reales del bot: con los términos actuales rutean 25, con estos rutean 27
      (entran "direccion de castelar" y "mail de la suc de lomas de zamora").

      NO se agregan `instalar` ni `instalacion`, aunque parezcan obvios: se
      llevan puestas las consultas de procedimiento que hoy el RAG responde bien
      ("como se realiza la instalacion" +4,08, "un byd se puede instalar" +6,05).
      Verificado, no supuesto.

   3) EL TEXTO DE BÚSQUEDA (`ChatbotTablaFila.busqueda`). Es lo que hace que este
      script no sea solo un UPDATE de metadata: `busqueda` es el texto
      normalizado de las columnas CLAVE, precomputado al guardar para no
      normalizar miles de filas en cada consulta. Si se cambian las claves y no
      se recalcula, la tabla sigue buscando con el texto viejo y el cambio no
      tiene ningún efecto — sin error visible en ninguna parte.

      La normalización replica la de `app/chatbot_tablas.normalizar()`:
      minúsculas, sin acentos y espacios colapsados. Se hace con REPLACE
      explícitos y no con un truco de COLLATE a propósito: son ocho caracteres
      del español y el resultado es predecible, que es lo que importa cuando de
      esto depende encontrar una fila.

   NO TOCA
   -------
   Las FILAS (siguen las 17), la descripción ni su embedding (`descripcion_vector`
   sigue siendo válido porque la descripción no cambia), ni el índice del bot: una
   tabla no se indexa. El cambio se ve en <= CHATBOT_TABLAS_TTL_SECONDS (60s), sin
   reindexar ni reiniciar nada.

   PENDIENTE APARTE (no lo resuelve este script): a la tabla le faltan bases.
   Tiene 17 filas y CERO de tipo "Tercera", mientras el documento de origen
   (pagina_web.ChatbotDocMarkdown id 15, hoy activo=0) trae 28 bloques de base e
   incluye la sección de bases terceras, Ducasse y Haedo. Eso se completa cargando
   las que faltan (Ver filas › Agregar fila, o una planilla por "Actualizar una
   tabla de datos › Novedades"). Además 10 de las 17 filas tienen `Tipo de Base`
   vacío, y la propia pauta del bot dice que informar si es propia o tercera es
   obligatorio.

   IDEMPOTENTE: se puede correr más de una vez.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

DECLARE @tabla_id INT;

SELECT @tabla_id = t.id
FROM pagina_web.ChatbotTabla t
JOIN pagina_web.Chatbots c ON c.id = t.chatbot_id
WHERE c.slug = 'vantix' AND t.activo = 1
  AND t.nombre LIKE '%Bases y Talleres%';

IF @tabla_id IS NULL
BEGIN
    RAISERROR('No se encontró la tabla de bases de vantix. Verificá el nombre antes de seguir.', 16, 1);
    RETURN;
END

IF (SELECT COUNT(*) FROM pagina_web.ChatbotTabla t
    JOIN pagina_web.Chatbots c ON c.id = t.chatbot_id
    WHERE c.slug = 'vantix' AND t.activo = 1 AND t.nombre LIKE '%Bases y Talleres%') > 1
BEGIN
    RAISERROR('Hay más de una tabla candidata en vantix: revisá cuál corregir antes de seguir.', 16, 1);
    RETURN;
END

PRINT CONCAT('Corrigiendo la tabla id ', @tabla_id, '.');

BEGIN TRANSACTION;

/* --- 1 y 2) definición: claves y términos ------------------------------- */
UPDATE pagina_web.ChatbotTabla
SET
    claves = N'Base / Taller',
    columnas = N'[{"nombre": "Base / Taller", "descripcion": "Nombre de la base, taller o sucursal de instalación", "clave": true}, {"nombre": "Dirección", "descripcion": "Dirección física y entrecalles del lugar", "clave": false}, {"nombre": "Tipo de Base", "descripcion": "Condición de la base (Propia, Tercera, etc.)", "clave": false}, {"nombre": "Nombre Comercial", "descripcion": "Nombre comercial o fantasía del taller o establecimiento", "clave": false}, {"nombre": "Vehículos Aptos", "descripcion": "Tipos y tamaños de vehículos admitidos para instalación", "clave": false}, {"nombre": "Pago en Base", "descripcion": "Indica si se permite cobrar o pagar en el lugar (Sí/No)", "clave": false}, {"nombre": "Contactos / Observaciones", "descripcion": "Correos electrónicos de contacto y observaciones operativas", "clave": false}]',
    terminos = N'base,bases,taller,talleres,sucursal,sucursales,punto de instalacion,lugar de instalacion,red de bases,daytona,direccion,mail,correo,suc,entrecalles',
    updated_at = SYSUTCDATETIME()
WHERE id = @tabla_id;

PRINT CONCAT('Definición actualizada (', @@ROWCOUNT, ' fila).');

/* --- 3) texto de búsqueda de cada fila ----------------------------------- */
/* Réplica de app/chatbot_tablas.normalizar(): minúsculas, sin acentos y espacios
   colapsados, sobre el valor de la ÚNICA columna clave que queda.             */
/* LOWER va PRIMERO y los REPLACE después: así "SARANDÍ" y "Sarandí" terminan
   igual. Al revés, una mayúscula acentuada se escapaba del reemplazo y quedaba
   con el acento puesto.                                                       */
UPDATE f
SET busqueda = LTRIM(RTRIM(
        REPLACE(REPLACE(REPLACE(
            REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                LOWER(ISNULL(JSON_VALUE(f.datos, '$."Base / Taller"'), N'')),
            N'á', N'a'), N'é', N'e'), N'í', N'i'), N'ó', N'o'), N'ú', N'u'),
            N'ü', N'u'), N'ñ', N'n'),
        /* colapso de espacios: tres pasadas cubren cualquier corrida razonable */
        N'  ', N' '), N'  ', N' '), N'  ', N' ')
    ))
FROM pagina_web.ChatbotTablaFila f
WHERE f.tabla_id = @tabla_id;

PRINT CONCAT('Texto de búsqueda recalculado en ', @@ROWCOUNT, ' filas.');

COMMIT TRANSACTION;
GO

/* ------------------------------------------------------------------ control */
/* Se espera: 1 columna clave, 17 filas, ninguna con busqueda vacía, y ninguna
   que todavía arrastre los valores de los atributos que dejaron de ser clave
   (no puede quedar "propia", "sucursal" ni "taller daytona" en el texto de
   búsqueda de una fila cuyo nombre no los tenga).                             */
SELECT t.id, t.nombre, t.claves, t.filas_total,
       (SELECT COUNT(*) FROM pagina_web.ChatbotTablaFila f
        WHERE f.tabla_id = t.id)                                   AS filas,
       (SELECT COUNT(*) FROM pagina_web.ChatbotTablaFila f
        WHERE f.tabla_id = t.id AND LTRIM(RTRIM(ISNULL(f.busqueda, N''))) = N'')
                                                                   AS sin_busqueda,
       (SELECT COUNT(*) FROM pagina_web.ChatbotTablaFila f
        WHERE f.tabla_id = t.id AND f.busqueda LIKE N'%propia%')    AS arrastra_propia,
       (SELECT COUNT(*) FROM pagina_web.ChatbotTablaFila f
        WHERE f.tabla_id = t.id AND f.busqueda LIKE N'%|%')         AS arrastra_separador
FROM pagina_web.ChatbotTabla t
JOIN pagina_web.Chatbots c ON c.id = t.chatbot_id
WHERE c.slug = 'vantix' AND t.activo = 1 AND t.nombre LIKE '%Bases y Talleres%';

/* Muestra del texto de búsqueda ya recalculado (tiene que ser solo el nombre
   de la base, en minúsculas y sin acentos).                                  */
SELECT TOP (17) JSON_VALUE(f.datos, '$."Base / Taller"') AS base, f.busqueda
FROM pagina_web.ChatbotTablaFila f
JOIN pagina_web.ChatbotTabla t ON t.id = f.tabla_id
JOIN pagina_web.Chatbots c ON c.id = t.chatbot_id
WHERE c.slug = 'vantix' AND t.nombre LIKE '%Bases y Talleres%'
ORDER BY f.orden;
GO

PRINT 'Ajuste 2026-09-02c_vantix_tabla_claves_y_terminos COMPLETO.';
GO
