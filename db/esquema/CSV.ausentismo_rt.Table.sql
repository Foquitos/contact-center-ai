-- Table [CSV].[ausentismo_rt]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[CSV].[ausentismo_rt]') AND type in (N'U'))
BEGIN
CREATE TABLE [CSV].[ausentismo_rt](
	[Fecha] [date] NULL,
	[Identif. de conexión] [varchar](50) NULL,
	[Identif. de conexión.1] [int] NULL,
	[Interno] [int] NULL,
	[Conexion] [datetime] NULL,
	[Desconexion] [datetime] NULL,
	[Fecha Desconexion] [date] NULL,
	[Skill 1] [smallint] NULL,
	[Nivel 1] [smallint] NULL,
	[Skill 2] [smallint] NULL,
	[Nivel 2] [smallint] NULL,
	[Skill 3] [smallint] NULL,
	[Nivel 3] [smallint] NULL,
	[Skill 4] [smallint] NULL,
	[Nivel 4] [smallint] NULL,
	[Skill 5] [smallint] NULL,
	[Nivel 5] [smallint] NULL,
	[Skill 6] [smallint] NULL,
	[Nivel 6] [smallint] NULL,
	[Skill 7] [smallint] NULL,
	[Nivel 7] [smallint] NULL,
	[Skill 8] [smallint] NULL,
	[Nivel 8] [smallint] NULL,
	[Skill 9] [smallint] NULL,
	[Nivel 9] [smallint] NULL,
	[Skill 10] [smallint] NULL,
	[Nivel 10] [smallint] NULL,
	[Skill 11] [smallint] NULL,
	[Nivel 11] [smallint] NULL,
	[Skill 12] [smallint] NULL,
	[Nivel 12] [smallint] NULL,
	[Skill 13] [smallint] NULL,
	[Nivel 13] [smallint] NULL,
	[Skill 14] [smallint] NULL,
	[Nivel 14] [smallint] NULL,
	[Skill 15] [smallint] NULL,
	[Nivel 15] [smallint] NULL,
	[batch_id] [datetime] NULL,
	[inserted_at] [datetime] NULL
) ON [PRIMARY]
END
GO
-- Index [IX_ausentismo_rt_batch]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[CSV].[ausentismo_rt]') AND name = N'IX_ausentismo_rt_batch')
CREATE NONCLUSTERED INDEX [IX_ausentismo_rt_batch] ON [CSV].[ausentismo_rt]
(
	[batch_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
