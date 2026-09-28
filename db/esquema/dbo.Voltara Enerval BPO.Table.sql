-- Table [dbo].[Voltara Enerval BPO]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval BPO]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara Enerval BPO](
	[id] [tinyint] IDENTITY(1,1) NOT NULL,
	[BPO] [varchar](50) NOT NULL
) ON [PRIMARY]
END
GO
