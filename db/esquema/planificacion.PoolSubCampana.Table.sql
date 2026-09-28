-- Table [planificacion].[PoolSubCampana]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[PoolSubCampana]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[PoolSubCampana](
	[PoolID] [int] NOT NULL,
	[CampanaRRHHID] [smallint] NOT NULL,
	[Clase] [varchar](20) NOT NULL,
	[Nota] [nvarchar](200) NULL,
 CONSTRAINT [PK_Plan_PoolSubCampana] PRIMARY KEY CLUSTERED 
(
	[PoolID] ASC,
	[CampanaRRHHID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_PoolSubCampana_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PoolSubCampana]'))
ALTER TABLE [planificacion].[PoolSubCampana]  WITH CHECK ADD  CONSTRAINT [FK_Plan_PoolSubCampana_Pool] FOREIGN KEY([PoolID])
REFERENCES [planificacion].[Pool] ([PoolID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_PoolSubCampana_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PoolSubCampana]'))
ALTER TABLE [planificacion].[PoolSubCampana] CHECK CONSTRAINT [FK_Plan_PoolSubCampana_Pool]
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_PoolSubCampana_Clase]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PoolSubCampana]'))
ALTER TABLE [planificacion].[PoolSubCampana]  WITH CHECK ADD  CONSTRAINT [CK_Plan_PoolSubCampana_Clase] CHECK  (([Clase]='dedicada' OR [Clase]='digital' OR [Clase]='telefonica_parcial'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_PoolSubCampana_Clase]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PoolSubCampana]'))
ALTER TABLE [planificacion].[PoolSubCampana] CHECK CONSTRAINT [CK_Plan_PoolSubCampana_Clase]
GO
