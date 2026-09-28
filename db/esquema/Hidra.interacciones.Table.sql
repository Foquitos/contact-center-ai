-- Table [Hidra].[interacciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[Hidra].[interacciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [Hidra].[interacciones](
	[ID] [varchar](40) NOT NULL,
	[CallLocalTime] [datetime] NOT NULL,
	[Duration] [smallint] NOT NULL,
	[AcceptDuration] [smallint] NOT NULL,
	[IvrDuration] [smallint] NOT NULL,
	[WaitDuration] [smallint] NOT NULL,
	[TotalWaitDuration] [smallint] NOT NULL,
	[ConvDuration] [smallint] NOT NULL,
	[WrapupDuration] [smallint] NOT NULL,
	[RerouteDuration] [smallint] NOT NULL,
	[OverflowDuration] [smallint] NOT NULL,
	[Closed] [smallint] NOT NULL,
	[NoAgent] [smallint] NOT NULL,
	[Overflow] [smallint] NOT NULL,
	[Abandon] [smallint] NOT NULL,
	[CallStatusGroup] [smallint] NOT NULL,
	[CallStatusNum] [smallint] NOT NULL,
	[CallStatusDetail] [smallint] NOT NULL,
	[EndByAgent] [smallint] NOT NULL,
	[AgentListen] [smallint] NOT NULL,
	[EndReason] [smallint] NOT NULL,
	[Rec_Filename] [varchar](128) NOT NULL,
	[Rec_Duration] [smallint] NOT NULL,
	[Rec_AgentId] [smallint] NOT NULL
) ON [PRIMARY]
END
GO
