-- Table [dbo].[TLMK Gerencial]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[TLMK Gerencial]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[TLMK Gerencial](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha Intervalo] [datetime] NULL,
	[Skill] [smallint] NULL,
	[Nombre Skill] [varchar](max) NULL,
	[Ofrecidas (sin aba<20)] [smallint] NULL,
	[ACD en SL] [smallint] NULL,
	[Llamadas ACD] [smallint] NULL,
	[Abandonadas > 20'] [smallint] NULL,
	[AHT] [int] NULL,
	[ASA] [int] NULL,
	[Avail] [int] NULL,
 CONSTRAINT [PK_TLMK Gerencial] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
