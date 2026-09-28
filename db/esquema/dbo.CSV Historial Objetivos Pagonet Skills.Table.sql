-- Table [dbo].[CSV Historial Objetivos Pagonet Skills]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[CSV Historial Objetivos Pagonet Skills]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[CSV Historial Objetivos Pagonet Skills](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Skill_ID] [int] NULL,
	[Objetivo_Pagonet] [smallint] NULL,
	[fecha_desde] [date] NULL,
	[fecha_hasta] [date] NULL,
 CONSTRAINT [PK_CSV Historial Objetivos Pagonet Skills] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF_CSV Historial Objetivos Pagonet Skills_fecha_desde]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[CSV Historial Objetivos Pagonet Skills] ADD  CONSTRAINT [DF_CSV Historial Objetivos Pagonet Skills_fecha_desde]  DEFAULT (getdate()) FOR [fecha_desde]
END
GO
