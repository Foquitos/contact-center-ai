-- Table [dbo].[Odonto_Plus_Turnero]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Odonto_Plus_Turnero]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Odonto_Plus_Turnero](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[IdTurno] [int] NOT NULL,
	[IdClinica] [int] NULL,
	[IdProfesional] [smallint] NULL,
	[IdPaciente] [int] NULL,
	[telefono] [varchar](max) NULL,
	[celular] [varchar](max) NULL,
	[fechanac] [date] NULL,
	[email] [varchar](max) NULL,
	[direccion] [varchar](max) NULL,
	[Localidad_id] [smallint] NULL,
	[estado_id] [tinyint] NULL,
	[operador] [varchar](max) NULL,
	[FechaHora] [datetime] NULL,
	[esImplante] [bit] NULL,
	[esTratamientoConducto] [bit] NULL,
	[esprimeravez] [bit] NULL,
	[esauditoria] [bit] NULL,
	[esrecupero] [bit] NULL,
	[esortodoncia] [bit] NULL,
	[FechaAlta] [datetime] NULL,
	[recepcionista_id] [smallint] NULL,
	[ComoNosConocio_id] [tinyint] NULL,
	[EsReatencion] [bit] NULL,
	[idProfesionalReatencion] [int] NULL,
	[ProfesionalReatencion] [varchar](max) NULL,
	[TipoReatencion] [varchar](max) NULL,
	[Canal_id] [tinyint] NULL,
	[ConfirmadoWhatsapp] [datetime] NULL,
	[UsuarioWhatsapp] [varchar](max) NULL,
	[ConfirmadoTelefono] [datetime] NULL,
	[UsuarioTelefono] [varchar](max) NULL,
	[Observaciones] [varchar](max) NULL,
 CONSTRAINT [PK_Odonto_Plus_Turnero] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
-- Index [IX_Odonto_Plus_Turnero]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Odonto_Plus_Turnero]') AND name = N'IX_Odonto_Plus_Turnero')
CREATE NONCLUSTERED INDEX [IX_Odonto_Plus_Turnero] ON [dbo].[Odonto_Plus_Turnero]
(
	[FechaAlta] DESC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
