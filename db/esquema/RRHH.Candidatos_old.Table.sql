-- Table [RRHH].[Candidatos_old]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[RRHH].[Candidatos_old]') AND type in (N'U'))
BEGIN
CREATE TABLE [RRHH].[Candidatos_old](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[FechaSubida] [datetime] NULL,
	[UsuarioSubida] [varchar](100) NULL,
	[NombreArchivo] [varchar](255) NULL,
	[Datos] [nvarchar](max) NULL,
	[IsActive] [bit] NULL,
 CONSTRAINT [PK_Candidatos] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[RRHH].[DF__Candidato__IsAct__60683044]') AND type = 'D')
BEGIN
ALTER TABLE [RRHH].[Candidatos_old] ADD  DEFAULT ((1)) FOR [IsActive]
END
GO
