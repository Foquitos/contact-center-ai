-- Table [dbo].[Forecast por minuto]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Forecast por minuto]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Forecast por minuto](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[fecha_hora] [datetime] NULL,
	[llamadas] [bigint] NULL,
	[Campaña] [varchar](max) NULL,
 CONSTRAINT [PK_Forecast por minuto] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
