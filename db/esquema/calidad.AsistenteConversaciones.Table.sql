-- Table [calidad].[AsistenteConversaciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[AsistenteConversaciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[AsistenteConversaciones](
	[Id] [int] IDENTITY(1,1) NOT NULL,
	[UsuarioId] [int] NOT NULL,
	[Titulo] [nvarchar](200) NOT NULL,
	[Alcance] [nvarchar](max) NULL,
	[PlantillaID] [int] NULL,
	[CampanaId] [int] NULL,
	[Activo] [bit] NOT NULL,
	[CreadoEn] [datetime2](7) NOT NULL,
	[ActualizadoEn] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_AsistenteConversaciones] PRIMARY KEY CLUSTERED 
(
	[Id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_AsistenteConv_Usuario]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[AsistenteConversaciones]') AND name = N'IX_AsistenteConv_Usuario')
CREATE NONCLUSTERED INDEX [IX_AsistenteConv_Usuario] ON [calidad].[AsistenteConversaciones]
(
	[UsuarioId] ASC,
	[Activo] ASC,
	[ActualizadoEn] DESC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AsistenteConv_Activo]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AsistenteConversaciones] ADD  CONSTRAINT [DF_AsistenteConv_Activo]  DEFAULT ((1)) FOR [Activo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AsistenteConv_Creado]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AsistenteConversaciones] ADD  CONSTRAINT [DF_AsistenteConv_Creado]  DEFAULT (sysutcdatetime()) FOR [CreadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_AsistenteConv_Actualizado]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[AsistenteConversaciones] ADD  CONSTRAINT [DF_AsistenteConv_Actualizado]  DEFAULT (sysutcdatetime()) FOR [ActualizadoEn]
END
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_AsistenteConv_Json]') AND parent_object_id = OBJECT_ID(N'[calidad].[AsistenteConversaciones]'))
ALTER TABLE [calidad].[AsistenteConversaciones]  WITH CHECK ADD  CONSTRAINT [CK_AsistenteConv_Json] CHECK  (([Alcance] IS NULL OR isjson([Alcance])=(1)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_AsistenteConv_Json]') AND parent_object_id = OBJECT_ID(N'[calidad].[AsistenteConversaciones]'))
ALTER TABLE [calidad].[AsistenteConversaciones] CHECK CONSTRAINT [CK_AsistenteConv_Json]
GO
