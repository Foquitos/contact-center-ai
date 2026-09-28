-- Table [pagina_web].[IA_Presupuesto_Alertas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[IA_Presupuesto_Alertas]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[IA_Presupuesto_Alertas](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[anio_mes] [char](7) NOT NULL,
	[umbral] [int] NOT NULL,
	[gasto_usd] [decimal](18, 6) NOT NULL,
	[pct] [decimal](6, 2) NOT NULL,
	[enviado_at] [datetime2](7) NOT NULL,
 CONSTRAINT [PK_IA_Presupuesto_Alertas] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UX_IA_Presupuesto_Alertas_mes_umbral] UNIQUE NONCLUSTERED 
(
	[anio_mes] ASC,
	[umbral] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_IA_Presupuesto_Alertas_enviado]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[IA_Presupuesto_Alertas] ADD  CONSTRAINT [DF_IA_Presupuesto_Alertas_enviado]  DEFAULT (sysutcdatetime()) FOR [enviado_at]
END
GO
