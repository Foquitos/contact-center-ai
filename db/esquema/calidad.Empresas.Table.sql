-- Table [calidad].[Empresas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[Empresas]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[Empresas](
	[EmpresaID] [int] IDENTITY(1,1) NOT NULL,
	[Nombre] [nvarchar](255) NOT NULL,
	[FechaCreacion] [datetime2](7) NULL,
	[IsActive] [bit] NOT NULL,
	[RequiredPermissionID] [int] NULL,
PRIMARY KEY CLUSTERED 
(
	[EmpresaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
UNIQUE NONCLUSTERED 
(
	[Nombre] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Empresas_IsActive]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Empresas]') AND name = N'IX_Empresas_IsActive')
CREATE NONCLUSTERED INDEX [IX_Empresas_IsActive] ON [calidad].[Empresas]
(
	[IsActive] ASC
)
WHERE ([IsActive]=(1))
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__Empresas__FechaC__5066D2A5]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Empresas] ADD  DEFAULT (getdate()) FOR [FechaCreacion]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__Empresas__IsActi__1328BA3B]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Empresas] ADD  DEFAULT ((1)) FOR [IsActive]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Empresas_Permissions]') AND parent_object_id = OBJECT_ID(N'[calidad].[Empresas]'))
ALTER TABLE [calidad].[Empresas]  WITH CHECK ADD  CONSTRAINT [FK_Empresas_Permissions] FOREIGN KEY([RequiredPermissionID])
REFERENCES [pagina_web].[Permissions] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Empresas_Permissions]') AND parent_object_id = OBJECT_ID(N'[calidad].[Empresas]'))
ALTER TABLE [calidad].[Empresas] CHECK CONSTRAINT [FK_Empresas_Permissions]
GO
