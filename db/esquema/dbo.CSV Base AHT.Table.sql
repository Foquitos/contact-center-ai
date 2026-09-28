-- Table [dbo].[CSV Base AHT]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV Base AHT]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV Base AHT](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Fecha] [date] NULL,
	[Agente] [varchar](max) NULL,
	[Agente_ID] [int] NULL,
	[Extn] [int] NULL,
	[Skill_ID] [smallint] NULL,
	[ACD] [smallint] NULL,
	[Re-direc] [smallint] NULL,
	[Hold] [smallint] NULL,
	[Transf] [smallint] NULL,
	[Conf] [smallint] NULL,
	[S_Ext] [smallint] NULL,
	[S_Externas] [smallint] NULL,
	[Total_ACD] [int] NULL,
	[Total_ACW] [smallint] NULL,
	[Ring] [smallint] NULL,
	[Other] [int] NULL,
	[Total_AUX] [int] NULL,
	[Total_AUX_OUT] [smallint] NULL,
	[Total_AUX_IN] [smallint] NULL,
	[Total_ACW_IN] [smallint] NULL,
	[Total_ACW_OUT] [smallint] NULL,
	[Total_Hold] [int] NULL,
	[Avail] [int] NULL,
	[Staff] [int] NULL,
	[Equipo] [varchar](max) NULL,
	[Objetivo_Pagonet] [smallint] NULL,
	[Objetivo_Acme] [smallint] NULL,
	[Fecha_ultima_capa] [date] NULL,
	[AHT_Total] [int] NULL,
	[Es_Upgrade] [bit] NULL,
	[PCRC_ID] [int] NULL,
	[Expertise_ID] [int] NULL,
	[horario_id] [tinyint] NULL,
 CONSTRAINT [PK_CSV Base AHT] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
