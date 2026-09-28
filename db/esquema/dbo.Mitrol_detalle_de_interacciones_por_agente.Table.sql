-- Table [dbo].[Mitrol_detalle_de_interacciones_por_agente]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Mitrol_detalle_de_interacciones_por_agente]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Mitrol_detalle_de_interacciones_por_agente](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[idInteraccion] [varchar](max) NULL,
	[Segmento] [smallint] NULL,
	[Tipo Contacto ID] [smallint] NULL,
	[Inicio] [datetime] NULL,
 CONSTRAINT [PK_Mitrol_detalle_de_interacciones_por_agente] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
