-- Table [orion].[Silver_Encuestas_Detalle]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[Silver_Encuestas_Detalle]') AND type in (N'U'))
BEGIN
CREATE TABLE [orion].[Silver_Encuestas_Detalle](
	[ID] [int] IDENTITY(1,1) NOT NULL,
	[ID_Llamada] [bigint] NOT NULL,
	[LogTareasID] [int] NOT NULL,
	[Fecha_Hora] [datetime] NOT NULL,
	[Campaña_Encuesta] [varchar](100) NULL,
	[Nro_Telefonico] [varchar](100) NULL,
	[Duracion_IVR_Segundos] [int] NULL,
	[Encuesta_Respondida] [varchar](10) NULL,
	[Legajo_Orion] [varchar](50) NULL,
	[Nodo_ID] [int] NULL,
	[Pregunta] [varchar](255) NULL,
	[Respuesta] [varchar](500) NULL,
 CONSTRAINT [PK_Silver_Encuestas_Detalle] PRIMARY KEY NONCLUSTERED 
(
	[ID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [CIX_Silver_Encuestas_Fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[orion].[Silver_Encuestas_Detalle]') AND name = N'CIX_Silver_Encuestas_Fecha')
CREATE CLUSTERED INDEX [CIX_Silver_Encuestas_Fecha] ON [orion].[Silver_Encuestas_Detalle]
(
	[Fecha_Hora] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [NCIX_Silver_Encuestas_Llamada]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[orion].[Silver_Encuestas_Detalle]') AND name = N'NCIX_Silver_Encuestas_Llamada')
CREATE NONCLUSTERED INDEX [NCIX_Silver_Encuestas_Llamada] ON [orion].[Silver_Encuestas_Detalle]
(
	[ID_Llamada] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
