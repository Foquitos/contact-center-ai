-- Table [dbo].[Sar_agentes]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Sar_agentes]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Sar_agentes](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Usuario] [varchar](max) NULL,
	[Legajo] [varchar](max) NULL,
	[Reclamos  R] [smallint] NULL,
	[Consultas X] [smallint] NULL,
	[Iniciativas I] [smallint] NULL,
	[Gdes. Conductos G] [smallint] NULL,
	[Reiterados] [smallint] NULL,
	[Rellamados] [smallint] NULL,
	[Total] [smallint] NULL,
	[Fecha] [date] NULL,
 CONSTRAINT [PK_Sar_agentes] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
