-- Table [calidad].[Plataformas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[Plataformas]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[Plataformas](
	[PlataformaID] [int] IDENTITY(1,1) NOT NULL,
	[nombre] [varchar](50) NOT NULL,
	[IsActive] [bit] NOT NULL,
 CONSTRAINT [PK_Plataformas] PRIMARY KEY CLUSTERED 
(
	[PlataformaID] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
-- Index [IX_Plataformas_IsActive]
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE object_id = OBJECT_ID(N'[calidad].[Plataformas]') AND name = N'IX_Plataformas_IsActive')
CREATE NONCLUSTERED INDEX [IX_Plataformas_IsActive] ON [calidad].[Plataformas]
(
	[IsActive] ASC
)
WHERE ([IsActive]=(1))
WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[DF__Plataform__IsAct__151102AD]') AND type = 'D')
BEGIN
ALTER TABLE [calidad].[Plataformas] ADD  DEFAULT ((1)) FOR [IsActive]
END
GO
