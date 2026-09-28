-- Table [calidad].[Plantillas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[Plantillas]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[Plantillas](
	[PlantillaID] [int] IDENTITY(1,1) NOT NULL,
	[Nombre] [nvarchar](255) NOT NULL,
	[SystemPrompt] [nvarchar](max) NULL,
	[FechaCreacion] [datetime2](7) NULL,
	[Recordatorio] [nvarchar](max) NULL,
	[descripcion] [nvarchar](max) NULL,
	[CampanaID] [int] NOT NULL,
	[IsActive] [bit] NOT NULL,
	[ModeloIA] [nvarchar](80) NULL,
	[NivelRazonamiento] [nvarchar](10) NULL,
PRIMARY KEY CLUSTERED 
(
	[PlantillaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_Plantillas_IsActive]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Plantillas]') AND name = N'IX_Plantillas_IsActive')
CREATE NONCLUSTERED INDEX [IX_Plantillas_IsActive] ON [calidad].[Plantillas]
(
	[IsActive] ASC
)
WHERE ([IsActive]=(1))
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__Plantilla__Fecha__48C5B0DD]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Plantillas] ADD  DEFAULT (getdate()) FOR [FechaCreacion]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__Plantilla__IsAct__160526E6]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Plantillas] ADD  DEFAULT ((1)) FOR [IsActive]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Plantillas_Campanas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Plantillas]'))
ALTER TABLE [calidad].[Plantillas]  WITH CHECK ADD  CONSTRAINT [FK_Plantillas_Campanas] FOREIGN KEY([CampanaID])
REFERENCES [calidad].[Campanas] ([CampanaID])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Plantillas_Campanas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Plantillas]'))
ALTER TABLE [calidad].[Plantillas] CHECK CONSTRAINT [FK_Plantillas_Campanas]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_Plantillas_NivelRazonamiento]') AND parent_object_id = OBJECT_ID(N'[calidad].[Plantillas]'))
ALTER TABLE [calidad].[Plantillas]  WITH CHECK ADD  CONSTRAINT [CK_Plantillas_NivelRazonamiento] CHECK  (([NivelRazonamiento] IS NULL OR ([NivelRazonamiento]='HIGH' OR [NivelRazonamiento]='MEDIUM' OR [NivelRazonamiento]='LOW')))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_Plantillas_NivelRazonamiento]') AND parent_object_id = OBJECT_ID(N'[calidad].[Plantillas]'))
ALTER TABLE [calidad].[Plantillas] CHECK CONSTRAINT [CK_Plantillas_NivelRazonamiento]
GO
