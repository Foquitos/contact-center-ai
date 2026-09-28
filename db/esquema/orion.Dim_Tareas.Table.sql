-- Table [orion].[Dim_Tareas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[orion].[Dim_Tareas]') AND type in (N'U'))
BEGIN
CREATE TABLE [orion].[Dim_Tareas](
	[id_acme] [int] IDENTITY(1,1) NOT NULL,
	[ID] [int] NULL,
	[Nombre] [varchar](50) NULL,
	[Creada] [datetime] NULL,
	[Modificada] [datetime] NULL,
	[Activa] [bit] NULL,
	[Clientes_ID] [smallint] NULL,
	[TipoTarea_ID] [smallint] NULL,
	[ACDQueue] [smallint] NULL,
 CONSTRAINT [PK_Dim_Tareas] PRIMARY KEY CLUSTERED 
(
	[id_acme] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
