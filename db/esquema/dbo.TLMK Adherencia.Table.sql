-- Table [dbo].[TLMK Adherencia]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[TLMK Adherencia]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[TLMK Adherencia](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Agente] [varchar](max) NULL,
	[Login] [int] NULL,
	[Extn] [int] NULL,
	[Skill] [smallint] NULL,
	[Ll. ACD] [smallint] NULL,
	[Re-direc] [smallint] NULL,
	[Ll.Hold] [smallint] NULL,
	[Transf] [smallint] NULL,
	[Conf.] [smallint] NULL,
	[S. Ext] [smallint] NULL,
	[S. Externas] [smallint] NULL,
	[T. ACD] [smallint] NULL,
	[T. ACW] [smallint] NULL,
	[Ring] [smallint] NULL,
	[Other] [int] NULL,
	[T. AUX] [int] NULL,
	[T.AUXOUT] [smallint] NULL,
	[T.AUXIN] [smallint] NULL,
	[T.ACWIN] [smallint] NULL,
	[T.ACWOUT] [smallint] NULL,
	[T.Hold] [smallint] NULL,
	[Avail] [int] NULL,
	[Staff] [int] NULL
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
