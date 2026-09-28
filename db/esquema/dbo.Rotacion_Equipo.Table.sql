-- Table [dbo].[Rotacion_Equipo]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Rotacion_Equipo]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Rotacion_Equipo](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Equipo_id] [int] NULL,
	[HEADCOUNT] [smallint] NULL,
	[ANTIGUEDAD PROM.] [smallint] NULL,
	[ALTAS] [smallint] NULL,
	[BAJAS] [smallint] NULL,
	[ROTACIÓN] [float] NULL,
	[BAJAS FORZOSAS] [smallint] NULL,
	[ROTACIÓN FORZOSA] [float] NULL,
	[BAJAS VOLUNTARIAS] [smallint] NULL,
	[ROTACIÓN VOLUNTARIA] [float] NULL,
	[BAJAS TEMPRANAS] [smallint] NULL,
	[ROTACIÓN TEMPRANA] [float] NULL,
	[FECHA] [date] NULL,
 CONSTRAINT [PK_Rotacion_Equipo] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
