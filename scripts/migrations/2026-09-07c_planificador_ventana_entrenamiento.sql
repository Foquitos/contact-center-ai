/* ============================================================================
   Feature — Planificador: la ventana de entrenamiento pasa a ser configurable
   Fecha: 2026-09-07 (posterior a 2026-09-07b_planificador_restricciones_planilla.sql)
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Dos columnas en planificacion.Campana: SemanasBase y DiasNivelReciente.

   OJO: ESTA MIGRACIÓN SÍ MUEVE LOS NÚMEROS
   -----------------------------------------
   A diferencia de las anteriores, esta cambia el pronóstico de toda la campaña.
   Es a propósito, y está medido. Lo que había cableado en el código eran 8
   semanas de línea de base y 14 días de corrección de nivel; los valores nuevos
   son 52 y 28.

   POR QUÉ
   -------
   La línea de base toma, para cada intervalo, la mediana del mismo día de semana
   a la misma hora en las últimas N semanas. Con N = 8, un evento de demanda que
   dura dos semanas —la ola de calor de agosto de 2026, con la demanda total de
   Voltara pasando de ~8.000 llamadas diarias a 13.000-15.000— ocupa la CUARTA
   PARTE de la ventana. Y para un día de semana puntual es peor: de los 8 sábados
   que entran a la mediana, cuatro cayeron adentro del evento. Ahí la mediana deja
   de proteger, porque la mitad de las muestras están contaminadas.

   Eso es exactamente lo que se veía: el pronóstico de los fines de semana de
   fines de agosto se iba +65%, +102%, +154% por encima de lo real, mientras que
   el XGBoost de dbo.Forecast —entrenado sobre más de un año, donde dos semanas
   raras son el 4% de los datos— erraba entre -8% y +47% esos mismos días.

   MEDIDO CON EL BACKTEST, sobre dos períodos independientes y con 7 días de
   antelación (error absoluto medio del total diario):

                          8 semanas / 14 días     52 semanas / 28 días
       10/08 – 06/09            49,7%                    31,0%
       01/06 – 30/06            38,5%                    35,2%
       fines de semana          82,9%                    48,4%
       sesgo (ago-sep)         -16,0%                    -2,2%

   No es una corazonada: se puede volver a correr desde la pestaña Comparación
   del planificador, que además muestra al lado el error de dbo.Forecast.

   LO QUE SE PROBÓ Y NO SIRVIÓ
   ----------------------------
   Calcular la corrección de nivel por separado para días hábiles y no hábiles.
   Suena razonable —una tormenta no golpea igual a un martes que a un domingo—
   pero medido no aporta nada (31,0% contra 30,9%) y con la ventana corta empeora
   (49,7% -> 55,5%). Queda registrado acá para que nadie lo vuelva a intentar sin
   medirlo primero.

   POR QUÉ CONFIGURABLE Y NO OTRA CONSTANTE
   -----------------------------------------
   52 semanas es lo mejor para Voltara en los dos períodos probados, pero Voltara
   es una campaña de emergencias, dominada por el clima. Hidra y Gasur no tienen
   por qué comportarse igual, y una campaña nueva no tiene un año de historia. El
   valor correcto se mide por campaña, y para eso tiene que poder cambiarse desde
   la pantalla.

   EFECTO COLATERAL: la corrida lee más historia (unos 400 días en vez de 180).
   Medido contra la tabla del IVR completo, la consulta pasa de 0,5s a 1,0s.

   CÓMO CORRER
   -----------
   Después de las tres migraciones anteriores. Idempotente y aditiva.
   Conviene, después de aplicarla, recalcular y mirar la pestaña Comparación.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF COL_LENGTH('planificacion.Campana', 'SemanasBase') IS NULL
BEGIN
    ALTER TABLE planificacion.Campana ADD
        -- Cuántas semanas mira la mediana estacional de la línea de base.
        SemanasBase        SMALLINT NOT NULL
            CONSTRAINT DF_Plan_Campana_SemanasBase DEFAULT (52),
        -- Ventana de la corrección de nivel, en días: cuánto se corrió el
        -- volumen reciente respecto del perfil.
        DiasNivelReciente  SMALLINT NOT NULL
            CONSTRAINT DF_Plan_Campana_DiasNivel DEFAULT (28);
    PRINT 'SemanasBase y DiasNivelReciente agregadas a planificacion.Campana.';
END
ELSE PRINT 'planificacion.Campana ya tenía la ventana de entrenamiento.';
GO

IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Plan_Campana_Ventana')
    ALTER TABLE planificacion.Campana DROP CONSTRAINT CK_Plan_Campana_Ventana;
GO

ALTER TABLE planificacion.Campana ADD CONSTRAINT CK_Plan_Campana_Ventana CHECK (
        -- Menos de 4 semanas no alcanza para tener 3 observaciones por día de
        -- semana, que es el mínimo que exige la línea de base; más de 3 años no
        -- tiene sentido con reportes que arrancan en 2025.
        SemanasBase BETWEEN 4 AND 156
    AND DiasNivelReciente BETWEEN 7 AND 120
);
GO

/* ====================================================================
   VERIFICACIÓN (no modifica nada)
   ==================================================================== */
SELECT CampanaID, IntervaloMin, SemanasBase, DiasNivelReciente,
       MaxOcupacion, ShrinkageDefault, PacienciaSeg
FROM planificacion.Campana
ORDER BY CampanaID;
GO
