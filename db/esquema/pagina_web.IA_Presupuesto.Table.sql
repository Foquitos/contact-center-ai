-- Table [pagina_web].[IA_Presupuesto]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[IA_Presupuesto]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[IA_Presupuesto](
	[id] [int] NOT NULL,
	[monto_usd] [decimal](12, 2) NOT NULL,
	[umbrales] [nvarchar](100) NOT NULL,
	[destinatarios] [nvarchar](500) NULL,
	[activo] [bit] NOT NULL,
	[updated_by] [nvarchar](50) NULL,
	[updated_at] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_IA_Presupuesto] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_IA_Presupuesto_umbrales]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[IA_Presupuesto] ADD  CONSTRAINT [DF_IA_Presupuesto_umbrales]  DEFAULT ('50,75,90,100') FOR [umbrales]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_IA_Presupuesto_activo]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[IA_Presupuesto] ADD  CONSTRAINT [DF_IA_Presupuesto_activo]  DEFAULT ((1)) FOR [activo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_IA_Presupuesto_updated]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[IA_Presupuesto] ADD  CONSTRAINT [DF_IA_Presupuesto_updated]  DEFAULT (sysutcdatetime()) FOR [updated_at]
END
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_IA_Presupuesto_singleton]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[IA_Presupuesto]'))
ALTER TABLE [pagina_web].[IA_Presupuesto]  WITH CHECK ADD  CONSTRAINT [CK_IA_Presupuesto_singleton] CHECK  (([id]=(1)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[pagina_web].[CK_IA_Presupuesto_singleton]') AND parent_object_id = OBJECT_ID(N'[pagina_web].[IA_Presupuesto]'))
ALTER TABLE [pagina_web].[IA_Presupuesto] CHECK CONSTRAINT [CK_IA_Presupuesto_singleton]
GO
