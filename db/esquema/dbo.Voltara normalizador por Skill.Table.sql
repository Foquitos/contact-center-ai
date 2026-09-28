-- Table [dbo].[Voltara normalizador por Skill]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara normalizador por Skill]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara normalizador por Skill](
	[Skill ID] [int] IDENTITY(1,1) NOT NULL,
	[Skill] [varchar](max) NULL,
 CONSTRAINT [PK_Forecast por Skill] PRIMARY KEY CLUSTERED 
(
	[Skill ID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
