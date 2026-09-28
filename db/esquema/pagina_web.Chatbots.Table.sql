-- Table [pagina_web].[Chatbots]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[Chatbots]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[Chatbots](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[slug] [nvarchar](50) NOT NULL,
	[nombre] [nvarchar](100) NOT NULL,
	[descripcion] [nvarchar](400) NULL,
	[system_prompt] [nvarchar](max) NOT NULL,
	[grupo] [nvarchar](30) NULL,
	[permission_id] [int] NOT NULL,
	[activo] [bit] NOT NULL,
	[index_version] [int] NOT NULL,
	[index_status] [nvarchar](20) NOT NULL,
	[last_indexed_at] [datetime2](7) NULL,
	[created_at] [datetime2](7) NOT NULL,
	[updated_at] [datetime2](7) NOT NULL,
	[temperatura] [float] NULL,
	[permite_adjuntos] [bit] NOT NULL,
 CONSTRAINT [PK_Chatbots] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_Chatbots_slug] UNIQUE NONCLUSTERED 
(
	[slug] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Chatbots_activo]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Chatbots] ADD  CONSTRAINT [DF_Chatbots_activo]  DEFAULT ((1)) FOR [activo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Chatbots_iv]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Chatbots] ADD  CONSTRAINT [DF_Chatbots_iv]  DEFAULT ((0)) FOR [index_version]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Chatbots_is]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Chatbots] ADD  CONSTRAINT [DF_Chatbots_is]  DEFAULT ('never_indexed') FOR [index_status]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Chatbots_ca]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Chatbots] ADD  CONSTRAINT [DF_Chatbots_ca]  DEFAULT (sysdatetime()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Chatbots_ua]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Chatbots] ADD  CONSTRAINT [DF_Chatbots_ua]  DEFAULT (sysdatetime()) FOR [updated_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Chatbots_permite_adjuntos]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Chatbots] ADD  CONSTRAINT [DF_Chatbots_permite_adjuntos]  DEFAULT ((0)) FOR [permite_adjuntos]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_Chatbots_Permission]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[Chatbots]'))
ALTER TABLE [pagina_web].[Chatbots]  WITH CHECK ADD  CONSTRAINT [FK_Chatbots_Permission] FOREIGN KEY([permission_id])
REFERENCES [pagina_web].[Permissions] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_Chatbots_Permission]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[Chatbots]'))
ALTER TABLE [pagina_web].[Chatbots] CHECK CONSTRAINT [FK_Chatbots_Permission]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_Chatbots_temperatura]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[Chatbots]'))
ALTER TABLE [pagina_web].[Chatbots]  WITH CHECK ADD  CONSTRAINT [CK_Chatbots_temperatura] CHECK  (([temperatura] IS NULL OR [temperatura]>=(0) AND [temperatura]<=(1)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_Chatbots_temperatura]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[Chatbots]'))
ALTER TABLE [pagina_web].[Chatbots] CHECK CONSTRAINT [CK_Chatbots_temperatura]
GO
