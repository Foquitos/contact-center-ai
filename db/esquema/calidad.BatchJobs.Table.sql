-- Table [calidad].[BatchJobs]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[BatchJobs]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[BatchJobs](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[name] [varchar](50) NOT NULL,
	[status] [varchar](50) NOT NULL,
	[created_at] [datetime] NOT NULL,
	[procesado_at] [datetime] NULL,
	[intentos_proceso] [int] NOT NULL,
	[Entorno] [nvarchar](20) NOT NULL,
 CONSTRAINT [PK_BatchJobs] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_BatchJobs_intentos_proceso]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[BatchJobs] ADD  CONSTRAINT [DF_BatchJobs_intentos_proceso]  DEFAULT ((0)) FOR [intentos_proceso]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF_BatchJobs_Entorno]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[BatchJobs] ADD  CONSTRAINT [DF_BatchJobs_Entorno]  DEFAULT ('prod') FOR [Entorno]
END
GO
