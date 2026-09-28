/* ============================================================================
   Feature — Planificador: las restricciones que tenía la planilla de Excel
   Fecha: 2026-09-07 (posterior a 2026-09-07_planificador_demanda_total.sql)
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Cuatro columnas en planificacion.Skill y dos en planificacion.Requerimiento.

   POR QUÉ
   -------
   Voltara se dimensionó durante años con una planilla de Excel: el módulo VBA de
   T&C Limited (ErlangB / ErlangC / Agents / ASA / SLA) más una función propia,
   `asesores`, que le agrega tres restricciones sobre el resultado de Erlang C:

       Ase = Agents(NS, Antesde, Llamadas*2, TMO)      ' 80% en 20s
       rutina:
         NiAt   = 1 - ErlangB(Ase, intensidad)          ' nivel de atención
         TiMeEs = ASA(Ase, Llamadas*2, TMO)             ' tiempo medio de espera
         NiSe2  = SLA(Ase, Antesde2, Llamadas*2, TMO)   ' segundo nivel de servicio
         If NiAt < NA Or TiMeEs > TME Or NiSe2 < NS2 Then Ase = Ase + 1: GoTo rutina

   El motor nuevo ya reproducía la primera parte EXACTAMENTE —Erlang B, Erlang C y
   el nivel de servicio coinciden a 1e-16, y la dotación da idéntica en todos los
   casos probados— pero no tenía las tres restricciones de abajo. Sin ellas, el
   planificador podía dar un número más chico que el del Excel y nadie iba a saber
   por qué. Estas columnas las hacen configurables por skill.

   SOBRE EL NIVEL DE ATENCIÓN: DOS DEFINICIONES QUE NO SON PARECIDAS
   ------------------------------------------------------------------
   La planilla mide el nivel de atención como `1 - ErlangB`. Erlang B modela un
   sistema SIN cola: la llamada que llega y no encuentra operador libre se pierde.
   En un call center con cola, la enorme mayoría de esas llamadas espera y termina
   siendo atendida, así que ese número es sistemáticamente PESIMISTA.

   Con 195 llamadas de TMO 180s y la dotación de 80/20 (24 operadores):

       nivel de atención por Erlang B (planilla)      94,3%
       nivel de atención por Erlang A (paciencia real) 99,4%
       lo que efectivamente pasó en agosto 2026        96,7%   (Emergencias)

   O sea que la realidad cae ENTRE las dos. Por eso no se elige una y se descarta
   la otra: `MinNivelAtencionB` permite reproducir el criterio histórico cuando hay
   que comparar contra el número viejo, y el requerimiento guarda las dos lecturas
   para que la diferencia esté a la vista en vez de escondida en una fórmula.

   NINGUNA SE SIEMBRA CON VALOR
   -----------------------------
   Las cuatro nacen en NULL, o sea sin restringir. Aplicar la migración no cambia
   ningún número: recién cuando alguien cargue un TME o un nivel de atención el
   dimensionamiento se mueve. Es a propósito — activar de golpe el criterio de
   Erlang B subiría la dotación de todos los intervalos sin que nadie lo haya
   decidido.

   CÓMO CORRER
   -----------
   Después de las tres migraciones anteriores. Idempotente y aditiva.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF COL_LENGTH('planificacion.Skill', 'MaxAsaSeg') IS NULL
BEGIN
    ALTER TABLE planificacion.Skill ADD
        -- "TME" de la planilla: techo del tiempo medio de espera, en segundos.
        MaxAsaSeg          SMALLINT     NULL,
        -- Segundo nivel de servicio (NS2 / Antesde2), típicamente más laxo y a
        -- un plazo más largo: por ejemplo 95% en 60s además del 80% en 20s.
        ObjetivoNds2       DECIMAL(4,3) NULL,
        UmbralSeg2         SMALLINT     NULL,
        -- Piso de nivel de atención medido como 1 - Erlang B (criterio viejo).
        MinNivelAtencionB  DECIMAL(5,4) NULL;
    PRINT 'Restricciones de la planilla agregadas a planificacion.Skill.';
END
ELSE PRINT 'planificacion.Skill ya tenía las restricciones de la planilla.';
GO

IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Plan_Skill_Planilla')
    ALTER TABLE planificacion.Skill DROP CONSTRAINT CK_Plan_Skill_Planilla;
GO

ALTER TABLE planificacion.Skill ADD CONSTRAINT CK_Plan_Skill_Planilla CHECK (
        (MaxAsaSeg         IS NULL OR MaxAsaSeg > 0)
    AND (ObjetivoNds2      IS NULL OR (ObjetivoNds2 > 0 AND ObjetivoNds2 <= 1))
    AND (UmbralSeg2        IS NULL OR UmbralSeg2 > 0)
    AND (MinNivelAtencionB IS NULL OR (MinNivelAtencionB > 0 AND MinNivelAtencionB <= 1))
    -- Un segundo nivel de servicio sin su umbral no significa nada, y al revés
    -- tampoco: o vienen los dos o no viene ninguno.
    AND ((ObjetivoNds2 IS NULL AND UmbralSeg2 IS NULL)
      OR (ObjetivoNds2 IS NOT NULL AND UmbralSeg2 IS NOT NULL))
);
GO

IF COL_LENGTH('planificacion.Requerimiento', 'AsaSeg') IS NULL
BEGIN
    ALTER TABLE planificacion.Requerimiento ADD
        -- Tiempo medio de espera proyectado, en segundos. La operación lo mira en
        -- su tablero (columna ASA), así que tiene que estar también acá.
        AsaSeg           DECIMAL(8,2) NULL,
        -- Nivel de atención por el criterio de la planilla (1 - Erlang B). El de
        -- Erlang A ya está, como complemento de la columna Abandono.
        NivelAtencionB   DECIMAL(5,4) NULL;
    PRINT 'AsaSeg y NivelAtencionB agregados a planificacion.Requerimiento.';
END
ELSE PRINT 'planificacion.Requerimiento ya tenía AsaSeg y NivelAtencionB.';
GO

/* ---------------------------------------------------------------------------
   PARA REPRODUCIR EXACTAMENTE EL DIMENSIONAMIENTO HISTÓRICO de un skill, cargar
   los mismos parámetros con los que se llamaba a `asesores` en la planilla. Por
   ejemplo, si se usaba NA = 98% y TME = 30 segundos en EMERGENCIAS:

UPDATE planificacion.Skill
SET MinNivelAtencionB = 0.9800, MaxAsaSeg = 30
WHERE CampanaID = 20 AND SkillID = 6;

   Conviene hacerlo de a un skill y comparar el requerimiento antes y después:
   el criterio de Erlang B es caro (ver el encabezado) y es mejor que el salto de
   dotación se vea skill por skill y no todo junto.
--------------------------------------------------------------------------- */

/* ====================================================================
   VERIFICACIÓN (no modifica nada)
   ==================================================================== */
SELECT SkillID, Nombre, ObjetivoNds, UmbralSeg, MaxAbandono,
       MaxAsaSeg, ObjetivoNds2, UmbralSeg2, MinNivelAtencionB
FROM planificacion.Skill
WHERE CampanaID = 20
ORDER BY SkillID;
GO
