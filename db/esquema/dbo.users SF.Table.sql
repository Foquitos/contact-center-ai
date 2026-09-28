-- Table [dbo].[users SF]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[users SF]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[users SF](
	[Nombre completo] [varchar](max) NULL,
	[Usuario] [varchar](max) NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
