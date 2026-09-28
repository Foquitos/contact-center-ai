-- Table [dbo].[TLMK Llamadas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[TLMK Llamadas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[TLMK Llamadas](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[fecha] [date] NULL,
	[login] [int] NULL,
	[agente] [varchar](max) NULL,
	[extn] [int] NULL,
	[skill] [smallint] NULL,
	[acdcalls] [smallint] NULL,
	[acd+acw+hold] [smallint] NULL,
	[i_acdtime] [smallint] NULL,
	[i_acwtime] [smallint] NULL,
	[holdtime] [smallint] NULL,
	[outtime] [smallint] NULL,
	[i_auxouttime] [smallint] NULL,
	[i_acwouttime] [smallint] NULL,
	[outcalls] [smallint] NULL,
	[auxoutcalls] [smallint] NULL,
	[acwoutcalls] [smallint] NULL,
	[holdcalls] [smallint] NULL,
	[holdabncalls] [smallint] NULL,
	[transferred] [smallint] NULL,
	[conference] [smallint] NULL,
	[i_ringtime] [smallint] NULL,
	[anstingtime] [smallint] NULL,
	[abntime] [smallint] NULL,
 CONSTRAINT [PK_TLMK DW Llamadas X Banco] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
