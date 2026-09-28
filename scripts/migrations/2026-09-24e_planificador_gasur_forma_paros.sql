/* ============================================================================
   Feature — Planificador: forma reciente, persistencia sin paros y ancla mensual (Gasur)
   Fecha: 2026-09-24 (posterior a 2026-09-24d_planificador_gasur.sql)
   Autor: equipo Acme

   QUÉ HACE
   --------
   1. Cuatro columnas nuevas en planificacion.Campana. Nacen APAGADAS: Voltara y
      Hidra quedan exactamente como estaban.
        FormaDias                  NULL = la forma del día (cómo se reparte el total
                                   entre las medias horas) sale de la mediana de
                                   SemanasBase semanas, como siempre; con N, de la
                                   suma de los últimos N días del mismo tipo (hábil,
                                   sábado, domingo-feriado). El total del día no cambia.
        PersistenciaSalteaEventos  1 = la persistencia no arrastra el desvío de un día
                                   marcado como atípico: lo saltea como a un feriado.
        AnclaMensualPeso           0 = apagado. Con peso, lo que queda del mes (desde
        AnclaMensualDesdeDias      AnclaMensualDesdeDias días de antelación) se lleva
                                   hacia el total del mes que manda el cliente en
                                   dbo.Forecast, descontando lo ya entrado.
   2. Las prende en Gasur (campaña 30): FormaDias 28, PersistenciaSalteaEventos 1
      y ancla mensual con peso 0,75 desde 7 días. Sólo si siguen en su valor de
      fábrica (no pisa lo que se haya tocado desde la pantalla).

   POR QUÉ (medido el 2026-09-24, backtest de un año, plan de la mañana)
   ----------------------------------------------------------------------
   A Gasur le llega el DESBORDE de su propio call center (y el 100% cuando ellos
   tienen un paro). Eso explica los dos problemas:

   - La FORMA del día depende de a qué hora les falta gente a ellos, no de cuándo
     llama la gente, y se mueve más rápido que una mediana de 26 semanas. Error por
     media hora aun acertando el total del día (15/07-20/09): 32,3% -> 27,0%
     (piso por azar 12,5%). En el año, WAPE por media hora 51,9% -> 46,1%; desde
     agosto 42,0% -> 38,0%. Probado con 14 días (peor) y 56 (igual que 28).
   - Los PAROS: el detector de atípicos ya los marca solo (x3 a x6 sobre lo
     esperado; 32 días desde 2024). Pero la persistencia arrastraba el paro al día
     siguiente, que encima viene BAJO (la gente ya pidió): el día después de un
     paro se pronosticaba +32% de más (error 34,6%, 7 casos). Salteándolo: 16,7%.
     El resto de los días casi igual (32,8% -> 32,5%, sesgo -2,5% -> -4,3%).
   - El TOTAL DEL MES del cliente (el mínimo que paga, cargado en dbo.Forecast
     'Gasur' repartido por hora) acierta: 8,1% de error mensual de oct-2025 a
     ago-2026, porque el desborde lo decide su dotación. Nuestro pronóstico a 7
     días sumado por mes erraba 21,6% y quedaba corto en la rampa del invierno.
     Anclando lo que queda del mes (con al menos 7 días por delante), total
     diario:
         a 7 días   peso 0: 45,1% sesgo -13,5%   peso 0,75: 40,6% sesgo -4,4%
         a 3 días   peso 0: 41,0%                peso 0,25: 40,6%  0,75: 43,0%
         a 1 día    peso 0: 37,3%                cualquier peso empeora
     Por eso va sólo desde 7 días. Se midió fuera del backtest de la pantalla
     (que pronostica día por día y no ve el resto del mes); el Laboratorio no lo
     puede probar.
   Resultado (año corrido, total diario sin los días de paro / media hora):
       plan de la mañana   32,8% / 51,9%  ->  32,5% / 46,1%
       a 7 días            44,4%          ->  ~40,6% con el ancla
   El error del total diario de hoy y mañana queda en ~33-37%: lo que manda ahí
   es cuánto desbordan ese día, y eso no lo sabemos.

   Junto con esto se corrigió en el código que `dias_a_excluir` leía el Hasta de los
   eventos como inclusivo: cada atípico sacaba también el día siguiente del
   entrenamiento. Aplica a todas las campañas (sin migración).

   CÓMO CORRER
   -----------
   Después de la 2026-09-24d. Idempotente y aditiva. El código la tolera ausente
   (lee NULL / 0 y no manda las columnas al guardar).
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF COL_LENGTH('planificacion.Campana', 'FormaDias') IS NULL
BEGIN
    ALTER TABLE planificacion.Campana ADD
        FormaDias                 TINYINT NULL,
        PersistenciaSalteaEventos BIT NOT NULL
            CONSTRAINT DF_Plan_Campana_PersSalteaEventos DEFAULT (0),
        AnclaMensualPeso          DECIMAL(4,3) NOT NULL
            CONSTRAINT DF_Plan_Campana_AnclaPeso DEFAULT (0),
        AnclaMensualDesdeDias     TINYINT NOT NULL
            CONSTRAINT DF_Plan_Campana_AnclaDesde DEFAULT (7);
    PRINT 'Columnas FormaDias, PersistenciaSalteaEventos y AnclaMensual* agregadas a planificacion.Campana.';
END
ELSE PRINT 'planificacion.Campana ya tenía FormaDias.';
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Plan_Campana_FormaDias')
BEGIN
    ALTER TABLE planificacion.Campana ADD CONSTRAINT CK_Plan_Campana_FormaDias
        CHECK ((FormaDias IS NULL OR FormaDias BETWEEN 7 AND 91)
               AND AnclaMensualPeso BETWEEN 0 AND 1
               AND AnclaMensualDesdeDias BETWEEN 0 AND 30);
    PRINT 'CK_Plan_Campana_FormaDias creado.';
END
GO

UPDATE planificacion.Campana
SET FormaDias = 28,
    PersistenciaSalteaEventos = 1,
    AnclaMensualPeso = 0.750,
    AnclaMensualDesdeDias = 7,
    ActualizadoEn = SYSUTCDATETIME()
WHERE CampanaID = 30
  AND FormaDias IS NULL
  AND PersistenciaSalteaEventos = 0
  AND AnclaMensualPeso = 0;
PRINT CONCAT('Gasur con forma reciente, persistencia sin paros y ancla mensual: ', @@ROWCOUNT, ' fila(s).');
GO

/* VERIFICACIÓN (no modifica nada) */
SELECT CampanaID, FormaDias, PersistenciaSalteaEventos, AnclaMensualPeso, AnclaMensualDesdeDias,
       PersistenciaPesoHoy, SemanasBase
FROM planificacion.Campana
ORDER BY CampanaID;
GO
