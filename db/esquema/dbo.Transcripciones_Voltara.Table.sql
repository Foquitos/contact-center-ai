-- Table [dbo].[Transcripciones_Voltara]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Transcripciones_Voltara]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Transcripciones_Voltara](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[ID_llamado] [int] NULL,
	[ConnID] [varchar](max) NULL,
	[Hold] [tinyint] NULL,
	[canales] [tinyint] NULL,
	[Interrupciones] [float] NULL,
	[Transcripcion] [varchar](max) NULL,
	[FechaSubida] [datetime] NULL,
 CONSTRAINT [PK_Transcripciones_Voltara] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF_Transcripciones_Voltara_FechaSubida]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[Transcripciones_Voltara] ADD  CONSTRAINT [DF_Transcripciones_Voltara_FechaSubida]  DEFAULT (getdate()) FOR [FechaSubida]
END
GO
