-- Table [pagina_web].[Informador_csv_logs]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[Informador_csv_logs]') AND type in (N'U'))
BEGIN
CREATE TABLE [pagina_web].[Informador_csv_logs](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Documento] [int] NOT NULL,
	[seccion_1] [varchar](max) NULL,
	[seccion_2] [varchar](max) NULL,
	[seccion_3] [varchar](max) NULL,
	[seccion_4] [varchar](max) NULL,
	[timestamp] [datetime] NOT NULL,
 CONSTRAINT [PK_Infromador_csv_logs] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[pagina_web].[DF_Informador_csv_logs_timestamp]') AND type = 'D')
BEGIN
ALTER TABLE [pagina_web].[Informador_csv_logs] ADD  CONSTRAINT [DF_Informador_csv_logs_timestamp]  DEFAULT (getdate()) FOR [timestamp]
END
GO
