-- Table [dbo].[Normalizador Trackon]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Normalizador Trackon]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Normalizador Trackon](
	[Skill] [varchar](255) NULL,
	[P] [varchar](255) NULL
) ON [PRIMARY]
END
GO
