-- Table [dbo].[Sanciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Sanciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Sanciones](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Creado] [date] NULL,
	[Legajo] [varchar](50) NULL,
	[Solicitante] [varchar](max) NULL,
	[Fecha de falta] [date] NULL,
	[Tipo de falta] [varchar](max) NULL,
	[Estado] [varchar](max) NULL,
	[Observaciones] [varchar](max) NULL,
	[Evolución de estado] [varchar](max) NULL,
	[Sanción a aplicar: Apercibimiento] [bit] NULL,
	[Sanción a aplicar: Suspensión] [bit] NULL,
 CONSTRAINT [PK_Sanciones] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
