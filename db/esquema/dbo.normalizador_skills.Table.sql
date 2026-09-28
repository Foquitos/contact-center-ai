-- Table [dbo].[normalizador_skills]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[normalizador_skills]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[normalizador_skills](
	[Skill ID] [bigint] NULL,
	[Skill] [varchar](max) NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
