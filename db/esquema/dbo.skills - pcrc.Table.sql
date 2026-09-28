-- Table [dbo].[skills - pcrc]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[skills - pcrc]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[skills - pcrc](
	[Skill_ID] [smallint] NOT NULL,
	[PCRC] [varchar](50) NOT NULL,
 CONSTRAINT [PK_skills - pcrc] PRIMARY KEY CLUSTERED 
(
	[Skill_ID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
