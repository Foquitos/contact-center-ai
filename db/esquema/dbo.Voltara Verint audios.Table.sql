-- Table [dbo].[Voltara Verint audios]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Verint audios]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara Verint audios](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Hora de inicio] [datetime] NULL,
	[Duración de interacción] [time](7) NULL,
	[Empleado] [varchar](max) NULL,
	[Marcado desde (ANI)] [varchar](max) NULL,
	[Marcado a (DNIS)] [bigint] NULL,
	[Extensión] [int] NULL,
	[Tiempo total en espera de la interacción] [time](7) NULL,
	[ConnID] [varchar](max) NULL,
	[Genesys UUID] [varchar](max) NULL,
	[Entrante] [bit] NULL,
	[ID_llamado] [int] NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
