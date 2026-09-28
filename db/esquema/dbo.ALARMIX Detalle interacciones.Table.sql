-- Table [dbo].[ALARMIX Detalle interacciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[ALARMIX Detalle interacciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[ALARMIX Detalle interacciones](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Agent ID] [int] NULL,
	[ANI/From] [varchar](max) NULL,
	[DNIS/To] [varchar](max) NULL,
	[Master Contact ID] [bigint] NULL,
	[Contact Start Date Time] [datetime] NULL,
	[Contact End Date Time] [datetime] NULL,
	[Queued] [tinyint] NULL,
	[Inbound Handled] [tinyint] NULL,
	[Abandons] [tinyint] NULL,
	[Outbound] [tinyint] NULL,
	[Outbound Handled] [tinyint] NULL,
	[Avg Abandon Time] [int] NULL,
	[Speed Of Answer] [int] NULL,
	[ACD Time] [int] NULL,
	[Hold Time] [int] NULL,
	[ACW Time] [int] NULL,
	[Refused] [tinyint] NULL,
	[Motivo del fin del contacto] [varchar](max) NULL,
 CONSTRAINT [PK_ALARMIX Detalle interacciones] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
