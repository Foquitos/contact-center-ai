-- Table [dbo].[Voltara_Salesforce_casos_calidad]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara_Salesforce_casos_calidad]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara_Salesforce_casos_calidad](
	[Caso] [int] NULL,
	[Numero de suministro] [int] NULL,
	[Estado] [varchar](50) NULL,
	[Tipo de caso] [varchar](100) NULL,
	[Motivo] [varchar](255) NULL,
	[Submotivo] [varchar](255) NULL,
	[Origen del caso] [varchar](100) NULL,
	[Fecha/Hora de apertura] [datetime] NULL,
	[Fecha de la última modificación] [datetime] NULL,
	[Creado por: Nombre completo] [varchar](255) NULL,
	[Creado por: Número de empleado] [varchar](50) NULL,
	[Última modificación por: Nombre completo] [varchar](255) NULL,
	[Propietario del caso: Nombre completo] [varchar](255) NULL,
	[EmployeeNumberOwner] [varchar](50) NULL,
	[Última modificación por: Número de empleado] [varchar](50) NULL,
	[Tramo] [varchar](20) NULL
) ON [PRIMARY]
END
GO
