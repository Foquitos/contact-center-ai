-- Table [dbo].[login_agentes_por_campanas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[login_agentes_por_campanas]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[login_agentes_por_campanas](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NOT NULL,
	[LoginId] [varchar](50) NOT NULL,
	[LogIn] [smalldatetime] NOT NULL,
	[LogOut] [smalldatetime] NULL,
	[idAgente] [int] NULL,
	[idGrupo] [smallint] NULL,
	[id Campaña] [smallint] NOT NULL,
	[Tiempo] [int] NOT NULL,
	[Logueado] [bit] NULL,
 CONSTRAINT [PK_login_agentes_por_campanas] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
