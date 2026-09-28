-- Table [dbo].[Voltara Enerval Operacion]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara Enerval Operacion]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara Enerval Operacion](
	[id] [tinyint] IDENTITY(1,1) NOT NULL,
	[Operacion] [varchar](50) NOT NULL
) ON [PRIMARY]
END
GO
