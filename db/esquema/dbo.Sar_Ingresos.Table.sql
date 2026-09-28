-- Table [dbo].[Sar_Ingresos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Sar_Ingresos]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Sar_Ingresos](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Operador] [varchar](50) NULL,
	[Legajo] [varchar](50) NULL,
	[Atención] [varchar](50) NULL,
	[Cliente] [varchar](255) NULL,
	[Nro. ODT] [varchar](50) NULL,
	[Fecha de Ing.] [datetime] NULL,
	[Direccion] [varchar](255) NULL,
	[Distrito] [varchar](50) NULL,
	[Localidad] [varchar](50) NULL,
	[Cuenta] [varchar](50) NULL,
	[Motivo] [varchar](50) NULL,
	[Origen] [varchar](50) NULL,
	[Observacion] [varchar](4000) NULL,
	[Prioridad] [varchar](50) NULL,
	[ODT_Digitos]  AS (ltrim(rtrim(substring([Nro. ODT],charindex('-',[Nro. ODT],charindex('-',[Nro. ODT])+(1))+(1),len([Nro. ODT]))))) PERSISTED,
 CONSTRAINT [PK_Sar_Ingresos] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Sar_Ingresos]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Sar_Ingresos]') AND name = N'IX_Sar_Ingresos')
CREATE NONCLUSTERED INDEX [IX_Sar_Ingresos] ON [dbo].[Sar_Ingresos]
(
	[Fecha de Ing.] DESC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ANSI_PADDING ON
GO
-- Index [IX_SarIngresos_Legajo_FechaIng]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Sar_Ingresos]') AND name = N'IX_SarIngresos_Legajo_FechaIng')
CREATE NONCLUSTERED INDEX [IX_SarIngresos_Legajo_FechaIng] ON [dbo].[Sar_Ingresos]
(
	[Legajo] ASC,
	[Fecha de Ing.] ASC
)
INCLUDE([Nro. ODT],[Motivo],[Observacion]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
SET ARITHABORT ON
SET CONCAT_NULL_YIELDS_NULL ON
SET QUOTED_IDENTIFIER ON
SET ANSI_NULLS ON
SET ANSI_PADDING ON
SET ANSI_WARNINGS ON
SET NUMERIC_ROUNDABORT OFF
GO
-- Index [IX_SarIngresos_ODTDigitos]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[Sar_Ingresos]') AND name = N'IX_SarIngresos_ODTDigitos')
CREATE NONCLUSTERED INDEX [IX_SarIngresos_ODTDigitos] ON [dbo].[Sar_Ingresos]
(
	[ODT_Digitos] ASC
)
INCLUDE([Nro. ODT],[Motivo],[Observacion],[Legajo],[Fecha de Ing.]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
