-- Table [dbo].[Gasur Eficiencia]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Gasur Eficiencia]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Gasur Eficiencia](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Horas] [time](0) NOT NULL,
	[Completadas] [smallint] NULL,
	[% Completadas] [float] NULL,
	[Transferidas] [smallint] NULL,
	[% Transferidas] [float] NULL,
	[Dur. Max] [time](0) NULL,
	[Abandonadas] [smallint] NULL,
	[% Abandonadas] [float] NULL,
	[Espera Max] [time](0) NULL,
	[Promedio] [float] NULL,
	[Nivel de Servicio %] [float] NULL,
	[Total] [smallint] NULL,
	[Fecha] [date] NULL,
 CONSTRAINT [PK_Gasur Eficiencia] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
