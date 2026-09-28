-- Table [dbo].[Voltara approach auxiliares]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara approach auxiliares]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara approach auxiliares](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[USERID] [int] NULL,
	[AGENT] [varchar](50) NULL,
	[REASON] [varchar](50) NULL,
	[CTIME] [time](7) NULL,
	[PCT] [smallint] NULL,
	[CTOT] [time](7) NULL,
	[Fecha] [date] NULL,
 CONSTRAINT [PK_Voltara approach auxiliares] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
