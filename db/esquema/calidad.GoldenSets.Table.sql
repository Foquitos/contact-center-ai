-- Table [calidad].[GoldenSets]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[GoldenSets]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[GoldenSets](
	[GoldenSetID] [int] IDENTITY(1,1) NOT NULL,
	[Nombre] [nvarchar](255) NOT NULL,
	[PlantillaID] [int] NOT NULL,
	[Descripcion] [nvarchar](1000) NULL,
	[FechaRevisionRecomendada] [date] NULL,
	[CreadoPorUsuarioID] [int] NULL,
	[FechaCreacion] [datetime2](7) NOT NULL,
	[IsActive] [bit] NOT NULL,
 CONSTRAINT [PK_GoldenSets] PRIMARY KEY CLUSTERED 
(
	[GoldenSetID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_GoldenSets_Plantilla]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[GoldenSets]') AND name = N'IX_GoldenSets_Plantilla')
CREATE NONCLUSTERED INDEX [IX_GoldenSets_Plantilla] ON [calidad].[GoldenSets]
(
	[PlantillaID] ASC,
	[IsActive] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_GoldenSets_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[GoldenSets] ADD  CONSTRAINT [DF_GoldenSets_Fecha]  DEFAULT (sysutcdatetime()) FOR [FechaCreacion]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_GoldenSets_Activo]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[GoldenSets] ADD  CONSTRAINT [DF_GoldenSets_Activo]  DEFAULT ((1)) FOR [IsActive]
END
GO
