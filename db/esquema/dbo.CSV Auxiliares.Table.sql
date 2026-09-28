-- Table [dbo].[CSV Auxiliares]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV Auxiliares]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV Auxiliares](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Nombre del agente] [varchar](max) NULL,
	[Identif. de conexión] [int] NULL,
	[Tiempo con personal] [int] NULL,
	[Tiempo AUX] [smallint] NULL,
	[Tiempo en Conexión] [smallint] NULL,
	[Tiempo en Refrigerio] [smallint] NULL,
	[Tiempo en Toilette] [smallint] NULL,
	[Tiempo en BackOffice] [smallint] NULL,
	[Tiempo en Devo Grupal] [smallint] NULL,
	[Tiempo en Capacitación] [smallint] NULL,
	[Tiempo en Médico] [smallint] NULL,
	[Tiempo en RRHH] [smallint] NULL,
	[Tiempo en Devo Individual] [smallint] NULL,
	[Tiempo en Errores Críticos] [smallint] NULL,
	[horario_id] [tinyint] NULL,
 CONSTRAINT [PK_CSV Auxiliares] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
