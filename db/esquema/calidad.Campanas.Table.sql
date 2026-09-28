-- Table [calidad].[Campanas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[Campanas]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[Campanas](
	[CampanaID] [int] IDENTITY(1,1) NOT NULL,
	[EmpresaID] [int] NOT NULL,
	[Nombre] [nvarchar](255) NOT NULL,
	[FechaCreacion] [datetime2](7) NULL,
	[PlataformaID] [int] NULL,
	[IsActive] [bit] NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[CampanaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Campanas_IsActive]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Campanas]') AND name = N'IX_Campanas_IsActive')
CREATE NONCLUSTERED INDEX [IX_Campanas_IsActive] ON [calidad].[Campanas]
(
	[IsActive] ASC
)
WHERE ([IsActive]=(1))
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__Campanas__FechaC__53433F50]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Campanas] ADD  DEFAULT (getdate()) FOR [FechaCreacion]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__Campanas__IsActi__141CDE74]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Campanas] ADD  DEFAULT ((1)) FOR [IsActive]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Campanas_Empresas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Campanas]'))
ALTER TABLE [calidad].[Campanas]  WITH CHECK ADD  CONSTRAINT [FK_Campanas_Empresas] FOREIGN KEY([EmpresaID])
REFERENCES [calidad].[Empresas] ([EmpresaID])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Campanas_Empresas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Campanas]'))
ALTER TABLE [calidad].[Campanas] CHECK CONSTRAINT [FK_Campanas_Empresas]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Campanas_Plataformas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Campanas]'))
ALTER TABLE [calidad].[Campanas]  WITH CHECK ADD  CONSTRAINT [FK_Campanas_Plataformas] FOREIGN KEY([PlataformaID])
REFERENCES [calidad].[Plataformas] ([PlataformaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Campanas_Plataformas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Campanas]'))
ALTER TABLE [calidad].[Campanas] CHECK CONSTRAINT [FK_Campanas_Plataformas]
GO
