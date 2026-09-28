-- Table [planificacion].[Pool]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[Pool]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[Pool](
	[PoolID] [int] IDENTITY(1,1) NOT NULL,
	[CampanaID] [int] NOT NULL,
	[Nombre] [nvarchar](80) NOT NULL,
	[MinOperadores] [smallint] NOT NULL,
	[Activo] [bit] NOT NULL,
	[Nota] [nvarchar](400) NULL,
 CONSTRAINT [PK_Plan_Pool] PRIMARY KEY CLUSTERED 
(
	[PoolID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_Plan_Pool] UNIQUE NONCLUSTERED 
(
	[CampanaID] ASC,
	[Nombre] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Pool_Min]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Pool] ADD  CONSTRAINT [DF_Plan_Pool_Min]  DEFAULT ((0)) FOR [MinOperadores]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[DF_Plan_Pool_Activo]') AND type = 'D')
BEGIN
ALTER TABLE [planificacion].[Pool] ADD  CONSTRAINT [DF_Plan_Pool_Activo]  DEFAULT ((1)) FOR [Activo]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Pool_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Pool]'))
ALTER TABLE [planificacion].[Pool]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Pool_Campana] FOREIGN KEY([CampanaID])
REFERENCES [planificacion].[Campana] ([CampanaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Pool_Campana]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Pool]'))
ALTER TABLE [planificacion].[Pool] CHECK CONSTRAINT [FK_Plan_Pool_Campana]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Pool_Min]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Pool]'))
ALTER TABLE [planificacion].[Pool]  WITH CHECK ADD  CONSTRAINT [CK_Plan_Pool_Min] CHECK  (([MinOperadores]>=(0)))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_Pool_Min]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Pool]'))
ALTER TABLE [planificacion].[Pool] CHECK CONSTRAINT [CK_Plan_Pool_Min]
GO
