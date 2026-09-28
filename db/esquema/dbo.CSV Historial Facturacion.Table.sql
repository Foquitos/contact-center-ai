-- Table [dbo].[CSV Historial Facturacion]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV Historial Facturacion]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV Historial Facturacion](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[PCRC ID] [int] NULL,
	[Skill ID] [int] NULL,
	[Objetivo] [int] NULL,
	[Fecha desde] [date] NULL,
	[Fecha hasta] [date] NULL
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF_CSV Historial Facturacion_Fecha desde]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[CSV Historial Facturacion] ADD  CONSTRAINT [DF_CSV Historial Facturacion_Fecha desde]  DEFAULT (datefromparts(datepart(year,getdate()),datepart(month,getdate()),(1))) FOR [Fecha desde]
END
GO
