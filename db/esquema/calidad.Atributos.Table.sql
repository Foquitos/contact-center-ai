-- Table [calidad].[Atributos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[Atributos]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[Atributos](
	[AtributoID] [int] IDENTITY(1,1) NOT NULL,
	[PlantillaID] [int] NOT NULL,
	[NombreAtributo] [nvarchar](255) NOT NULL,
	[PromptAdyacente] [nvarchar](max) NOT NULL,
	[TipoDato] [nvarchar](50) NOT NULL,
	[Restricciones] [nvarchar](max) NULL,
	[Orden] [int] NULL,
	[IsActive] [bit] NOT NULL,
	[DarAviso] [bit] NOT NULL,
	[FrasesAviso] [varchar](255) NULL,
	[Ponderacion] [decimal](6, 2) NOT NULL,
	[EsOpcional] [bit] NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[AtributoID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_Atributos_IsActive]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Atributos]') AND name = N'IX_Atributos_IsActive')
CREATE NONCLUSTERED INDEX [IX_Atributos_IsActive] ON [calidad].[Atributos]
(
	[IsActive] ASC
)
WHERE ([IsActive]=(1))
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [UQ_Atributos_PlantillaID_Orden_Activos]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Atributos]') AND name = N'UQ_Atributos_PlantillaID_Orden_Activos')
CREATE UNIQUE NONCLUSTERED INDEX [UQ_Atributos_PlantillaID_Orden_Activos] ON [calidad].[Atributos]
(
	[PlantillaID] ASC,
	[Orden] ASC
)
WHERE ([IsActive]=(1))
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, IGNORE_DUP_KEY = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__Atributos__Orden__4BA21D88]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Atributos] ADD  DEFAULT ((0)) FOR [Orden]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__Atributos__IsAct__16F94B1F]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Atributos] ADD  DEFAULT ((1)) FOR [IsActive]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_Atributos_dar_aviso]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Atributos] ADD  CONSTRAINT [DF_Atributos_dar_aviso]  DEFAULT ((0)) FOR [DarAviso]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_Atributos_Ponderacion]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Atributos] ADD  CONSTRAINT [DF_Atributos_Ponderacion]  DEFAULT ((0)) FOR [Ponderacion]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_Atributos_EsOpcional]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Atributos] ADD  CONSTRAINT [DF_Atributos_EsOpcional]  DEFAULT ((0)) FOR [EsOpcional]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Atributos_Plantillas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Atributos]'))
ALTER TABLE [calidad].[Atributos]  WITH CHECK ADD  CONSTRAINT [FK_Atributos_Plantillas] FOREIGN KEY([PlantillaID])
REFERENCES [calidad].[Plantillas] ([PlantillaID])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Atributos_Plantillas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Atributos]'))
ALTER TABLE [calidad].[Atributos] CHECK CONSTRAINT [FK_Atributos_Plantillas]
GO
