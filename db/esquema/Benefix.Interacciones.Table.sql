-- Table [Benefix].[Interacciones]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[Benefix].[Interacciones]') AND type in (N'U'))
BEGIN
CREATE TABLE [Benefix].[Interacciones](
	[ConversationId] [varchar](36) NOT NULL,
	[ParticipantId] [varchar](36) NOT NULL,
	[Fecha] [date] NOT NULL,
	[InicioConversacion] [datetime2](0) NOT NULL,
	[FinConversacion] [datetime2](0) NULL,
	[InicioAgente] [datetime2](0) NULL,
	[FinAgente] [datetime2](0) NULL,
	[Canal] [varchar](20) NOT NULL,
	[Sentido] [varchar](10) NULL,
	[UserId] [varchar](36) NULL,
	[Operador] [nvarchar](200) NULL,
	[Email] [nvarchar](200) NULL,
	[QueueId] [varchar](36) NULL,
	[Cola] [nvarchar](200) NULL,
	[Skills] [nvarchar](1000) NULL,
	[WrapUpCodeId] [varchar](36) NULL,
	[Tipificacion] [nvarchar](300) NULL,
	[NotaWrapUp] [nvarchar](4000) NULL,
	[TelefonoCliente] [varchar](64) NULL,
	[ANI] [varchar](200) NULL,
	[DNIS] [varchar](200) NULL,
	[SegundosAlerta] [int] NULL,
	[SegundosHablados] [int] NULL,
	[SegundosEspera] [int] NULL,
	[CantidadEsperas] [int] NULL,
	[SegundosACW] [int] NULL,
	[SegundosManejo] [int] NULL,
	[Transferido] [bit] NOT NULL,
	[DesconexionAgente] [varchar](30) NULL,
	[Grabada] [bit] NOT NULL,
	[AgentesEnConversacion] [tinyint] NOT NULL,
	[CargadoEn] [datetime2](0) NOT NULL,
	[ExternalTag] [varchar](64) NULL,
 CONSTRAINT [PK_BenefixInteracciones] PRIMARY KEY CLUSTERED 
(
	[ConversationId] ASC,
	[ParticipantId] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
SET ANSI_PADDING ON
GO
-- Index [IX_BenefixInteracciones_Fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[Benefix].[Interacciones]') AND name = N'IX_BenefixInteracciones_Fecha')
CREATE NONCLUSTERED INDEX [IX_BenefixInteracciones_Fecha] ON [Benefix].[Interacciones]
(
	[Fecha] ASC,
	[Canal] ASC
)
INCLUDE([InicioConversacion],[Operador],[Cola],[Tipificacion],[Sentido],[SegundosHablados],[Grabada]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[Benefix].[DF_BenefixInteracciones_Transferido]') AND type = 'D')
BEGIN
ALTER TABLE [Benefix].[Interacciones] ADD  CONSTRAINT [DF_BenefixInteracciones_Transferido]  DEFAULT ((0)) FOR [Transferido]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[Benefix].[DF_BenefixInteracciones_CargadoEn]') AND type = 'D')
BEGIN
ALTER TABLE [Benefix].[Interacciones] ADD  CONSTRAINT [DF_BenefixInteracciones_CargadoEn]  DEFAULT (sysdatetime()) FOR [CargadoEn]
END
GO
