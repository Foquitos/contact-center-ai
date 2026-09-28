-- Table [dbo].[Voltara_Salesforce_casos_calidad_historico]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara_Salesforce_casos_calidad_historico]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara_Salesforce_casos_calidad_historico](
	[Propietario del caso] [varchar](255) NULL,
	[Nombre de la cuenta] [varchar](255) NULL,
	[Asunto] [varchar](255) NULL,
	[Fecha/Hora de apertura] [datetime] NULL,
	[Antigüedad] [int] NULL,
	[Abierto] [bit] NULL,
	[Cerrado] [bit] NULL,
	[Interacción: Identificador Interacción] [int] NULL,
	[Última modificación de caso realizada por] [varchar](255) NULL,
	[Tipo de caso] [varchar](255) NULL,
	[Última fecha/hora de modificación de caso] [datetime] NULL,
	[Comentarios del caso] [varchar](2000) NULL,
	[Última modificación de Comentario del caso realizada por] [varchar](255) NULL,
	[Caso] [int] NULL
) ON [PRIMARY]
END
GO
