-- Table [dbo].[Planes de accion]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Planes de accion]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Planes de accion](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Legajo] [varchar](50) NULL,
	[Plan ID] [int] NULL,
	[Creado] [datetime] NULL,
	[Creado por] [varchar](max) NULL,
	[Superior] [varchar](max) NULL,
	[Tipo] [varchar](max) NULL,
	[Métrica trabajada] [varchar](max) NULL,
	[Descripción] [varchar](max) NULL,
	[Conclusión] [varchar](max) NULL,
	[Notas] [varchar](max) NULL,
	[Reconocido] [datetime] NULL,
	[Objetivos] [varchar](max) NULL,
 CONSTRAINT [PK_Planes de accion] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
