/* ============================================================================
   Feature — Planificador: el clima del área de concesión como insumo del pronóstico
   Fecha: 2026-09-07 (posterior a 2026-09-07c_planificador_ventana_entrenamiento.sql)
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   La tabla planificacion.Clima y tres columnas en planificacion.Campana
   (ClimaLat, ClimaLon, ClimaActivo).

   POR QUÉ
   -------
   El backtest mostró que el error del pronóstico es casi todo de NIVEL —cuántas
   llamadas entran— y no de forma: sabiendo el total del día, el error por media
   hora baja de 44% a 21%. Y el nivel de una distribuidora eléctrica no lo explica
   el calendario, lo explica el tiempo.

   NO ES "OLA DE CALOR": ES UNA U
   -------------------------------
   La hipótesis de arranque era el calor y estaba equivocada. Medido sobre dos
   años de demanda total del cliente contra el clima real del área, los quince
   días de mayor demanda se parten en dos grupos y el medio no aparece:

       2026-01-13   máxima 31,5 °C   32.723 llamadas    calor
       2025-12-31   máxima 39,2 °C   28.276             calor extremo
       2025-07-02   máxima  7,9 °C   25.231             FRÍO
       2026-07-06   máxima 11,0 °C   25.116             FRÍO

   El pico de agosto de 2026 que rompía el pronóstico —y que en la migración
   2026-09-07c quedó descrito como "ola de calor", lo cual es un error que este
   encabezado corrige— fue en realidad una ola de FRÍO: máximas de 10 a 14 °C
   entre el 10 y el 14 de agosto, más dos temporales (38 mm y ráfagas de 64 km/h
   el 6 de agosto).

   Por eso se guardan la máxima y la mínima APARENTES (que ya incluyen humedad y
   viento: en Buenos Aires la diferencia con la de bulbo seco llega a 8 °C en
   enero, y es justo la que decide si se prende el aire), la lluvia y las ráfagas.
   La humedad y el viento crudos se guardan igual, para poder volver a probarlos,
   aunque medidos NO aportan (ver abajo).
   Una regresión sobre la temperatura a secas le pone un signo a la U y no ve
   ninguno de los dos picos.

   CUÁNTO EXPLICA
   --------------
   Regresión del logaritmo del cociente entre lo real y el perfil estacional,
   contra doce rasgos, sobre ~1.100 días: R² entre 0,41 y 0,46 según el corte, y
   el desvío del residuo baja entre 23% y 26%.

   Los doce rasgos son ocho de clima del día (grados-día de refrigeración y de
   calefacción, sus cuadrados, los del día anterior, lluvia y ráfagas) más cuatro
   de PERSISTENCIA: grados-día acumulados de tres y de siete días. La persistencia
   no es un adorno — lo que rompe la red no es el día caluroso, es el tercero
   seguido — y medida es la diferencia entre mejorar el pronóstico y empeorarlo en
   verano.

   MEDIDO CON EL BACKTEST, cinco períodos, antelación 7 días
   (error absoluto medio del total diario / error ponderado por media hora):

       período                sin clima      con clima      dbo.Forecast
       ago-sep (ola de frío)  29,9% / 31,3%  30,9% / 30,8%  27,5% / 35,0%
       junio (invierno)       35,3% / 34,5%  22,5% / 28,3%  36,9% / 35,7%
       enero (verano)         42,3% / 39,9%  42,3% / 43,7%  25,4% / 33,7%
       octubre (templado)     34,5% / 32,8%  30,1% / 31,6%  38,9% / 44,6%
       marzo (calor tardío)   53,8% / 45,7%  25,3% / 30,9%  93,0% / 89,1%
       PROMEDIO               39,1% / 36,9%  30,2% / 33,1%  44,4% / 47,6%

   Gana en tres de los cinco períodos y empata en enero. El único frente que
   queda abierto es el verano medido por media hora (39,9% -> 43,7%): el factor
   llega al tope de 2,20 y ahí el modelo deja de poder representar lo que pasa.

   QUÉ RASGOS SE PROBARON Y NO ENTRARON
   -------------------------------------
   Se midió grupo por grupo, no se eligió por intuición:

     - humedad, viento y amplitud térmica: la PEOR combinación de todas (34,7%
       de promedio contra 30,2%). Vienen gratis en la misma llamada a la API y
       aun así empeoran — la humedad ya está adentro de la temperatura aparente.
     - estacionalidad anual en armónicos: 32,1%, o sea igual que no ponerla.
     - calendario (ciclo de facturación, vísperas, fines de semana largos,
       vacaciones): 31,8%, dentro del ruido.
     - los veinticinco rasgos juntos: 30,3%, que EMPATA con los doce usando trece
       parámetros más. Y no es un empate parejo: es el mejor en agosto-septiembre
       (21,7%) y el peor de todos en enero (47,4%), que es la firma clásica del
       sobreajuste — mejora cerca del final del entrenamiento y empeora lejos.

   Todos quedan implementados y medibles en planificador_clima.GRUPOS, para
   volver a probarlos cuando haya más historia. Hoy no se ganan el lugar.

   NACE APAGADO
   ------------
   ClimaActivo arranca en 0, incluso para Voltara, que ya viene con las coordenadas
   cargadas. Aplicar esta migración NO cambia ningún número. La secuencia pensada
   es: aplicar -> correr scripts/clima_voltara.py -> ir a la pestaña Comparación y
   medir con clima y sin clima sobre los mismos días -> recién ahí activarlo desde
   Configuración. Un pronóstico que cambia solo porque corrió un cron no es algo
   que nadie quiera descubrir un lunes a la mañana.

   SOBRE LA FUENTE DE DATOS
   ------------------------
   El script de descarga usa Open-Meteo, que no pide API key. OJO: su plan
   gratuito es para uso NO comercial. Antes de dejarlo en producción hay que
   resolverlo: o se contrata el plan comercial, o se cambia la fuente por el
   Servicio Meteorológico Nacional (https://ssl.smn.gob.ar/dpd/descarga_opendata.php),
   que es dato público argentino y no tiene esa restricción. La tabla y el modelo
   son iguales para las dos: lo único que cambia es el script que la llena, y por
   eso la columna Origen guarda de dónde salió cada fila.

   CÓMO CORRER
   -----------
   Después de las cuatro migraciones anteriores. Idempotente y aditiva.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF OBJECT_ID('planificacion.Clima', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.Clima (
        CampanaID      INT           NOT NULL,
        Fecha          DATE          NOT NULL,

        -- Temperatura de bulbo seco. Se guarda para poder explicar el dato a un
        -- humano ("hizo 34 grados"), aunque el modelo use la aparente.
        TempMax        DECIMAL(5,2)  NULL,
        TempMin        DECIMAL(5,2)  NULL,
        TempMedia      DECIMAL(5,2)  NULL,

        -- Temperatura APARENTE: la que usa el modelo. Incluye humedad y viento.
        AparenteMax    DECIMAL(5,2)  NULL,
        AparenteMin    DECIMAL(5,2)  NULL,

        LluviaMm       DECIMAL(6,2)  NULL,
        VientoKmh      DECIMAL(5,2)  NULL,
        RafagaKmh      DECIMAL(5,2)  NULL,
        HumedadPct     DECIMAL(5,2)  NULL,

        -- 1 = todavía es pronóstico meteorológico, 0 = ya es observación.
        -- Importa: el modelo se ENTRENA sólo con observaciones. Entrenar con
        -- pronósticos meteorológicos le enseñaría el error del meteorólogo.
        EsPronostico   BIT           NOT NULL
            CONSTRAINT DF_Plan_Clima_EsPron DEFAULT (0),

        Origen         VARCHAR(40)   NOT NULL,
        ActualizadoEn  DATETIME2(0)  NOT NULL
            CONSTRAINT DF_Plan_Clima_Act DEFAULT (SYSUTCDATETIME()),

        CONSTRAINT PK_Plan_Clima PRIMARY KEY (CampanaID, Fecha),
        CONSTRAINT FK_Plan_Clima_Campana FOREIGN KEY (CampanaID)
            REFERENCES planificacion.Campana (CampanaID)
    );
    PRINT 'planificacion.Clima creada.';
END
ELSE PRINT 'planificacion.Clima ya existía.';
GO

IF COL_LENGTH('planificacion.Campana', 'ClimaActivo') IS NULL
BEGIN
    ALTER TABLE planificacion.Campana ADD
        -- Punto representativo del área de concesión. Uno solo alcanza: el área
        -- de Voltara son unos 3.300 km2 de conurbano sur, y la diferencia de
        -- temperatura entre sus extremos es menor que la que hay entre el día y
        -- la noche del mismo punto.
        ClimaLat     DECIMAL(9,6) NULL,
        ClimaLon     DECIMAL(9,6) NULL,
        -- Arranca APAGADO. Ver el encabezado: activarlo cambia el pronóstico y
        -- esa decisión se toma después de medirla, no como efecto de un deploy.
        ClimaActivo  BIT NOT NULL
            CONSTRAINT DF_Plan_Campana_ClimaActivo DEFAULT (0);
    PRINT 'ClimaLat, ClimaLon y ClimaActivo agregadas a planificacion.Campana.';
END
ELSE PRINT 'planificacion.Campana ya tenía las columnas de clima.';
GO

/* Coordenadas del área de concesión de Voltara: conurbano sur (Avellaneda, Lanús,
   Lomas de Zamora, Quilmes, Berazategui) más el sur de CABA. */
UPDATE planificacion.Campana
SET ClimaLat = -34.700000, ClimaLon = -58.400000
WHERE CampanaID = 20 AND ClimaLat IS NULL;
GO

/* ====================================================================
   VERIFICACIÓN (no modifica nada)
   ==================================================================== */
SELECT CampanaID, ClimaLat, ClimaLon, ClimaActivo, SemanasBase, DiasNivelReciente
FROM planificacion.Campana ORDER BY CampanaID;
GO
SELECT COUNT(*) AS filas_de_clima FROM planificacion.Clima;
GO
