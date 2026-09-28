-- Table [dbo].[Aurora Salud Pacientes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Aurora Salud Pacientes]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Aurora Salud Pacientes](
	[Paciente ID] [int] IDENTITY(1,1) NOT NULL,
	[Apellido y Nombre] [varchar](max) NULL,
	[Tipo Documento] [varchar](max) NULL,
	[Nro Documento] [bigint] NULL,
	[Telefonos] [varchar](max) NULL,
	[Correo Electronico] [varchar](max) NULL,
 CONSTRAINT [PK_Aurora Salud Pacientes] PRIMARY KEY CLUSTERED 
(
	[Paciente ID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
