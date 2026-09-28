/* ============================================================================
   Feature — Planificador: corregir el nivel por separado en hábiles y fines de semana
   Fecha: 2026-09-08 (posterior a 2026-09-07d_planificador_clima.sql)
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Una columna en planificacion.Campana: NivelPorTipoDeDia, en 1.

   OJO: ESTA MIGRACIÓN MUEVE LOS NÚMEROS DEL FIN DE SEMANA
   --------------------------------------------------------
   Nace ACTIVADA, a diferencia de la del clima. Es a propósito y está medido: no
   agrega una capacidad nueva sino que corrige un sesgo, y dejarlo apagado sería
   dejar el error puesto.

   EL PROBLEMA
   -----------
   El pronóstico de los sábados y domingos erraba mucho más que el de los días
   hábiles, y siempre para el mismo lado: +65%, +70%, +154%, +176% en los fines de
   semana de fines de agosto de 2026. Eso es SESGO, no ruido.

   La causa: en Voltara un día hábil y un fin de semana son prácticamente dos
   campañas distintas.

       mezcla de skills          día hábil    fin de semana
       COMERCIAL                   40,1%          0,1%
       EMERGENCIAS                 54,9%         98,9%

   COMERCIAL es facturación y trámites: es estable y no la mueve el clima. En un
   día hábil ancla el nivel; el sábado no ancla nada. Y como los días hábiles son
   el 83% del volumen, la corrección de nivel —que era UNA SOLA para toda la
   campaña— la escribían ellos, y después se le aplicaba igual al fin de semana.
   Medido: el pronóstico de fin de semana venía 15,5% por encima de lo real.

   LO MEDIDO
   ---------
   Error absoluto medio del total diario, cinco períodos, antelación 7 días:

                                  días hábiles   fin de semana
       corrección única               21,5%          54,2%
       corrección separada            22,8%          48,5%   <-- ESTE
       corrección separada,
         ventana triple para
         el fin de semana             22,5%          51,5%
       dbo.Forecast (el cliente)      43,3%          47,7%

   Se probó además estirar la ventana del fin de semana (en 28 días hay 20 días
   hábiles y sólo 8 de fin de semana, así que la corrección del sábado se calcula
   con muy pocas muestras). EMPEORA: 51,5% contra 48,5%. La ventana larga deja de
   seguir el nivel reciente, que es justamente para lo que está. Queda anotado acá
   para que nadie lo reintente sin medirlo.

   Con esto el planificador queda mejor que el pronóstico del cliente en los días
   hábiles por bastante (22,8% contra 43,3%) y a la par en el fin de semana (48,5%
   contra 47,7%).

   CÓMO CORRER
   -----------
   Después de las cinco migraciones anteriores. Idempotente y aditiva.
   Conviene recalcular después y mirar la pestaña Comparación.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF COL_LENGTH('planificacion.Campana', 'NivelPorTipoDeDia') IS NULL
BEGIN
    ALTER TABLE planificacion.Campana ADD
        NivelPorTipoDeDia BIT NOT NULL
            CONSTRAINT DF_Plan_Campana_NivelTipoDia DEFAULT (1);
    PRINT 'NivelPorTipoDeDia agregada a planificacion.Campana (activada).';
END
ELSE PRINT 'planificacion.Campana ya tenía NivelPorTipoDeDia.';
GO

/* ====================================================================
   VERIFICACIÓN (no modifica nada)
   ==================================================================== */
SELECT CampanaID, SemanasBase, DiasNivelReciente, NivelPorTipoDeDia,
       ClimaActivo, ClimaLat, ClimaLon
FROM planificacion.Campana ORDER BY CampanaID;
GO
