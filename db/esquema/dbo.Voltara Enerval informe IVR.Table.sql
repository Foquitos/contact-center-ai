-- Table [dbo].[Voltara Enerval informe IVR]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval informe IVR]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara Enerval informe IVR](
	[ConnID] [varchar](32) NOT NULL,
	[ANI] [varchar](32) NULL,
	[Fecha de Inicio] [datetime2](0) NOT NULL,
	[IVR] [varchar](64) NULL,
	[Tiempo IVR] [int] NULL,
	[Resultado IVR] [varchar](64) NULL,
	[Tiempo (IVR-Total)] [int] NULL,
	[Línea telefónica] [varchar](32) NULL,
	[Tiempo Total] [int] NULL,
	[Numero de caso] [varchar](32) NULL,
	[Documento] [varchar](32) NULL,
	[Tipo de Documento] [varchar](32) NULL,
	[Suministro] [varchar](32) NULL,
	[Cola] [varchar](64) NULL,
	[Duración Cola] [int] NULL,
	[Skill] [varchar](64) NULL,
	[BPO] [varchar](32) NULL,
	[Nombre de Agente] [varchar](128) NULL,
	[Agente] [varchar](32) NULL,
	[Duración Ring] [int] NULL,
	[Duración Talk] [int] NULL,
	[Duración Hold] [int] NULL,
	[Duración ACW] [int] NULL,
	[Duración POS] [int] NULL,
	[Servicio <= 20 segundos] [varchar](8) NULL,
	[Duración Encuesta] [int] NULL,
	[Puntos de control IVR] [varchar](4000) NULL,
	[Trazabilidad de Llamadas] [varchar](64) NULL,
	[Status Servicio] [varchar](128) NULL,
	[Fecha de Agente] [datetime2](0) NULL,
	[FechaCarga] [datetime2](0) NOT NULL
) ON [PRIMARY]
WITH
(
DATA_COMPRESSION = PAGE
)
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_VoltaraEnervalInformeIVR_Fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval informe IVR]') AND name = N'IX_VoltaraEnervalInformeIVR_Fecha')
CREATE CLUSTERED INDEX [IX_VoltaraEnervalInformeIVR_Fecha] ON [dbo].[Voltara Enerval informe IVR]
(
	[Fecha de Inicio] ASC,
	[ConnID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON, DATA_COMPRESSION = PAGE) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_VoltaraEnervalInformeIVR_ConnID]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval informe IVR]') AND name = N'IX_VoltaraEnervalInformeIVR_ConnID')
CREATE NONCLUSTERED INDEX [IX_VoltaraEnervalInformeIVR_ConnID] ON [dbo].[Voltara Enerval informe IVR]
(
	[ConnID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON, DATA_COMPRESSION = PAGE) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF_VoltaraEnervalInformeIVR_FechaCarga]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[Voltara Enerval informe IVR] ADD  CONSTRAINT [DF_VoltaraEnervalInformeIVR_FechaCarga]  DEFAULT (sysdatetime()) FOR [FechaCarga]
END
GO
