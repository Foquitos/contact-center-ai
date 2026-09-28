-- Schema [calidad]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'calidad')
EXEC sys.sp_executesql N'CREATE SCHEMA [calidad]'
GO
