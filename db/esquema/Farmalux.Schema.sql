-- Schema [Farmalux]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'Farmalux')
EXEC sys.sp_executesql N'CREATE SCHEMA [Farmalux]'
GO
