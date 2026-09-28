-- Schema [chatbot]
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = N'chatbot')
EXEC sys.sp_executesql N'CREATE SCHEMA [chatbot]'
GO
