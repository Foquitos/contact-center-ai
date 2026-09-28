-- Table [dbo].[Voltara informe IVR]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara informe IVR]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara informe IVR](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[ConnID] [varchar](max) NULL,
	[ANI] [varchar](max) NULL,
	[Fecha de Inicio] [datetime] NULL,
	[IVR] [varchar](max) NULL,
	[Tiempo IVR] [smallint] NULL,
	[Resultado IVR] [varchar](max) NULL,
	[Tiempo (IVR-Total)] [smallint] NULL,
	[Línea telefónica] [int] NULL,
	[Tiempo Total] [smallint] NULL,
	[Numero de caso] [int] NULL,
	[Documento] [varchar](max) NULL,
	[Tipo de Documento] [varchar](max) NULL,
	[Suministro] [float] NULL,
	[Cola] [varchar](max) NULL,
	[Duración Cola] [smallint] NULL,
	[Skill] [varchar](max) NULL,
	[BPO] [varchar](max) NULL,
	[Nombre de Agente] [varchar](max) NULL,
	[Agente] [varchar](max) NULL,
	[Duración Ring] [smallint] NULL,
	[Duración Talk] [smallint] NULL,
	[Duración Hold] [smallint] NULL,
	[Duración ACW] [smallint] NULL,
	[Duración POS] [smallint] NULL,
	[Servicio <= 20 segundos] [varchar](max) NULL,
	[Duración Encuesta] [smallint] NULL,
	[Puntos de control IVR] [varchar](max) NULL,
	[Trazabilidad de Llamadas] [varchar](max) NULL,
	[Status Servicio] [varchar](max) NULL,
	[Fecha de Agente] [datetime] NULL,
 CONSTRAINT [PK_Voltara informe IVR] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [NonClusteredIndex-20250526-150051]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Voltara informe IVR]') AND name = N'NonClusteredIndex-20250526-150051')
CREATE NONCLUSTERED INDEX [NonClusteredIndex-20250526-150051] ON [dbo].[Voltara informe IVR]
(
	[Fecha de Inicio] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
