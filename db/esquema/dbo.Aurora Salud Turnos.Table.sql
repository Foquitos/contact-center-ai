-- Table [dbo].[Aurora Salud Turnos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Aurora Salud Turnos]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Aurora Salud Turnos](
	[Turno ID] [int] IDENTITY(1,1) NOT NULL,
	[Fecha Turno] [date] NULL,
	[Hora de Inicio] [time](7) NULL,
	[Hora Fin] [time](7) NULL,
	[Fecha Hora Otorgamiento] [datetime] NULL,
	[Estado Turno ID] [tinyint] NULL,
	[Es Sobreturno] [bit] NULL,
	[Es Primera Consulta] [bit] NULL,
	[Es Consultorio Interno] [bit] NULL,
	[Servicio ID] [smallint] NULL,
	[Paciente ID] [int] NULL,
	[Convenio ID] [smallint] NULL,
	[Prestacion ID] [int] NULL,
	[Personal] [varchar](max) NULL,
	[Usuario Asigna] [varchar](max) NULL,
	[Observaciones] [varchar](max) NULL,
	[Tipo Solicitud ID] [tinyint] NULL,
 CONSTRAINT [PK_Aurora Salud Turnos] PRIMARY KEY CLUSTERED 
(
	[Turno ID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
