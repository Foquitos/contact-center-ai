-- Table [dbo].[Voltara Enerval Salientes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval Salientes]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara Enerval Salientes](
	[Fecha de Inicio] [datetime] NULL,
	[Fecha de Fin] [datetime] NULL,
	[ConnID] [varchar](max) NULL,
	[ANI] [varchar](max) NULL,
	[BPO] [varchar](max) NULL,
	[Región] [varchar](max) NULL,
	[Nombre de Agente] [varchar](max) NULL,
	[Agente] [varchar](max) NULL,
	[Extensión del agente] [int] NULL,
	[Tiempo Total] [smallint] NULL,
	[Duración Ring] [smallint] NULL,
	[Duración Talk] [smallint] NULL,
	[Duración Hold] [smallint] NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
