-- Table [planificacion].[CodigoPayroll]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[CodigoPayroll]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[CodigoPayroll](
	[Codigo] [nvarchar](30) NOT NULL,
	[Clase] [nvarchar](20) NOT NULL,
	[Descripcion] [nvarchar](200) NULL,
 CONSTRAINT [PK_Plan_CodigoPayroll] PRIMARY KEY CLUSTERED 
(
	[Codigo] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CodigoPayroll_Clase]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CodigoPayroll]'))
ALTER TABLE [planificacion].[CodigoPayroll]  WITH CHECK ADD  CONSTRAINT [CK_Plan_CodigoPayroll_Clase] CHECK  (([Clase]='otro' OR [Clase]='licencia' OR [Clase]='capacitacion' OR [Clase]='ausente' OR [Clase]='piso'))
GO
IF  EXISTS (SELECT * FROM sys.check_constraints WHERE object_id = OBJECT_ID(N'[planificacion].[CK_Plan_CodigoPayroll_Clase]') AND parent_object_id = OBJECT_ID(N'[planificacion].[CodigoPayroll]'))
ALTER TABLE [planificacion].[CodigoPayroll] CHECK CONSTRAINT [CK_Plan_CodigoPayroll_Clase]
GO
