-- Table [dbo].[Intervalos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Intervalos]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Intervalos](
	[Interval_Start] [datetime] NULL
) ON [PRIMARY]
END
GO
