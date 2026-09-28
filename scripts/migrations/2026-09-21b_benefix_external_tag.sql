/* ============================================================================
   2026-09-21b — Benefix.Interacciones.ExternalTag
   ----------------------------------------------------------------------------
   Aditiva e idempotente. Aplicar ANTES de deployar el código que la usa: el
   cargador (scripts/benefix_genesys.py) la inserta y el builder
   (SQL_query.get_filtered_data_Benefix) la selecciona; sin la columna, los dos
   fallan.

   externalTag es un dato de la CONVERSACIÓN en Genesys: lo que el cliente marcó en
   el IVR. Medido el 2026-09-19: 16 dígitos que empiezan con 5117 (parece el número
   de tarjeta Benefix), 7 dígitos o 5 dígitos. Lo tiene ~1 de cada 3 conversaciones
   con voz, y casi todas son de IVR sin agente. Se repite en cada tramo de agente de
   la conversación.

   Después de aplicarla, recargar lo ya bajado para completar la columna:
       python scripts/benefix_genesys.py historico --desde 2026-07-01 --forzar
   ============================================================================ */
IF COL_LENGTH('Benefix.Interacciones', 'ExternalTag') IS NULL
    ALTER TABLE Benefix.Interacciones ADD ExternalTag varchar(64) NULL;
GO
