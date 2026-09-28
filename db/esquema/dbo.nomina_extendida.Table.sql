-- Table [dbo].[nomina_extendida]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[nomina_extendida]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[nomina_extendida](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[DOCUMENTO] [int] NOT NULL,
	[CODIGO LABORAL] [varchar](50) NULL,
	[ÁREA] [varchar](100) NULL,
	[SUB ÁREA] [varchar](100) NULL,
	[CATEGORÍA] [varchar](100) NULL,
	[CENTRO COSTO] [varchar](100) NULL,
	[SUB MOTIVO DE BAJA] [varchar](100) NULL,
	[EMPLEADOR] [varchar](100) NULL,
	[WAVE] [varchar](100) NULL,
	[SEXO] [varchar](20) NULL,
	[DIRECCIÓN] [varchar](255) NULL,
	[PISO DEPTO.] [varchar](20) NULL,
	[CÓDIGO POSTAL] [varchar](10) NULL,
	[TELÉFONO] [varchar](20) NULL,
	[INTERNO] [varchar](20) NULL,
	[TELÉFONO SEC.] [varchar](20) NULL,
	[EMAIL INTERNO] [varchar](255) NULL,
	[EMAIL PERSONAL] [varchar](100) NULL,
	[ESTADO CIVIL] [varchar](50) NULL,
	[HIJOS] [tinyint] NULL,
	[NACIONALIDAD] [varchar](50) NULL,
	[COMENTARIOS] [varchar](255) NULL,
	[PAÍS] [varchar](50) NULL,
	[SALARIO] [int] NULL,
	[MOTIVO BAJA] [varchar](255) NULL,
	[TAREA] [varchar](100) NULL,
	[BANCO] [varchar](100) NULL,
	[CBU] [varchar](50) NULL,
	[Convenio] [varchar](100) NULL,
	[Modalidad de Contratación] [varchar](100) NULL,
	[FECHA BAJA] [date] NULL,
	[FECHA ÚLT. DÍA] [date] NULL,
	[FECHA NACIMIENTO] [date] NULL,
 CONSTRAINT [PK_nomina_1] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
