-- Table [calidad].[transcripciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[transcripciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[transcripciones](
	[ID] [int] IDENTITY(1,1) NOT NULL,
	[IdAplicativo] [varchar](100) NOT NULL,
	[metadata] [nvarchar](max) NULL,
	[segments] [nvarchar](max) NULL,
	[analytics] [nvarchar](max) NULL,
	[input_tokens] [int] NULL,
	[output_tokens] [int] NULL,
	[thoughts_tokens] [int] NULL,
	[fecha_subida] [datetime] NULL,
	[user_id] [int] NULL,
 CONSTRAINT [PK__transcri__3214EC27A8FDA682] PRIMARY KEY CLUSTERED 
(
	[ID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_Calidad_Transcripciones_IdAplicativo]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[transcripciones]') AND name = N'IX_Calidad_Transcripciones_IdAplicativo')
CREATE NONCLUSTERED INDEX [IX_Calidad_Transcripciones_IdAplicativo] ON [calidad].[transcripciones]
(
	[IdAplicativo] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_transcripciones_mitrol_fecha_subida]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[transcripciones] ADD  CONSTRAINT [DF_transcripciones_mitrol_fecha_subida]  DEFAULT (getdate()) FOR [fecha_subida]
END
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[analytics debe ser un json valido]') AND parent_object_id = OBJECT_ID(N'[calidad].[transcripciones]'))
ALTER TABLE [calidad].[transcripciones]  WITH CHECK ADD  CONSTRAINT [analytics debe ser un json valido] CHECK  ((isjson([analytics])>(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[analytics debe ser un json valido]') AND parent_object_id = OBJECT_ID(N'[calidad].[transcripciones]'))
ALTER TABLE [calidad].[transcripciones] CHECK CONSTRAINT [analytics debe ser un json valido]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[La metadata debe ser un JSON valido]') AND parent_object_id = OBJECT_ID(N'[calidad].[transcripciones]'))
ALTER TABLE [calidad].[transcripciones]  WITH CHECK ADD  CONSTRAINT [La metadata debe ser un JSON valido] CHECK  ((isjson([metadata])>(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[La metadata debe ser un JSON valido]') AND parent_object_id = OBJECT_ID(N'[calidad].[transcripciones]'))
ALTER TABLE [calidad].[transcripciones] CHECK CONSTRAINT [La metadata debe ser un JSON valido]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[Segments debe ser un Json valido]') AND parent_object_id = OBJECT_ID(N'[calidad].[transcripciones]'))
ALTER TABLE [calidad].[transcripciones]  WITH CHECK ADD  CONSTRAINT [Segments debe ser un Json valido] CHECK  ((isjson([segments])>(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[Segments debe ser un Json valido]') AND parent_object_id = OBJECT_ID(N'[calidad].[transcripciones]'))
ALTER TABLE [calidad].[transcripciones] CHECK CONSTRAINT [Segments debe ser un Json valido]
GO
