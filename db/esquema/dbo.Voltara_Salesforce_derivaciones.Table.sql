-- Table [dbo].[Voltara_Salesforce_derivaciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara_Salesforce_derivaciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara_Salesforce_derivaciones](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Usuario] [varchar](max) NULL,
	[Elemento de trabajo] [bigint] NULL,
	[Cola] [varchar](max) NULL,
	[Fecha/Hora solicitud] [datetime] NULL,
	[Fecha/Hora asignación] [datetime] NULL,
	[Fecha/Hora aceptación] [datetime] NULL,
	[Fecha/Hora cierre] [datetime] NULL,
	[Velocidad respuesta] [int] NULL,
	[Tiempo tratado] [int] NULL,
	[Tiempo activo] [int] NULL,
	[Fecha asignación]  AS (CONVERT([date],[Fecha/Hora asignación])),
 CONSTRAINT [PK_Voltara_Salesforce_derivaciones] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
