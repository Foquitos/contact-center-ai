-- Table [calidad].[Skills]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[Skills]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[Skills](
	[SkillID] [int] IDENTITY(1,1) NOT NULL,
	[Nombre] [nvarchar](255) NOT NULL,
	[CampanaID] [int] NOT NULL,
	[IsActive] [bit] NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[SkillID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_Skills_CampanaID_Nombre] UNIQUE NONCLUSTERED 
(
	[CampanaID] ASC,
	[Nombre] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Skills_IsActive]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Skills]') AND name = N'IX_Skills_IsActive')
CREATE NONCLUSTERED INDEX [IX_Skills_IsActive] ON [calidad].[Skills]
(
	[IsActive] ASC
)
WHERE ([IsActive]=(1))
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__Skills__IsActive__17ED6F58]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Skills] ADD  DEFAULT ((1)) FOR [IsActive]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Skills_Campanas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Skills]'))
ALTER TABLE [calidad].[Skills]  WITH CHECK ADD  CONSTRAINT [FK_Skills_Campanas] FOREIGN KEY([CampanaID])
REFERENCES [calidad].[Campanas] ([CampanaID])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Skills_Campanas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Skills]'))
ALTER TABLE [calidad].[Skills] CHECK CONSTRAINT [FK_Skills_Campanas]
GO
