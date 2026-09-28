-- Table [pagina_web].[ChatbotVacios]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotVacios]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[ChatbotVacios](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[chatbot_id] [int] NOT NULL,
	[tema] [nvarchar](300) NOT NULL,
	[pregunta_ejemplo] [nvarchar](1000) NULL,
	[clasificacion] [varchar](20) NOT NULL,
	[estado] [varchar](20) NOT NULL,
	[ocurrencias] [int] NOT NULL,
	[usuarios] [int] NOT NULL,
	[primera_vez] [datetime2](7) NOT NULL,
	[ultima_vez] [datetime2](7) NOT NULL,
	[doc_sugerido] [nvarchar](300) NULL,
	[score_sugerido] [float] NULL,
	[notas] [nvarchar](2000) NULL,
	[resuelto_por] [int] NULL,
	[resuelto_at] [datetime2](7) NULL,
	[created_at] [datetime2](7) NOT NULL,
	[updated_at] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_ChatbotVacios] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_ChatbotVacios_bot_estado]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotVacios]') AND name = N'IX_ChatbotVacios_bot_estado')
CREATE NONCLUSTERED INDEX [IX_ChatbotVacios_bot_estado] ON [pagina_web].[ChatbotVacios]
(
	[chatbot_id] ASC,
	[estado] ASC,
	[ocurrencias] DESC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotVacios_clas]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotVacios] ADD  CONSTRAINT [DF_ChatbotVacios_clas]  DEFAULT ('hueco') FOR [clasificacion]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotVacios_estado]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotVacios] ADD  CONSTRAINT [DF_ChatbotVacios_estado]  DEFAULT ('pendiente') FOR [estado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotVacios_ocur]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotVacios] ADD  CONSTRAINT [DF_ChatbotVacios_ocur]  DEFAULT ((1)) FOR [ocurrencias]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotVacios_usr]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotVacios] ADD  CONSTRAINT [DF_ChatbotVacios_usr]  DEFAULT ((1)) FOR [usuarios]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotVacios_pv]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotVacios] ADD  CONSTRAINT [DF_ChatbotVacios_pv]  DEFAULT (sysdatetime()) FOR [primera_vez]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotVacios_uv]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotVacios] ADD  CONSTRAINT [DF_ChatbotVacios_uv]  DEFAULT (sysdatetime()) FOR [ultima_vez]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotVacios_ca]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotVacios] ADD  CONSTRAINT [DF_ChatbotVacios_ca]  DEFAULT (sysdatetime()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotVacios_ua]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotVacios] ADD  CONSTRAINT [DF_ChatbotVacios_ua]  DEFAULT (sysdatetime()) FOR [updated_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotVacios_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotVacios]'))
ALTER TABLE [pagina_web].[ChatbotVacios]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotVacios_Chatbot] FOREIGN KEY([chatbot_id])
REFERENCES [pagina_web].[Chatbots] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotVacios_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotVacios]'))
ALTER TABLE [pagina_web].[ChatbotVacios] CHECK CONSTRAINT [FK_ChatbotVacios_Chatbot]
GO
