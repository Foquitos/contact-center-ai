-- Table [dbo].[TLMK Auxiliares]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[TLMK Auxiliares]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[TLMK Auxiliares](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Nombre del agente] [varchar](max) NULL,
	[Identif. de conexion] [int] NULL,
	[Tiempo con personal] [int] NULL,
	[Tiempo AUX] [int] NULL,
	[Tiempo en Conexion] [smallint] NULL,
	[Tiempo en Refrigerio] [smallint] NULL,
	[Tiempo en Toilette] [smallint] NULL,
	[Tiempo en BackOffice] [int] NULL,
	[Tiempo en Devo Grupal] [smallint] NULL,
	[Tiempo en Capacitacion] [smallint] NULL,
	[Tiempo en Medico] [smallint] NULL,
	[Tiempo en RRHH] [smallint] NULL,
	[Tiempo en Devo Individual] [smallint] NULL,
	[Tiempo en Errores Criticos] [smallint] NULL,
 CONSTRAINT [PK_TLMK Grupo en AUX diario multifecha] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
