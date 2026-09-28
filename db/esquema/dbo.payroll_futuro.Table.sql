-- Table [dbo].[payroll_futuro]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[payroll_futuro]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[payroll_futuro](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[id_operadores] [int] NULL,
	[fecha] [date] NULL,
	[inicio] [time](7) NULL,
	[final] [time](7) NULL,
	[codigo] [varchar](20) NULL,
	[conexion] [time](7) NULL,
	[desconexion] [time](7) NULL,
	[horas_programadas] [float] NULL,
	[horas_adherencia] [float] NULL,
	[horas_adicionales] [float] NULL,
	[horas_extras] [float] NULL,
	[horas_programadas_sin_extras] [float] NULL,
	[actividad] [varchar](50) NULL,
	[horas_trabajadas_sin_codigos_presenciales] [float] NULL,
	[horas_nocturnas] [float] NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
