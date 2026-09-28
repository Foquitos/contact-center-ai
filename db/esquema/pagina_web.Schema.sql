-- Schema [pagina_web]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'pagina_web')
EXEC sys.sp_executesql N'CREATE SCHEMA [pagina_web]'
GO
