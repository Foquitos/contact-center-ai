-- Table [orion].[Silver_Agente_Skill_Logins]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[Silver_Agente_Skill_Logins]') AND type in (N'U'))
BEGIN
CREATE TABLE [orion].[Silver_Agente_Skill_Logins](
	[Legajo_Orion] [varchar](50) NOT NULL,
	[Fecha] [date] NOT NULL,
	[Skill_ID] [int] NOT NULL,
	[Skill_Nombre] [varchar](150) NULL,
	[Campana] [varchar](100) NULL,
	[Total_Segundos] [int] NOT NULL,
	[Tiempo_Login_Horas] [decimal](12, 4) NOT NULL,
	[Fecha_Carga] [datetime] NULL,
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Agente_Nombre] [varchar](50) NULL,
 CONSTRAINT [PK_Silver_Agente_Skill_Logins] PRIMARY KEY CLUSTERED 
(
	[Legajo_Orion] ASC,
	[Fecha] ASC,
	[Skill_ID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Silver_Agente_Skill_Logins_Fecha]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[orion].[Silver_Agente_Skill_Logins]') AND name = N'IX_Silver_Agente_Skill_Logins_Fecha')
CREATE NONCLUSTERED INDEX [IX_Silver_Agente_Skill_Logins_Fecha] ON [orion].[Silver_Agente_Skill_Logins]
(
	[Fecha] ASC
)
INCLUDE([Campana],[Tiempo_Login_Horas]) WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[DF__Silver_Ag__Fecha__7A47FDD8]') AND type = 'D')
BEGIN
ALTER TABLE [orion].[Silver_Agente_Skill_Logins] ADD  DEFAULT (getdate()) FOR [Fecha_Carga]
END
GO
