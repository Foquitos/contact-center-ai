-- Table [dbo].[ALARMIX Normalizador nombres]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[ALARMIX Normalizador nombres]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[ALARMIX Normalizador nombres](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Agent ID] [int] NULL,
	[Agent Name] [varchar](max) NULL,
	[fecha_desde] [datetime] NULL,
	[fecha_hasta] [datetime] NULL,
 CONSTRAINT [PK_ALARMIX Normalizador nombres] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[DF_ALARMIX Normalizador nombres_fecha_desde]') AND type = 'D')
BEGIN
ALTER TABLE [dbo].[ALARMIX Normalizador nombres] ADD  CONSTRAINT [DF_ALARMIX Normalizador nombres_fecha_desde]  DEFAULT (getdate()) FOR [fecha_desde]
END
GO
