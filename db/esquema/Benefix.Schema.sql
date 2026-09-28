-- Schema [Benefix]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'Benefix')
EXEC sys.sp_executesql N'CREATE SCHEMA [Benefix]'
GO
