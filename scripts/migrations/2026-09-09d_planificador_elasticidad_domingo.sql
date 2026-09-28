/* ============================================================================
   Feature — Planificador: el domingo no responde al clima como un martes
   Fecha: 2026-09-09 (posterior a 2026-09-09c_planificador_nivel_gbdt.sql)
   Autor: equipo Acme

   QUE AGREGA
   ----------
   Una columna en planificacion.Campana. No toca ninguna tabla ni ningun dato.

       ClimaElasticidadTipoDia   0   <- NACE APAGADA

   Con la columna en 0 el pronostico queda EXACTAMENTE como esta hoy.


   ============================================================================
   EL PROBLEMA, MEDIDO
   ============================================================================

   Operaciones reporto que los fines de semana el pronostico venia MUY por
   encima de lo real y que el forecast que manda el cliente pegaba mejor.
   Verificado, dia por dia (demanda total del cliente):

       fecha        real   nuestro          cliente
       2026-08-22   5698    6013  (+6%)     4162  (-27%)
       2026-08-23   3426    6713  (+96%)    3033  (-11%)
       2026-08-29   3749    4060  (+8%)     3776   (+1%)
       2026-08-30   2254    3488  (+55%)    3120  (+38%)
       2026-09-05   3274    5260  (+61%)    4077  (+25%)
       2026-09-06   3407    5709  (+68%)    3385   (-1%)

   La causa NO es el perfil (para un domingo dice ~3.300 y los domingos reales
   rondan 3.400). Es el FACTOR DE CLIMA. Descompuesto:

       fecha        real   perfil   xclima   xnivel    pronostico
       2026-08-23   3426     3274     1,99     1,16          7516
       2026-09-06   3407     3297     1,87     1,13          6991

   Y el mecanismo es estructural: el modelo de clima se ajusta POR SKILL -lo
   cual es correcto, EMERGENCIAS tiene R2 0,44 y COMERCIAL 0,07- pero un domingo
   de Voltara es 98,9% EMERGENCIAS mientras que un dia habil es 55% EMERGENCIAS y
   40% COMERCIAL. O sea que el domingo recibe los factores MAS GRANDES justo
   donde el modelo menos se sostiene. Medido sobre los ultimos 60 dias: factor
   medio x1,12 en habiles contra x1,26 en findes, con maximos de 1,48 y 1,99.

   Regresando log(real/pronosticado) contra log(factor) sobre 464 dias:

       tipo       n     pendiente   elasticidad   correlacion
       habil    312       -0,039        0,96         -0,04
       sabado    65       -0,099        0,90         -0,12
       domingo   66       -0,417        0,58         -0,34
       feriado   20       -0,186        0,81         -0,24

   En dias habiles el factor esta bien. El domingo cumple el 58% de lo que se le
   pide, con correlacion -0,34: es sistematico, no ruido.


   ============================================================================
   QUE HACE, Y CUANTO DA
   ============================================================================

   Eleva el factor de clima a un exponente alfa en los domingos. El alfa NO se
   escribe a mano: se mide sobre los domingos de los ultimos 365 dias y se
   encoge hacia 1 -o sea hacia no corregir- con n/(n+15). Si el domingo vuelve a
   responder al clima, el factor vuelve a 1 solo. Hoy converge a 0,711.

   LA VENTANA ES DE 365 Y NO DE 180, y eso costo una corrida: con 180 dias el
   alfa oscila alrededor del umbral (0,816 / 0,929 / 0,885 en los cortes de
   agosto) y la correccion se prende y se apaga sola de una semana a la otra.
   Con 365 da 0,711 y coincide con la ventana completa, que es la firma de un
   parametro ESTRUCTURAL -un domingo es 98,9% EMERGENCIAS y eso no cambia de mes
   a mes- y no de un regimen.

   MAPE diario del total, medido con el codigo que se despliega:

                       464 dias        ultimos 180      ultimos 90
       habil         13,3 -> 13,3     13,2 -> 13,2     14,2 -> 14,2
       sabado        23,1 -> 23,4     23,7 -> 24,1     30,3 -> 30,8
       domingo       29,4 -> 28,8     33,7 -> 32,2     45,0 -> 41,4
       feriado       22,8 -> 22,4     19,0 -> 18,2     17,5 -> 16,3

   El efecto CRECE al acercarse al presente. El sabado empeora unas decimas
   aunque no se lo toque: comparte la correccion de nivel con el domingo.

   Y HASTA DONDE LLEGA, que hay que decirlo: los seis fines de semana que
   motivaron esto siguen mal, solo que menos. El 23/08 pasa de +96% a +81% y el
   06/09 de +68% a +52%. Sobre el ano el total no se mueve (WAPE 21,5%).

   POR ESO NACE APAGADA: no es una mejora universal, y se decide mirandola en la
   pestana Comparacion con el selector propio.

   SOLO EL DOMINGO, y eso esta medido: el sabado converge a alfa ~0,95 -no lo
   necesita- y aplicarselo igual le mete el ruido del ajuste (23,44% -> 24,32%).


   ============================================================================
   PROBADO Y DESCARTADO para el mismo problema (no reintentar sin medir)
   ============================================================================

   - Topear el factor en los dias no habiles: EMPEORA. Tope 1,9 -> 29,80%;
     1,7 -> 29,93%; 1,5 -> 29,98%; 1,3 -> 30,15%, contra 29,65% sin tope.
   - Sacarle el clima al domingo del todo: 32,14%, mucho peor. La senal existe,
     esta mal escalada.
   - Partir la correccion de nivel en sabado / domingo+feriado (3 buckets) o en
     los cuatro tipos: 30,62% y 30,18% contra 29,65%. En 28 dias hay cuatro
     domingos. Con ventana mas larga para los no habiles, peor todavia.
   - Subir el peso del GBDT de planificador_nivel en los fines de semana. El
     peso optimo in-sample era tentador (domingo 0,8 -> 27,4% contra 29,3%) pero
     NO SOBREVIVE LA VALIDACION CRUZADA: el sabado pide 0,80 en una mitad del
     periodo y 0,25 en la otra, y evaluado cruzado empeora en las dos
     direcciones. El domingo mejora en un sentido y empeora en el otro.
   - Combinar con el pronostico del cliente por dia de semana y ventana movil:
     29,7% -> 29,0% sobre el ano y 45,3% -> 43,2% sobre los ultimos 90 dias.
     Es la alternativa viva y da menos que esto; queda para reintentar.

   ============================================================================
   Y EL DATO QUE HAY QUE TENER PRESENTE ANTES DE INVERTIR MAS
   ============================================================================

   Un pronostico CLARIVIDENTE -la mediana de los cuatro mismos dias de semana
   anteriores y los cuatro POSTERIORES- da 43,0% en sabados y 43,9% en domingos.
   Nuestro pronostico da 23,4% y 29,7%. O sea que ya le ganamos por lejos a
   alguien que ve el futuro: el nivel de un fin de semana cambia tanto de una
   semana a otra que la mayor parte del error no es corregible con mas modelo.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('planificacion.Campana')
                 AND name = 'ClimaElasticidadTipoDia')
BEGIN
    ALTER TABLE planificacion.Campana ADD
        /* 0 = apagada. El alfa no es configurable a proposito: lo mide el
           modelo sobre una ventana movil. Esto es solo la llave de luz. */
        ClimaElasticidadTipoDia BIT NOT NULL
            CONSTRAINT DF_Plan_Campana_ClimaElasticidad DEFAULT (0);
    PRINT 'Columna de elasticidad del clima por tipo de dia agregada (apagada).';
END
ELSE PRINT 'planificacion.Campana ya tenia ClimaElasticidadTipoDia.';
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT CampanaID, ClimaActivo, ClimaElasticidadTipoDia, NivelGbdt, NivelGbdtPeso
FROM planificacion.Campana ORDER BY CampanaID;
GO
