-- Table [dbo].[TLMK Metricas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[TLMK Metricas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[TLMK Metricas](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Agente] [varchar](max) NULL,
	[Identif. de conexion] [int] NULL,
	[Split/Skill] [varchar](max) NULL,
	[Skill] [smallint] NULL,
	[Tiempo con personal] [int] NULL,
	[Llamadas ACD] [smallint] NULL,
	[Tiempo ACD] [smallint] NULL,
	[Tiempo ACW] [smallint] NULL,
	[Tiempo dispon.] [smallint] NULL,
	[Otra Hora] [smallint] NULL,
	[Llamadas de entrada a la extn] [smallint] NULL,
	[Tiempo de entrada a la extn] [smallint] NULL,
	[Llamadas de salida de la extn] [smallint] NULL,
	[Tiempo de salida de la extn] [smallint] NULL,
	[Llamadas retenidas] [smallint] NULL,
	[Tiempo de reten.] [smallint] NULL,
	[Trans. de salida] [smallint] NULL,
	[Conexion] [smallint] NULL,
	[Refrigerio] [smallint] NULL,
	[Toilette] [smallint] NULL,
	[BackOffice] [int] NULL,
	[Devo Grupal] [smallint] NULL,
	[Capacitacion] [smallint] NULL,
	[Medico] [smallint] NULL,
	[RRHH] [smallint] NULL,
	[Devo Individual] [smallint] NULL,
	[Errores Criticos] [smallint] NULL,
 CONSTRAINT [PK_TLMK Metricas] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
