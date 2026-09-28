-- Table [RRHH].[Candidatos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[RRHH].[Candidatos]') AND type in (N'U'))
BEGIN
CREATE TABLE [RRHH].[Candidatos](
	[DNI] [varchar](50) NOT NULL,
	[CUIL] [varchar](50) NULL,
	[FechaSubida] [datetime] NULL,
	[Apellido] [varchar](100) NULL,
	[Nombre] [varchar](100) NULL,
	[Email] [varchar](150) NULL,
	[Telefono] [varchar](50) NULL,
	[Sede] [varchar](100) NULL,
	[Turno] [varchar](100) NULL,
	[Zona] [varchar](150) NULL,
	[Exp_Atencion_Cliente] [varchar](10) NULL,
	[Exp_Ventas] [varchar](10) NULL,
	[Exp_Cobranzas] [varchar](10) NULL,
	[Comentarios] [nvarchar](max) NULL,
	[Hijos] [varchar](10) NULL,
	[Analitico] [varchar](10) NULL,
	[Estudiando] [varchar](10) NULL,
	[Estado] [varchar](50) NULL,
	[Entrevista_Fecha] [varchar](20) NULL,
	[Entrevista_Hora] [varchar](20) NULL,
	[Entrevista_Lugar] [varchar](255) NULL,
	[Entrevista_Campana] [varchar](100) NULL,
	[Sincronizado_Ingresos] [bit] NULL,
	[Mail_Enviado] [bit] NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[DNI] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[RRHH].[DF__Candidato__Estad__54575F1A]') AND type = 'D')
BEGIN
ALTER TABLE [RRHH].[Candidatos] ADD  DEFAULT ('PENDIENTE') FOR [Estado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[RRHH].[DF__Candidato__Sincr__554B8353]') AND type = 'D')
BEGIN
ALTER TABLE [RRHH].[Candidatos] ADD  DEFAULT ((0)) FOR [Sincronizado_Ingresos]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[RRHH].[DF_Candidatos_Mail_Enviado]') AND type = 'D')
BEGIN
ALTER TABLE [RRHH].[Candidatos] ADD  CONSTRAINT [DF_Candidatos_Mail_Enviado]  DEFAULT ((0)) FOR [Mail_Enviado]
END
GO
