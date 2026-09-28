-- Table [dbo].[Vitalis Auxiliares]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Vitalis Auxiliares]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Vitalis Auxiliares](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[Usuario] [varchar](max) NULL,
	[Fecha] [date] NULL,
	[Conectado] [int] NULL,
	[Disponible] [int] NULL,
	[En Llamada] [int] NULL,
	[On Hold] [smallint] NULL,
	[After Call Work] [smallint] NULL,
	[Invisible] [int] NULL,
	[Back Office] [smallint] NULL,
	[Baño] [int] NULL,
	[Break] [smallint] NULL,
	[Confirmación de agendas] [smallint] NULL,
	[Feedback] [smallint] NULL,
	[Gestión WhatsApp ] [smallint] NULL,
	[Problemas IT] [smallint] NULL,
	[Tareas administrativas] [smallint] NULL,
	[Ticketera interna] [smallint] NULL,
	[WHATSAPP ] [int] NULL,
 CONSTRAINT [PK_Vitalis Auxiliares] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
