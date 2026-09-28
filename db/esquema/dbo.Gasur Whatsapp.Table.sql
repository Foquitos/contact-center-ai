-- Table [dbo].[Gasur Whatsapp]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Gasur Whatsapp]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Gasur Whatsapp](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[id_conversation] [varchar](max) NULL,
	[mdc_id] [varchar](max) NULL,
	[conv_id] [varchar](max) NULL,
	[Fecha iniciada] [datetime] NULL,
	[Fecha finalizada] [datetime] NULL,
	[Contacto] [varchar](50) NULL,
	[Atendida por Agente] [datetime] NULL,
	[Agente] [varchar](max) NULL,
	[id_orgin] [tinyint] NULL,
	[id_cola] [tinyint] NULL,
 CONSTRAINT [PK_Gasur Whatsapp] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
