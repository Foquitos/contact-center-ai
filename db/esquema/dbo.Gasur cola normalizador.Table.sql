-- Table [dbo].[Gasur cola normalizador]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Gasur cola normalizador]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Gasur cola normalizador](
	[id] [tinyint] IDENTITY(1,1) NOT NULL,
	[Cola nombre] [varchar](max) NULL,
 CONSTRAINT [PK_Gasur cola normalizador] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
