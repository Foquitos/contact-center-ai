-- Table [dbo].[Normalizador_VOLTARA]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Normalizador_VOLTARA]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Normalizador_VOLTARA](
	[Skill] [varchar](50) NULL,
	[Agrupacion] [varchar](50) NULL
) ON [PRIMARY]
END
GO
