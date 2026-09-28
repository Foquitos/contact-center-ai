-- Table [dbo].[Voltara TMO por Skill]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara TMO por Skill]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara TMO por Skill](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Hora] [time](7) NULL,
	[Valor] [smallint] NULL,
	[Skill ID] [smallint] NULL
) ON [PRIMARY]
END
GO
