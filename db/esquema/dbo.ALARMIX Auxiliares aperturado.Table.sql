-- Table [dbo].[ALARMIX Auxiliares aperturado]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[ALARMIX Auxiliares aperturado]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[ALARMIX Auxiliares aperturado](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Agent ID] [int] NULL,
	[Unavailable Code] [varchar](max) NULL,
	[Unavailable Time] [float] NULL,
	[Intervalo] [smalldatetime] NULL,
	[Available Time] [float] NULL,
 CONSTRAINT [PK_ALARMIX Auxiliares aperturado] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
