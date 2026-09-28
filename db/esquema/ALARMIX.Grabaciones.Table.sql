-- Table [ALARMIX].[Grabaciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[ALARMIX].[Grabaciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [ALARMIX].[Grabaciones](
	[segmentId] [varchar](50) NULL,
	[interactionId] [varchar](50) NULL,
	[directionType] [varchar](50) NULL,
	[duration] [smallint] NULL,
	[agentName_sort] [varchar](50) NULL,
	[callTagging] [varchar](50) NULL,
	[skillName] [varchar](50) NULL,
	[holdCount] [smallint] NULL,
	[holdTime] [smallint] NULL,
	[segmentContactStartTime] [datetime] NULL,
	[endTime] [datetime] NULL,
	[groupName_sort] [varchar](255) NULL,
	[agentIds] [int] NULL,
	[analyticsCategoryName_sort] [varchar](255) NULL,
	[teamName_sort] [varchar](50) NULL,
	[customerPhoneNumbers] [varchar](50) NULL
) ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_ALARMIX_Grabaciones_segmentId]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[ALARMIX].[Grabaciones]') AND name = N'IX_ALARMIX_Grabaciones_segmentId')
CREATE NONCLUSTERED INDEX [IX_ALARMIX_Grabaciones_segmentId] ON [ALARMIX].[Grabaciones]
(
	[segmentId] ASC
)
INCLUDE([agentIds],[agentName_sort]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
