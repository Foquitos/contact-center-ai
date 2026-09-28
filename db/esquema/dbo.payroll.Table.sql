-- Table [dbo].[payroll]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[payroll]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[payroll](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[id_operadores] [int] NULL,
	[fecha] [date] NULL,
	[inicio] [smalldatetime] NULL,
	[final] [smalldatetime] NULL,
	[codigo] [varchar](20) NULL,
	[conexion] [datetime] NULL,
	[desconexion] [datetime] NULL,
	[horas_programadas] [float] NULL,
	[horas_adherencia] [float] NULL,
	[horas_adicionales] [float] NULL,
	[horas_extras] [float] NULL,
	[horas_trabajadas] [float] NULL,
	[horas_programadas_sin_extras] [float] NULL,
	[actividad] [varchar](50) NULL,
	[horas_trabajadas_sin_codigos_presenciales] [float] NULL,
	[horas_nocturnas] [float] NULL,
 CONSTRAINT [PK__payroll__3213E83F0EED424B] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_payroll_fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[payroll]') AND name = N'IX_payroll_fecha')
CREATE NONCLUSTERED INDEX [IX_payroll_fecha] ON [dbo].[payroll]
(
	[fecha] DESC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_payroll_fecha_codigo]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[payroll]') AND name = N'IX_payroll_fecha_codigo')
CREATE NONCLUSTERED INDEX [IX_payroll_fecha_codigo] ON [dbo].[payroll]
(
	[fecha] ASC
)
INCLUDE([id_operadores],[codigo],[conexion],[desconexion],[horas_programadas],[horas_extras],[horas_trabajadas],[horas_programadas_sin_extras]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
-- Index [IX_payroll_id_operador]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[dbo].[payroll]') AND name = N'IX_payroll_id_operador')
CREATE NONCLUSTERED INDEX [IX_payroll_id_operador] ON [dbo].[payroll]
(
	[id_operadores] DESC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[dbo].[FK_payroll_operadores]') AND parent_object_id = OBJECT_ID(N'[dbo].[payroll]'))
ALTER TABLE [dbo].[payroll]  WITH CHECK ADD  CONSTRAINT [FK_payroll_operadores] FOREIGN KEY([id_operadores])
REFERENCES [dbo].[operadores] ([id])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[dbo].[FK_payroll_operadores]') AND parent_object_id = OBJECT_ID(N'[dbo].[payroll]'))
ALTER TABLE [dbo].[payroll] CHECK CONSTRAINT [FK_payroll_operadores]
GO
