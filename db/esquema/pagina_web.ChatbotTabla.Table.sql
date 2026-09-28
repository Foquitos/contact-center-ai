-- Table [pagina_web].[ChatbotTabla]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotTabla]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[ChatbotTabla](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[chatbot_id] [int] NOT NULL,
	[nombre] [nvarchar](200) NOT NULL,
	[descripcion] [nvarchar](1000) NOT NULL,
	[terminos] [nvarchar](1000) NULL,
	[columnas] [nvarchar](max) NOT NULL,
	[claves] [nvarchar](400) NULL,
	[nota] [nvarchar](1000) NULL,
	[modo] [nvarchar](20) NOT NULL,
	[filas_total] [int] NOT NULL,
	[descripcion_vector] [nvarchar](max) NULL,
	[vector_modelo] [nvarchar](100) NULL,
	[activo] [bit] NOT NULL,
	[doc_origen_id] [int] NULL,
	[created_by] [int] NULL,
	[created_at] [datetime2](3) NOT NULL,
	[updated_at] [datetime2](3) NOT NULL,
 CONSTRAINT [PK_ChatbotTabla] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_ChatbotTabla_bot]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[pagina_web].[ChatbotTabla]') AND name = N'IX_ChatbotTabla_bot')
CREATE NONCLUSTERED INDEX [IX_ChatbotTabla_bot] ON [pagina_web].[ChatbotTabla]
(
	[chatbot_id] ASC,
	[activo] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotTabla_desc]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotTabla] ADD  CONSTRAINT [DF_ChatbotTabla_desc]  DEFAULT (N'') FOR [descripcion]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotTabla_modo]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotTabla] ADD  CONSTRAINT [DF_ChatbotTabla_modo]  DEFAULT ('auto') FOR [modo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotTabla_filas]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotTabla] ADD  CONSTRAINT [DF_ChatbotTabla_filas]  DEFAULT ((0)) FOR [filas_total]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotTabla_activo]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotTabla] ADD  CONSTRAINT [DF_ChatbotTabla_activo]  DEFAULT ((1)) FOR [activo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotTabla_created]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotTabla] ADD  CONSTRAINT [DF_ChatbotTabla_created]  DEFAULT (sysutcdatetime()) FOR [created_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_ChatbotTabla_updated]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[ChatbotTabla] ADD  CONSTRAINT [DF_ChatbotTabla_updated]  DEFAULT (sysutcdatetime()) FOR [updated_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotTabla_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotTabla]'))
ALTER TABLE [pagina_web].[ChatbotTabla]  WITH CHECK ADD  CONSTRAINT [FK_ChatbotTabla_Chatbot] FOREIGN KEY([chatbot_id])
REFERENCES [pagina_web].[Chatbots] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[pagina_web].[FK_ChatbotTabla_Chatbot]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotTabla]'))
ALTER TABLE [pagina_web].[ChatbotTabla] CHECK CONSTRAINT [FK_ChatbotTabla_Chatbot]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_ChatbotTabla_modo]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotTabla]'))
ALTER TABLE [pagina_web].[ChatbotTabla]  WITH CHECK ADD  CONSTRAINT [CK_ChatbotTabla_modo] CHECK  (([modo]='lookup' OR [modo]='completa' OR [modo]='auto'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_ChatbotTabla_modo]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[ChatbotTabla]'))
ALTER TABLE [pagina_web].[ChatbotTabla] CHECK CONSTRAINT [CK_ChatbotTabla_modo]
GO
