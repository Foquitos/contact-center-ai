-- Table [dbo].[CSV Avaya Horas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV Avaya Horas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV Avaya Horas](
	[Fecha] [date] NULL,
	[Skill] [smallint] NULL,
	[Nombre Skill] [varchar](max) NULL,
	[Nombre Agente] [varchar](max) NULL,
	[Cod Agente] [int] NULL,
	[Hora Inicio Intervalo] [time](7) NULL,
	[-] [varchar](max) NULL,
	[Hora Fin Intervalo] [time](7) NULL,
	[Tiempo ACD] [smallint] NULL,
	[Tiempo ACW] [smallint] NULL,
	[Otra hora] [smallint] NULL,
	[Ring Time] [smallint] NULL,
	[Tiempo AUX] [smallint] NULL,
	[Tiempo dispon] [smallint] NULL,
	[Tiempo con personal] [int] NULL,
	[Tiempo de reten] [smallint] NULL,
	[Tiempo Sa de Ext] [smallint] NULL,
	[Auxtime 0] [smallint] NULL,
	[Auxtime 1] [smallint] NULL,
	[Auxtime 2] [smallint] NULL,
	[Auxtime 3] [smallint] NULL,
	[Auxtime 4] [smallint] NULL,
	[Auxtime 5] [smallint] NULL,
	[Auxtime 6] [smallint] NULL,
	[Auxtime 7] [smallint] NULL,
	[Auxtime 8] [smallint] NULL,
	[Auxtime 9] [smallint] NULL,
	[Llamadas ACD] [smallint] NULL,
	[Llamadas Liberadas] [smallint] NULL,
	[id] [int] IDENTITY(1,1) NOT NULL,
	[horario_id] [tinyint] NULL,
 CONSTRAINT [PK_CSV_Avaya_Horas] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
