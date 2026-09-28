-- Table [planificacion].[PoolOrigen]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[PoolOrigen]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[PoolOrigen](
	[PoolID] [int] NOT NULL,
	[CampanaRRHHID] [smallint] NOT NULL,
	[Nota] [nvarchar](200) NULL,
 CONSTRAINT [PK_Plan_PoolOrigen] PRIMARY KEY CLUSTERED 
(
	[PoolID] ASC,
	[CampanaRRHHID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_PoolOrigen_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PoolOrigen]'))
ALTER TABLE [planificacion].[PoolOrigen]  WITH CHECK ADD  CONSTRAINT [FK_Plan_PoolOrigen_Pool] FOREIGN KEY([PoolID])
REFERENCES [planificacion].[Pool] ([PoolID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_PoolOrigen_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[PoolOrigen]'))
ALTER TABLE [planificacion].[PoolOrigen] CHECK CONSTRAINT [FK_Plan_PoolOrigen_Pool]
GO
