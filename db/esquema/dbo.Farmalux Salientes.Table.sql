-- Table [dbo].[Farmalux Salientes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Farmalux Salientes]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Farmalux Salientes](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Agente] [varchar](max) NULL,
	[Sonó] [smallint] NULL,
	[Atendidas (%)] [smallint] NULL,
	[Ring y No Atendidas] [smallint] NULL,
	[AHT Atendidas] [time](7) NULL,
	[AHT Espera] [time](7) NULL,
	[Cortó Agente] [smallint] NULL,
	[Total] [smallint] NULL,
	[Atendidas] [smallint] NULL,
	[No Atendidas] [smallint] NULL,
	[AHT Salientes] [time](7) NULL,
	[AHT TOTAL Atendidas] [time](7) NULL,
	[Fecha] [date] NULL,
 CONSTRAINT [PK_Farmalux Salientes] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
