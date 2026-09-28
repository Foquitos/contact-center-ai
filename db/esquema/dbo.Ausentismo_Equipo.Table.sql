-- Table [dbo].[Ausentismo_Equipo]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Ausentismo_Equipo]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Ausentismo_Equipo](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Equipo_id] [int] NULL,
	[PROGRAMADOS] [smallint] NULL,
	[AUSENTES] [smallint] NULL,
	[HORAS PROGRAMADAS] [float] NULL,
	[CUMPLIMIENTO] [float] NULL,
	[CUMPLIMIENTO NETO] [float] NULL,
	[DESVÍO] [float] NULL,
	[DESVÍO (HS)] [float] NULL,
	[ADHERENCIA] [float] NULL,
	[AUSENTISMO] [float] NULL,
	[AUS. PLANIFICADO] [float] NULL,
	[AUS. NO PLANIFICADO] [float] NULL,
	[FECHA] [date] NULL,
 CONSTRAINT [PK_Ausentismo_Equipo] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
