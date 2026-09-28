-- Table [calidad].[BandejaDashboard]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[BandejaDashboard]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[BandejaDashboard](
	[Id] [int] IDENTITY(1,1) NOT NULL,
	[PlantillaID] [int] NOT NULL,
	[Nombre] [nvarchar](120) NOT NULL,
	[EsDefault] [bit] NOT NULL,
	[Orden] [int] NOT NULL,
	[Config] [nvarchar](max) NOT NULL,
	[ActualizadoPor] [int] NULL,
	[ActualizadoEn] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_BandejaDashboard] PRIMARY KEY CLUSTERED 
(
	[Id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_BandejaDashboard_Plantilla_Nombre] UNIQUE NONCLUSTERED 
(
	[PlantillaID] ASC,
	[Nombre] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_BandejaDashboard_Plantilla]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[BandejaDashboard]') AND name = N'IX_BandejaDashboard_Plantilla')
CREATE NONCLUSTERED INDEX [IX_BandejaDashboard_Plantilla] ON [calidad].[BandejaDashboard]
(
	[PlantillaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_BandejaDashboard_Def]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[BandejaDashboard] ADD  CONSTRAINT [DF_BandejaDashboard_Def]  DEFAULT ((0)) FOR [EsDefault]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_BandejaDashboard_Ord]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[BandejaDashboard] ADD  CONSTRAINT [DF_BandejaDashboard_Ord]  DEFAULT ((0)) FOR [Orden]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_BandejaDashboard_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[BandejaDashboard] ADD  CONSTRAINT [DF_BandejaDashboard_Fecha]  DEFAULT (sysutcdatetime()) FOR [ActualizadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_BandejaDashboard_Json]') AND parent_object_id = OBJECT_ID(N'[calidad].[BandejaDashboard]'))
ALTER TABLE [calidad].[BandejaDashboard]  WITH CHECK ADD  CONSTRAINT [CK_BandejaDashboard_Json] CHECK  ((isjson([Config])=(1)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_BandejaDashboard_Json]') AND parent_object_id = OBJECT_ID(N'[calidad].[BandejaDashboard]'))
ALTER TABLE [calidad].[BandejaDashboard] CHECK CONSTRAINT [CK_BandejaDashboard_Json]
GO
