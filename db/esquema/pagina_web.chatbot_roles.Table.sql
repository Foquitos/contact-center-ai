-- Table [pagina_web].[chatbot_roles]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[chatbot_roles]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[chatbot_roles](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[nomina_id] [int] NOT NULL,
	[is_admin] [bit] NOT NULL,
	[is_QA] [bit] NOT NULL,
	[is_chatbot_admin] [bit] NOT NULL,
 CONSTRAINT [PK_chatbot_admin] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_chatbot_roles_is_admin]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[chatbot_roles] ADD  CONSTRAINT [DF_chatbot_roles_is_admin]  DEFAULT ((0)) FOR [is_admin]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_chatbot_roles_is_QA]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[chatbot_roles] ADD  CONSTRAINT [DF_chatbot_roles_is_QA]  DEFAULT ((0)) FOR [is_QA]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_chatbot_roles_is_chatbot_admin]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[chatbot_roles] ADD  CONSTRAINT [DF_chatbot_roles_is_chatbot_admin]  DEFAULT ((0)) FOR [is_chatbot_admin]
END
GO
