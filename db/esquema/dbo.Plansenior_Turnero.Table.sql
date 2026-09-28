-- Table [dbo].[Plansenior_Turnero]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Plansenior_Turnero]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Plansenior_Turnero](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[IdTurno] [int] NULL,
	[IdClinica] [int] NULL,
	[IdProfesional] [int] NULL,
	[profesional] [varchar](100) NULL,
	[IdPaciente] [int] NULL,
	[paciente] [varchar](100) NULL,
	[telefono] [varchar](255) NULL,
	[celular] [varchar](100) NULL,
	[fechanac] [date] NULL,
	[email] [varchar](100) NULL,
	[direccion] [varchar](100) NULL,
	[localidad] [varchar](100) NULL,
	[DNI] [varchar](50) NULL,
	[Estado_Id] [int] NULL,
	[operador] [varchar](100) NULL,
	[FechaHora] [datetime] NULL,
	[esImplante] [bit] NULL,
	[esAuditoria] [bit] NULL,
	[Observaciones] [nvarchar](max) NULL,
	[FechaAlta] [datetime] NULL,
	[recepcionista] [nvarchar](100) NULL,
	[ComoNosConocio_Id] [int] NULL,
	[Anio] [int] NULL,
	[Mes] [int] NULL,
	[Dia] [int] NULL,
	[Canal_id] [int] NULL,
	[ConfirmadoWhatsapp] [datetime] NULL,
	[UsuarioWhatsapp] [varchar](100) NULL,
	[ConfirmadoTelefono] [datetime] NULL,
	[UsuarioTelefono] [varchar](100) NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
