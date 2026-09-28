-- Table [pagina_web].[chatbot_calificaciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[chatbot_calificaciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[chatbot_calificaciones](
	[task_id] [varchar](max) NOT NULL,
	[calificacion] [tinyint] NOT NULL,
	[comentario] [varchar](max) NULL,
	[Fecha] [datetime] NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_chatbot_calificaciones_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[chatbot_calificaciones] ADD  CONSTRAINT [DF_chatbot_calificaciones_Fecha]  DEFAULT (getdate()) FOR [Fecha]
END
GO
