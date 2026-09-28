-- Table [dbo].[campanas_mitrol]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[campanas_mitrol]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[campanas_mitrol](
	[id] [int] NOT NULL,
	[campana] [varchar](50) NULL,
 CONSTRAINT [PK_Campanas_mitrol] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
