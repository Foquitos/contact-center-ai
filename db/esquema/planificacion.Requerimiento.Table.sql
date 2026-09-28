-- Table [planificacion].[Requerimiento]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[Requerimiento]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[Requerimiento](
	[CorridaID] [bigint] NOT NULL,
	[PoolID] [int] NOT NULL,
	[Intervalo] [datetime2](0) NOT NULL,
	[Llamadas] [decimal](10, 2) NOT NULL,
	[TmoSeg] [decimal](8, 2) NOT NULL,
	[Trafico] [decimal](10, 3) NOT NULL,
	[OperadoresLinea] [smallint] NOT NULL,
	[OperadoresPlanificar] [smallint] NOT NULL,
	[NdsContractual] [decimal](5, 4) NULL,
	[NdsAtendidas] [decimal](5, 4) NULL,
	[NdsEntrantes] [decimal](5, 4) NULL,
	[Abandono] [decimal](5, 4) NULL,
	[Ocupacion] [decimal](5, 4) NULL,
	[Motivo] [nvarchar](40) NULL,
	[OperadoresPlanificados] [smallint] NULL,
	[Brecha] [smallint] NULL,
	[AsaSeg] [decimal](8, 2) NULL,
	[NivelAtencionB] [decimal](5, 4) NULL,
	[RefuerzoDisponible] [smallint] NULL,
	[RefuerzoCubre] [smallint] NULL,
	[CitadosEquivalentes] [decimal](7, 2) NULL,
 CONSTRAINT [PK_Plan_Requerimiento] PRIMARY KEY CLUSTERED 
(
	[CorridaID] ASC,
	[PoolID] ASC,
	[Intervalo] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Req_Corrida]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Requerimiento]'))
ALTER TABLE [planificacion].[Requerimiento]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Req_Corrida] FOREIGN KEY([CorridaID])
REFERENCES [planificacion].[Corrida] ([CorridaID])
ON DELETE CASCADE
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Req_Corrida]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Requerimiento]'))
ALTER TABLE [planificacion].[Requerimiento] CHECK CONSTRAINT [FK_Plan_Req_Corrida]
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Req_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Requerimiento]'))
ALTER TABLE [planificacion].[Requerimiento]  WITH CHECK ADD  CONSTRAINT [FK_Plan_Req_Pool] FOREIGN KEY([PoolID])
REFERENCES [planificacion].[Pool] ([PoolID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[planificacion].[FK_Plan_Req_Pool]') AND parent_object_id = OBJECT_ID(N'[planificacion].[Requerimiento]'))
ALTER TABLE [planificacion].[Requerimiento] CHECK CONSTRAINT [FK_Plan_Req_Pool]
GO
