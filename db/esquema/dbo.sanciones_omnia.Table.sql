-- Table [dbo].[sanciones_omnia]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[sanciones_omnia]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[sanciones_omnia](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Creado] [date] NULL,
	[Documento] [int] NULL,
	[Legajo] [int] NULL,
	[Apellidos] [varchar](max) NULL,
	[Nombres] [varchar](max) NULL,
	[Cliente] [varchar](max) NULL,
	[Campaña] [varchar](max) NULL,
	[Sub-campaña] [varchar](max) NULL,
	[Solicitante] [varchar](max) NULL,
	[Fecha de falta] [date] NULL,
	[Tipo de falta] [varchar](max) NULL,
	[Sanción a aplicar] [varchar](max) NULL,
	[Aplicar penalidad desde] [float] NULL,
	[Aplicar penalidad hasta] [float] NULL,
	[Estado] [varchar](max) NULL,
 CONSTRAINT [PK_sanciones_omnia] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
