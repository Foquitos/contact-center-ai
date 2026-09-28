-- Table [calidad].[Empresa_nombreMitrol]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[Empresa_nombreMitrol]') AND type in (N'U'))
BEGIN
CREATE TABLE [calidad].[Empresa_nombreMitrol](
	[EmpresaID] [int] NOT NULL,
	[nombre_mitrol] [varchar](50) NOT NULL,
 CONSTRAINT [PK_Empresa_nombreMitrol] PRIMARY KEY CLUSTERED 
(
	[EmpresaID] ASC,
	[nombre_mitrol] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
END
GO
IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Empresa_nombreMitrol_Empresas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Empresa_nombreMitrol]'))
ALTER TABLE [calidad].[Empresa_nombreMitrol]  WITH CHECK ADD  CONSTRAINT [FK_Empresa_nombreMitrol_Empresas] FOREIGN KEY([EmpresaID])
REFERENCES [calidad].[Empresas] ([EmpresaID])
GO
IF  EXISTS (SELECT * FROM sys.foreign_keys WHERE object_id = OBJECT_ID(N'[calidad].[FK_Empresa_nombreMitrol_Empresas]') AND parent_object_id = OBJECT_ID(N'[calidad].[Empresa_nombreMitrol]'))
ALTER TABLE [calidad].[Empresa_nombreMitrol] CHECK CONSTRAINT [FK_Empresa_nombreMitrol_Empresas]
GO
