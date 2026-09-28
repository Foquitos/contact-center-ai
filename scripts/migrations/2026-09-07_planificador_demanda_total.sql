/* ============================================================================
   Feature — Planificador: pronóstico sobre la demanda TOTAL del cliente
   Fecha: 2026-09-07 (posterior a 2026-09-03 y 2026-09-03b)
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1. Permite Porcentaje = 0 en planificacion.Asignacion.
   2. Siembra los tramos de asignación MIDIÉNDOLOS de `dbo.[Voltara Enerval informe IVR]`,
      en vez de escribir números a mano.

   POR QUÉ
   -------
   Con la descarga completa del portal de Enerval (25,4 millones de llamadas desde
   2023-01-01, con BPO y Skill poblados para TODOS los contact centers) el
   planificador deja de tener su problema de fondo. Hasta ahora la serie era
   `demanda_de_Voltara x nuestro_share` mezcladas, así que un cambio de reparto
   rompía el pronóstico por construcción.

   Y el reparto cambió. El 2026-09-01, de un día para el otro:

       26/08 al 31/08   Acme 31,4% – 33,4% de las llamadas ruteadas
       01/09 al 06/09   Acme 48,5% – 50,4%

   Un escalón limpio, sin transición. Con la serie vieja el pronóstico habría
   seguido proyectando el volumen de agosto durante semanas.

   EL SHARE NO ES UNO SOLO: ES DISTINTO POR SKILL
   -----------------------------------------------
   Medido sobre el régimen nuevo (desde el 2026-09-01):

       TOC                       65,1%
       ELECTRODEPENDIENTES       54,0%
       RECLAMO-DANO              50,4%
       COMERCIAL                 49,9%
       GRANDES-CUENTAS           49,5%
       EMERGENCIAS               49,2%
       CNR-EMPRESARIAL          100,0%   (4 llamadas: sin peso estadístico)
       CNR                        0,0%   (las 252 fueron todas a BPO-05)
       EMERGENCIAS-EMPRESARIAL    0,0%   (las 40 fueron todas a BPO-05)

   Por eso el tramo va POR SKILL y no como un número único de campaña: aplicar un
   70% parejo cuando llegue el aumento repartiría mal el volumen entre colas con
   TMO muy distinto (Emergencias 180s, Comercial 335s) y el dimensionamiento
   saldría sesgado.

   POR QUÉ SE PERMITE EL CERO
   --------------------------
   CNR y EMERGENCIAS-EMPRESARIAL hoy no nos llegan: son colas reales del cliente
   que se rutean íntegras al otro BPO. El CHECK original exigía Porcentaje > 0, o
   sea que no había forma de representarlas y quedaban sin tramo — y sin tramo el
   pronóstico no sabe si es que no nos mandan nada o si falta configurar. Cero es
   un valor legítimo y explícito.

   QUÉ CUENTA COMO DEMANDA
   -----------------------
   Sólo las llamadas que llegaron a una COLA (`Cola IS NOT NULL`). El resto de la
   tabla no es demanda de los BPOs: las de `Resultado IVR = 'AGENT'` sin BPO ni
   cola se rutearon pero nunca se conectaron (Tiempo Total == Tiempo IVR y
   Talk = 0 en 185.667 de 185.688 casos de agosto), y DISCONNECT/SURVEY se
   resolvieron o se cortaron dentro del IVR.

   CÓMO CORRER
   -----------
   Después de las dos migraciones anteriores. Idempotente: si ya hay tramos
   cargados no toca nada, para no pisar lo que alguien haya ajustado a mano.

   CUANDO LLEGUE EL 70%
   --------------------
   No hace falta tocar código ni base a mano: se carga el tramo nuevo desde la
   pantalla (Configuración → Asignación), con su fecha de vigencia. El bloque
   comentado del final queda como referencia de cómo sería por SQL.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* --------------------------------------------- permitir el cero explícito */
IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Plan_Asignacion_Pct')
BEGIN
    ALTER TABLE planificacion.Asignacion DROP CONSTRAINT CK_Plan_Asignacion_Pct;
    PRINT 'CK_Plan_Asignacion_Pct viejo eliminado.';
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Plan_Asignacion_Pct')
BEGIN
    ALTER TABLE planificacion.Asignacion
        ADD CONSTRAINT CK_Plan_Asignacion_Pct
            CHECK (Porcentaje >= 0 AND Porcentaje <= 1);
    PRINT 'CK_Plan_Asignacion_Pct nuevo (permite 0) creado.';
END
GO

/* ====================================================================
   SIEMBRA — medida, no escrita a mano
   ==================================================================== */

IF NOT EXISTS (SELECT 1 FROM planificacion.Asignacion WHERE CampanaID = 20)
BEGIN
    /* Régimen anterior: del 2025-05-01 (cuando el reparto se estabilizó tras la
       caída de abril) al 2026-08-31 inclusive. Sirve para que un backtest sobre
       fechas pasadas use el share que de verdad regía entonces. */
    INSERT INTO planificacion.Asignacion
        (CampanaID, SkillID, VigenteDesde, VigenteHasta, Porcentaje, Nota)
    SELECT 20,
           n.[Skill ID],
           '2025-05-01',
           '2026-08-31',
           CAST(1.0 * SUM(CASE WHEN i.BPO = 'BPO-04' THEN 1 ELSE 0 END)
                    / COUNT(*) AS DECIMAL(5,4)),
           CONCAT('Medido sobre ', FORMAT(COUNT(*), 'N0'), ' llamadas ruteadas.')
    FROM [dbo].[Voltara Enerval informe IVR] i
    JOIN [dbo].[Voltara normalizador por Skill] n
      ON UPPER(n.Skill) = UPPER(i.Skill)
    WHERE i.Cola IS NOT NULL
      AND i.[Fecha de Inicio] >= '2025-05-01'
      AND i.[Fecha de Inicio] <  '2026-09-01'
    GROUP BY n.[Skill ID]
    HAVING COUNT(*) >= 100;   -- menos que esto no es un share, es ruido

    /* Régimen vigente: desde el escalón del 2026-09-01, sin fecha de fin. */
    INSERT INTO planificacion.Asignacion
        (CampanaID, SkillID, VigenteDesde, VigenteHasta, Porcentaje, Nota)
    SELECT 20,
           n.[Skill ID],
           '2026-09-01',
           NULL,
           CAST(1.0 * SUM(CASE WHEN i.BPO = 'BPO-04' THEN 1 ELSE 0 END)
                    / COUNT(*) AS DECIMAL(5,4)),
           CONCAT('Escalón del 2026-09-01. Medido sobre ',
                  FORMAT(COUNT(*), 'N0'), ' llamadas ruteadas.')
    FROM [dbo].[Voltara Enerval informe IVR] i
    JOIN [dbo].[Voltara normalizador por Skill] n
      ON UPPER(n.Skill) = UPPER(i.Skill)
    WHERE i.Cola IS NOT NULL
      AND i.[Fecha de Inicio] >= '2026-09-01'
    GROUP BY n.[Skill ID]
    HAVING COUNT(*) >= 100;

    PRINT 'Tramos de asignación de Voltara sembrados (medidos de la descarga completa).';
END
ELSE
    PRINT 'planificacion.Asignacion ya tenía tramos cargados: no se toca.';
GO

/* Los skills con menos de 100 llamadas en el período quedan sin tramo a
   propósito: con 4 llamadas el share da 100% y es una cifra sin sentido que
   después el pronóstico tomaría en serio. Sin tramo, el planificador avisa en
   vez de inventar. */

/* ---------------------------------------------------------------------------
   CUANDO VOLTARA CONFIRME EL PASO AL 70%: cargarlo desde la pantalla
   (Configuración → Asignación). Por SQL sería así — ajustar fecha y porcentajes,
   y CERRAR el tramo vigente el día anterior:

UPDATE planificacion.Asignacion
SET VigenteHasta = '2026-10-31'
WHERE CampanaID = 20 AND VigenteHasta IS NULL;

INSERT INTO planificacion.Asignacion
    (CampanaID, SkillID, VigenteDesde, VigenteHasta, Porcentaje, Nota)
VALUES
    (20, 6, '2026-11-01', NULL, 0.70, 'Paso al 70% - EMERGENCIAS'),
    (20, 4, '2026-11-01', NULL, 0.70, 'Paso al 70% - COMERCIAL');
    -- ... el resto de los skills
--------------------------------------------------------------------------- */

/* ====================================================================
   VERIFICACIÓN (no modifica nada)
   ==================================================================== */
SELECT a.VigenteDesde, a.VigenteHasta, s.Nombre AS Skill,
       CAST(a.Porcentaje * 100 AS DECIMAL(5,2)) AS PorcentajeAcme, a.Nota
FROM planificacion.Asignacion a
LEFT JOIN planificacion.Skill s
       ON s.CampanaID = a.CampanaID AND s.SkillID = a.SkillID
WHERE a.CampanaID = 20
ORDER BY a.VigenteDesde, s.Nombre;
GO
