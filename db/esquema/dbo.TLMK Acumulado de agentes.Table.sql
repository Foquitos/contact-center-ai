-- Table [dbo].[TLMK Acumulado de agentes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[TLMK Acumulado de agentes]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[TLMK Acumulado de agentes](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Agente] [varchar](max) NULL,
	[Identif. de conexion] [int] NULL,
	[Skill] [smallint] NULL,
	[Llamadas ACD] [smallint] NULL,
	[Tiempo con personal] [int] NULL,
	[Tiempo ACW] [smallint] NULL,
	[Tiempo ACD] [int] NULL,
	[Tiempo de reten.] [smallint] NULL,
	[RINGTIME] [smallint] NULL,
 CONSTRAINT [PK_Acumulado de agentes] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
