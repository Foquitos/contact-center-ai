-- Table [planificacion].[PoolRefuerzo]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[PoolRefuerzo]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[PoolRefuerzo](
	[PoolID] [int] NOT NULL,
	[CampanaRRHHID] [smallint] NOT NULL,
	[Nota] [nvarchar](200) NULL,
 CONSTRAINT [PK_Plan_PoolRefuerzo] PRIMARY KEY CLUSTERED 
(
	[PoolID] ASC,
	[CampanaRRHHID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_PoolRefuerzo_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PoolRefuerzo]'))
ALTER TABLE [planificacion].[PoolRefuerzo]  WITH CHECK ADD  CONSTRAINT [FK_Plan_PoolRefuerzo_Pool] FOREIGN KEY([PoolID])
REFERENCES [planificacion].[Pool] ([PoolID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_PoolRefuerzo_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PoolRefuerzo]'))
ALTER TABLE [planificacion].[PoolRefuerzo] CHECK CONSTRAINT [FK_Plan_PoolRefuerzo_Pool]
GO
