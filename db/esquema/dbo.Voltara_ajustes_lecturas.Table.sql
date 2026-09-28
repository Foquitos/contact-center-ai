-- Table [dbo].[Voltara_ajustes_lecturas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara_ajustes_lecturas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara_ajustes_lecturas](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Agente] [varchar](50) NULL,
	[Estado] [varchar](20) NULL,
	[DNI] [bigint] NULL,
	[Legajo] [varchar](25) NULL,
	[AgenteFecha] [varchar](60) NULL,
	[Marca temporal] [datetime] NULL
) ON [PRIMARY]
END
GO
