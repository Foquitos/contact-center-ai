-- Table [dbo].[CSV AHT]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV AHT]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV AHT](
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
	[T. ACD] [int] NULL,
	[T. ACW] [int] NULL,
	[Ring] [int] NULL,
	[Other] [int] NULL,
	[T. AUX] [int] NULL,
	[T.AUXOUT] [int] NULL,
	[T.AUXIN] [int] NULL,
	[T.ACWIN] [int] NULL,
	[T.ACWOUT] [int] NULL,
	[T.Hold] [int] NULL,
	[Avail] [int] NULL,
	[Staff] [int] NULL,
 CONSTRAINT [PK_CSV AHT] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
