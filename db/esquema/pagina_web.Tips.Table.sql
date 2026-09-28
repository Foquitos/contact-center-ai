-- Table [pagina_web].[Tips]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[Tips]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[Tips](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[group_id] [int] NOT NULL,
	[title] [varchar](150) NULL,
	[content] [nvarchar](max) NOT NULL,
	[activo] [bit] NOT NULL,
	[created_at] [datetime] NOT NULL,
	[updated_at] [datetime] NOT NULL,
	[tipo] [varchar](20) NOT NULL,
	[fecha_desde] [date] NULL,
	[fecha_hasta] [date] NULL,
	[es_prioritario] [bit] NOT NULL,
	[url_accion] [varchar](500) NULL,
	[texto_accion] [varchar](100) NULL,
 CONSTRAINT [PK_Tips] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_Tips_group_id_activo]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[Tips]') AND name = N'IX_Tips_group_id_activo')
CREATE NONCLUSTERED INDEX [IX_Tips_group_id_activo] ON [pagina_web].[Tips]
(
	[group_id] ASC,
	[activo] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Tips_activo]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Tips] ADD  CONSTRAINT [DF_Tips_activo]  DEFAULT ((1)) FOR [activo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Tips_created]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Tips] ADD  CONSTRAINT [DF_Tips_created]  DEFAULT (getdate()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Tips_updated]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Tips] ADD  CONSTRAINT [DF_Tips_updated]  DEFAULT (getdate()) FOR [updated_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Tips_tipo]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Tips] ADD  CONSTRAINT [DF_Tips_tipo]  DEFAULT ('info') FOR [tipo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Tips_es_prioritario]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Tips] ADD  CONSTRAINT [DF_Tips_es_prioritario]  DEFAULT ((0)) FOR [es_prioritario]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_Tips_TipGroups]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[Tips]'))
ALTER TABLE [pagina_web].[Tips]  WITH CHECK ADD  CONSTRAINT [FK_Tips_TipGroups] FOREIGN KEY([group_id])
REFERENCES [pagina_web].[TipGroups] ([id])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_Tips_TipGroups]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[Tips]'))
ALTER TABLE [pagina_web].[Tips] CHECK CONSTRAINT [FK_Tips_TipGroups]
GO
