-- Table [dbo].[Transcripciones_Mitrol]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Transcripciones_Mitrol]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Transcripciones_Mitrol](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[idInteraccion] [varchar](255) NOT NULL,
	[Segmento] [tinyint] NULL,
	[Hold] [tinyint] NULL,
	[canales] [tinyint] NULL,
	[Interrupciones] [float] NULL,
	[Transcripcion] [varchar](max) NOT NULL,
	[FechaSubida] [datetime] NULL,
 CONSTRAINT [PK_Transcripciones_Mitrol] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF__Transcrip__Fecha__4297D63B]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[Transcripciones_Mitrol] ADD  DEFAULT (getdate()) FOR [FechaSubida]
END
GO
