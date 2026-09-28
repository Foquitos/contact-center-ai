-- Table [dbo].[Voltara S1 Estados de agente]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Voltara S1 Estados de agente]') AND type in (N'U'))
BEGIN
CREATE TABLE [dbo].[Voltara S1 Estados de agente](
	[id] [int] IDENTITY(1,1) NOT NULL,
	[_day] [date] NULL,
	[_user] [varchar](max) NULL,
	[_agent] [varchar](max) NULL,
	[_state_available] [time](7) NULL,
	[_state_handling] [time](7) NULL,
	[backoffice] [time](7) NULL,
	[coaching] [time](7) NULL,
	[break] [time](7) NULL,
	[_occupancy] [float] NULL,
 CONSTRAINT [PK_Voltara S1 Estados de agente] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
END
GO
