-- Table [planificacion].[PuestoMalla]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[planificacion].[PuestoMalla]') AND type in (N'U'))
BEGIN
CREATE TABLE [planificacion].[PuestoMalla](
	[PuestoID] [smallint] NOT NULL,
	[EnMalla] [bit] NOT NULL,
	[Nota] [nvarchar](200) NULL,
 CONSTRAINT [PK_Plan_PuestoMalla] PRIMARY KEY CLUSTERED 
(
	[PuestoID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
