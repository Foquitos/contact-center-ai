-- Schema [Hidra]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'Hidra')
EXEC sys.sp_executesql N'CREATE SCHEMA [Hidra]'
GO
