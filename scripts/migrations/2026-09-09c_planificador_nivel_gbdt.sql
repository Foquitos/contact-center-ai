/* ============================================================================
   Feature — Planificador: segunda opinion del NIVEL diario con arboles
             de decision (GBDT), promediada con el modelo de clima
   Fecha: 2026-09-09 (posterior a 2026-09-09b_planificador_pool_unico.sql)
   Autor: equipo Acme

   QUE AGREGA
   ----------
   Dos columnas en planificacion.Campana. No toca ninguna tabla ni ningun dato.

       NivelGbdt         0      <- NACE APAGADA
       NivelGbdtPeso   0,400    <- el peso medido, listo para cuando se prenda

   Con NivelGbdt = 0 el pronostico queda EXACTAMENTE como esta hoy. Hay un test
   que lo fija (peso 0 devuelve el ajuste intacto).


   ============================================================================
   POR QUE, Y CUANTO DA
   ============================================================================

   Antes que nada, el techo, porque ordena toda la discusion. Medido sobre 464
   dias (2025-06-01 a 2026-09-06), antelacion 7, WAPE por media hora sobre la
   demanda del cliente:

       pronostico de produccion                      22,7%
       con el NIVEL DIARIO REAL de cada cola         14,1%   <- techo del nivel
       con el nivel real y la mejor forma posible    13,9%
       piso de Poisson (ruido de llegada)             5,2%

   O sea que un modelo de nivel puede ganar 8,6 puntos y la forma intradia no
   da mas de 0,2: un estimador de forma que VE EL FUTURO (la mediana de los
   cuatro mismos dias de semana anteriores y los cuatro posteriores) mejora eso
   y nada mas. Por eso esto ataca el nivel del dia y no toca la curva.

   Un GBDT (HistGradientBoostingRegressor, scikit-learn, que YA esta en
   requirements.txt) estima el mismo cociente que estima el modelo de clima
   -cuantas llamadas tuvo el dia contra lo que decia el perfil estacional- pero
   sin imponerle forma funcional. NO reemplaza a la regresion: se promedia con
   ella en logaritmo.

   Y eso no es un capricho: el GBDT SOLO PIERDE (25,6% contra 22,7% de WAPE).
   Con 8.500 filas y nueve series aprende ruido que el ridge de doce rasgos no
   puede aprender. Mezclado gana, porque los dos se equivocan en lugares
   distintos:

       peso   WAPE 1/2h   MAPE diario   sesgo
       0,00     22,69%       19,71%     +0,3%   (produccion de hoy)
       0,25     21,81%       18,04%     +1,9%
       0,40     21,62%       17,54%     +2,6%   <- EL DEFAULT
       0,50     21,62%       17,35%     +3,1%

   Por tipo de dia (MAPE diario del total):

       habil     14,43%  ->  13,30%
       sabado    28,21%  ->  23,41%
       domingo   34,27%  ->  29,59%
       feriado   26,28%  ->  24,88%

   Y por trimestre, gana los SEIS del periodo en las dos metricas:

       trim      produccion        con la mezcla
       2025-Q2   19,16 / 16,06     16,30 / 11,62
       2025-Q3   22,42 / 20,70     21,19 / 18,46
       2025-Q4   23,62 / 19,84     22,74 / 17,61
       2026-Q1   24,93 / 18,82     23,77 / 17,10
       2026-Q2   18,32 / 16,12     17,54 / 14,48
       2026-Q3   26,24 / 25,75     25,79 / 23,48


   ============================================================================
   DECISIONES QUE ESTAN MEDIDAS (para que nadie las reintente sin medir)
   ============================================================================

   1. PERDIDA DE POISSON, no error cuadratico sobre el logaritmo. El cuadratico
      en log estima la MEDIANA condicional y al volver con la exponencial se
      queda corto (sesgo de Jensen). Con Poisson sobre el cociente ponderado por
      el perfil -que es un Poisson sobre el conteo con offset- se estima la
      MEDIA. Mejora el error Y baja el sesgo a la vez: 21,99/18,03/+2,6% paso a
      21,93/17,89/+2,3%.

   2. SIN RASGOS DE REZAGO. Meterle el nivel de los ultimos 7 y 28 dias parece
      obvio y es PEOR: 21,92% con ellos contra 21,62% sin ellos. Duplican lo que
      ya hace la correccion de nivel y la mezcla los contaba dos veces. Al reves
      tambien se probo: SOLO los rezagos da 33,9%, o sea que toda la senal esta
      en el clima y el calendario.

   3. NO ES "CORREGIR MENOS EL NIVEL". Como la rama del GBDT no lleva correccion
      de nivel, mezclar al 40% equivale a aplicar la correccion elevada a 0,6, y
      habia que descartar que la ganancia fuera esa. Medido: atenuar la
      correccion sola da 22,60% en el mejor caso contra 22,69%, o sea 0,09
      puntos. La ganancia es del modelo.

   4. EL SESGO NO SE CORRIGE CON UNA ESCALA. El peso 0,40 lleva el sesgo del ano
      de +0,3% a +2,6%. Corregirlo con el cociente real/pronosticado de los dias
      ya cerrados EMPEORA en todas las ventanas probadas (60, 90 y 180 dias), y
      empeora incluso aplicado al pronostico de produccion solo: 22,69% ->
      22,92%. El smearing de Duan tambien: saca el sesgo y cuesta medio punto.

   5. EL PRONOSTICO QUE MANDA EL CLIENTE COMO RASGO: probado y descartado. Con
      el reparto fijo en 50% desde septiembre de 2026, dbo.Forecast dividido por
      el porcentaje es una estimacion directa de la demanda del cliente, asi que
      valia la pena. Da 22,08% contra 22,04% de la misma configuracion sin el.

   6. NO HACE FALTA ABRIR LOS INTERVALOS. Con los rezagos afuera, la version que
      se calcula solo con la serie diaria da identico (21,62 contra 21,63). Por
      eso el modulo recibe lo mismo que planificador_clima y usa su helper de
      perfil.


   ============================================================================
   VERIFICADO DE PUNTA A PUNTA (no es solo el banco de pruebas)
   ============================================================================

   Corriendo el codigo que se despliega -planificador_nivel + baseline_estacional-
   sobre los mismos 464 dias:

       peso 0,00   WAPE 22,70%   MAPE diario 19,72%   sesgo +0,3%
       peso 0,40   WAPE 21,54%   MAPE diario 17,32%   sesgo +2,4%

   Con peso 0 reproduce el pronostico de hoy hasta el segundo decimal: prender
   esto es una decision, no un efecto lateral del deploy. Con 0,40 sale un poco
   MEJOR que en el banco (21,54 contra 21,62) porque cada fila de entrenamiento
   usa el perfil hasta el dia mismo, igual que planificador_clima, y el banco lo
   tomaba hasta siete dias antes. Se nota sobre todo en los feriados: 22,5%
   contra 24,9%.

   ============================================================================
   COMO SE PRENDE (y por que nace apagada)
   ============================================================================

   Regla de la casa: todo lo que cambia el pronostico se mira primero en la
   pestana Comparacion, con los datos de la campana, y recien despues se prende.
   El backtest acepta forzar la mezcla prendida o apagada sin tocar la
   configuracion (parametro `nivel` del endpoint), que es para eso.

   Cuando se decida:

       UPDATE planificacion.Campana SET NivelGbdt = 1 WHERE CampanaID = 20;

   OJO CON EL COSTO: el modelo se reajusta una vez por corrida (~1,5 s) y una
   vez por FECHA DE CORTE en el backtest, asi que un backtest de un mes agrega
   unos cuarenta segundos. La corrida normal (`recalcular`) pasa de ~4 a ~6 s.

   Depende de scikit-learn, que ya esta en requirements.txt (1.7.2). Si faltara,
   el modulo devuelve None y el pronostico queda como esta hoy, sin romperse.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('planificacion.Campana')
                 AND name = 'NivelGbdt')
BEGIN
    ALTER TABLE planificacion.Campana ADD
        /* 0 = apagada. Con 0 el pronostico es identico al de hoy. */
        NivelGbdt BIT NOT NULL CONSTRAINT DF_Plan_Campana_NivelGbdt DEFAULT (0),
        /* Cuanto pesa la segunda opinion en el promedio logaritmico del nivel
           del dia. 0,400 es el valor medido sobre 464 dias; 0 la apaga aunque
           NivelGbdt este en 1. */
        NivelGbdtPeso DECIMAL(4,3) NOT NULL
            CONSTRAINT DF_Plan_Campana_NivelGbdtPeso DEFAULT (0.400);
    PRINT 'Columnas de la segunda opinion del nivel agregadas (apagada).';
END
ELSE PRINT 'planificacion.Campana ya tenia NivelGbdt.';
GO

/* El tope de 0,6 no es el medido sino el limite de lo razonable: con peso 1 el
   pronostico seria el GBDT solo, que MIDE PEOR que produccion (25,6 contra
   22,7). El CHECK protege de un UPDATE a mano, que es el camino que no pasa por
   el codigo. */
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints
               WHERE name = 'CK_Plan_Campana_NivelGbdtPeso')
    ALTER TABLE planificacion.Campana ADD CONSTRAINT CK_Plan_Campana_NivelGbdtPeso
        CHECK (NivelGbdtPeso BETWEEN 0 AND 0.6);
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT CampanaID, ClimaActivo, NivelGbdt, NivelGbdtPeso,
       SemanasBase, DiasNivelReciente, NivelPorTipoDeDia
FROM planificacion.Campana ORDER BY CampanaID;
GO
