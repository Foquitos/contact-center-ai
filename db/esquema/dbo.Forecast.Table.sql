-- Table [dbo].[Forecast]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Forecast]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Forecast](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Intervalo] [time](0) NULL,
	[Forecast] [smallint] NULL,
	[Campaña] [varchar](max) NULL,
	[Skill ID] [int] NULL,
 CONSTRAINT [PK_Forecast] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
