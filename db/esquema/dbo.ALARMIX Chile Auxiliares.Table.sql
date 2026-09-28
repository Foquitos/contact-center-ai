-- Table [dbo].[ALARMIX Chile Auxiliares]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[ALARMIX Chile Auxiliares]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[ALARMIX Chile Auxiliares](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Nombre del agente] [varchar](max) NULL,
	[Identif. de conexión] [smallint] NULL,
	[Tiempo con personal] [int] NULL,
	[Tiempo AUX] [smallint] NULL,
	[Tiempo en Defecto] [smallint] NULL,
	[Tiempo en Trabajo Administrat.] [smallint] NULL,
	[Tiempo en Reunión] [smallint] NULL,
	[Tiempo en Capacitacion] [smallint] NULL,
	[Tiempo en Descanso] [smallint] NULL,
	[Tiempo en Baño] [smallint] NULL,
	[Tiempo en Almuerzo] [smallint] NULL,
	[Tiempo en Campaña 1] [smallint] NULL,
	[Tiempo en ATE WEB] [smallint] NULL,
	[Tiempo en CONEXIÓN REMOTA DL] [smallint] NULL,
	[Tiempo en AUX 10-99] [smallint] NULL,
	[Fecha] [date] NULL,
 CONSTRAINT [PK_ALARMIX Chile Auxiliares] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
