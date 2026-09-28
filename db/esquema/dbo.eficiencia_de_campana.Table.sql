-- Table [dbo].[eficiencia_de_campana]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[eficiencia_de_campana]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[eficiencia_de_campana](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[NombreCampania] [varchar](max) NULL,
	[Fecha] [smalldatetime] NULL,
	[EntrantesACola] [int] NULL,
	[Entrantes] [int] NULL,
	[FlowIn] [int] NULL,
	[TransferIn] [int] NULL,
	[Atendidas] [int] NULL,
	[AgNoAtendio] [int] NULL,
	[Abandonadas] [int] NULL,
	[FlowOut] [int] NULL,
	[NoDerivadas] [int] NULL,
	[ASA] [int] NULL,
	[AAT] [int] NULL,
	[AHT] [int] NULL,
	[ATT] [int] NULL,
	[idCampania] [int] NULL,
	[TiempoUmbral] [int] NULL,
	[AtAntesUmbral] [int] NULL,
	[AtDespuesUmbral] [int] NULL,
	[AbAntesUmbral] [int] NULL,
	[AbDespuesUmbral] [int] NULL,
	[fecha_inicio]  AS (CONVERT([date],[fecha])) PERSISTED,
 CONSTRAINT [PK_eficiencia_de_campana] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
