-- Table [dbo].[TLMK Speedy Agente Skill]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[TLMK Speedy Agente Skill]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[TLMK Speedy Agente Skill](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha Intervalo] [datetime] NULL,
	[Split/Skill] [varchar](max) NULL,
	[Agente] [varchar](max) NULL,
	[Llamadas Salientes Externas] [smallint] NULL,
	[Llamadas Recibidas] [smallint] NULL,
	[Llamadas Atendidas] [smallint] NULL,
	[Llamadas aban.] [smallint] NULL,
	[TMO Saliente] [smallint] NULL,
	[RINGTIME] [smallint] NULL,
	[Tiempo ACD] [smallint] NULL,
	[Tiempo ACW] [smallint] NULL,
	[Tiempo de reten.] [smallint] NULL,
	[Tiempo dispon.] [smallint] NULL,
	[Tiempo AUX] [smallint] NULL,
	[Break] [smallint] NULL,
	[Baño] [smallint] NULL,
	[Capacitacion 1] [smallint] NULL,
	[Capacitacion 2] [smallint] NULL,
	[Reunion] [smallint] NULL,
	[Coaching] [smallint] NULL,
	[Tiempo con personal] [smallint] NULL,
	[Conexion] [smallint] NULL,
	[PHANTOMABNS] [smallint] NULL,
 CONSTRAINT [PK_TLMK Speedy Agente Skill] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
