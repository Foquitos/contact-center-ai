-- Table [dbo].[ALARMIX Auxiliares]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[ALARMIX Auxiliares]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[ALARMIX Auxiliares](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Agent ID] [int] NULL,
	[Login Time] [float] NULL,
	[Inbound] [smallint] NULL,
	[Refused] [smallint] NULL,
	[Outbound Active Talk Time] [float] NULL,
	[Inbound Active Talk Time] [float] NULL,
	[Outbound] [smallint] NULL,
	[Outbound Handled] [smallint] NULL,
	[Intervalo] [smalldatetime] NULL,
 CONSTRAINT [PK_ALARMIX Auxiliares] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
