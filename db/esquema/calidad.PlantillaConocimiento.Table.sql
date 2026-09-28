-- Table [calidad].[PlantillaConocimiento]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[PlantillaConocimiento]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[PlantillaConocimiento](
	[PlantillaID] [int] NOT NULL,
	[DocID] [int] NOT NULL,
	[CreadoPorUsuarioID] [int] NULL,
	[FechaAlta] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_PlantillaConocimiento] PRIMARY KEY CLUSTERED 
(
	[PlantillaID] ASC,
	[DocID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_PlantillaConocimiento_FechaAlta]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[PlantillaConocimiento] ADD  CONSTRAINT [DF_PlantillaConocimiento_FechaAlta]  DEFAULT (sysutcdatetime()) FOR [FechaAlta]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_PlantillaConocimiento_Doc]') AND parent_object_id = OBJECT_ID(N'[calidad].[PlantillaConocimiento]'))
ALTER TABLE [calidad].[PlantillaConocimiento]  WITH CHECK ADD  CONSTRAINT [FK_PlantillaConocimiento_Doc] FOREIGN KEY([DocID])
REFERENCES [pagina_web].[ChatbotDocMarkdown] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_PlantillaConocimiento_Doc]') AND parent_object_id = OBJECT_ID(N'[calidad].[PlantillaConocimiento]'))
ALTER TABLE [calidad].[PlantillaConocimiento] CHECK CONSTRAINT [FK_PlantillaConocimiento_Doc]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_PlantillaConocimiento_Plantilla]') AND parent_object_id = OBJECT_ID(N'[calidad].[PlantillaConocimiento]'))
ALTER TABLE [calidad].[PlantillaConocimiento]  WITH CHECK ADD  CONSTRAINT [FK_PlantillaConocimiento_Plantilla] FOREIGN KEY([PlantillaID])
REFERENCES [calidad].[Plantillas] ([PlantillaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_PlantillaConocimiento_Plantilla]') AND parent_object_id = OBJECT_ID(N'[calidad].[PlantillaConocimiento]'))
ALTER TABLE [calidad].[PlantillaConocimiento] CHECK CONSTRAINT [FK_PlantillaConocimiento_Plantilla]
GO
