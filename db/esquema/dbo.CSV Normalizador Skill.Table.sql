-- Table [dbo].[CSV Normalizador Skill]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV Normalizador Skill]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV Normalizador Skill](
	[Skill_ID] [int] NULL,
	[Skill_Name] [varchar](max) NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
