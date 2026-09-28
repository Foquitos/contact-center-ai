-- Table [calidad].[GoldenSetItems]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[GoldenSetItems]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[GoldenSetItems](
	[ItemID] [bigint] IDENTITY(1,1) NOT NULL,
	[GoldenSetID] [int] NOT NULL,
	[IdAplicativo] [nvarchar](200) NOT NULL,
	[AuditoriaID] [bigint] NULL,
	[Split] [nvarchar](10) NOT NULL,
	[Notas] [nvarchar](1000) NULL,
	[AgregadoPorUsuarioID] [int] NULL,
	[FechaAgregado] [datetime2](7) NOT NULL,
	[IsActive] [bit] NOT NULL,
 CONSTRAINT [PK_GoldenSetItems] PRIMARY KEY CLUSTERED 
(
	[ItemID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_GoldenSetItems_Set_Id] UNIQUE NONCLUSTERED 
(
	[GoldenSetID] ASC,
	[IdAplicativo] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_GoldenSetItems_Split]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[GoldenSetItems] ADD  CONSTRAINT [DF_GoldenSetItems_Split]  DEFAULT ('train') FOR [Split]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_GoldenSetItems_Fecha]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[GoldenSetItems] ADD  CONSTRAINT [DF_GoldenSetItems_Fecha]  DEFAULT (sysutcdatetime()) FOR [FechaAgregado]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_GoldenSetItems_Activo]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[GoldenSetItems] ADD  CONSTRAINT [DF_GoldenSetItems_Activo]  DEFAULT ((1)) FOR [IsActive]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_GoldenSetItems_Set]') AND parent_object_id = OBJECT_ID(N'[calidad].[GoldenSetItems]'))
ALTER TABLE [calidad].[GoldenSetItems]  WITH CHECK ADD  CONSTRAINT [FK_GoldenSetItems_Set] FOREIGN KEY([GoldenSetID])
REFERENCES [calidad].[GoldenSets] ([GoldenSetID])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_GoldenSetItems_Set]') AND parent_object_id = OBJECT_ID(N'[calidad].[GoldenSetItems]'))
ALTER TABLE [calidad].[GoldenSetItems] CHECK CONSTRAINT [FK_GoldenSetItems_Set]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_GoldenSetItems_Split]') AND parent_object_id = OBJECT_ID(N'[calidad].[GoldenSetItems]'))
ALTER TABLE [calidad].[GoldenSetItems]  WITH CHECK ADD  CONSTRAINT [CK_GoldenSetItems_Split] CHECK  (([Split]='test' OR [Split]='train'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[calidad].[CK_GoldenSetItems_Split]') AND parent_object_id = OBJECT_ID(N'[calidad].[GoldenSetItems]'))
ALTER TABLE [calidad].[GoldenSetItems] CHECK CONSTRAINT [CK_GoldenSetItems_Split]
GO
