-- Table [dbo].[Voltara_Salesforce_casos_cerrados]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara_Salesforce_casos_cerrados]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara_Salesforce_casos_cerrados](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Caso] [bigint] NULL,
	[Nro. de Cliente] [bigint] NULL,
	[Agente] [varchar](max) NULL,
	[Fecha/Hora apertura] [datetime] NULL,
	[Fecha/Hora modificación] [datetime] NULL,
	[Fecha/Hora cierre] [datetime] NULL,
	[Motivo] [varchar](max) NULL,
	[Submotivo] [varchar](max) NULL,
	[Origen del caso] [varchar](max) NULL,
	[Tipo de caso] [varchar](max) NULL,
	[Observaciones] [varchar](max) NULL,
	[Usuario] [varchar](max) NULL,
	[Fecha cierre]  AS (CONVERT([date],[Fecha/Hora cierre])),
 CONSTRAINT [PK_Voltara_Salesforce_casos_cerrados] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
