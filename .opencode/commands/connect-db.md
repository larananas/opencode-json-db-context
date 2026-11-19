---
description: Connect to an accessible relational SQL database using the local MCP
agent: tohru-db
---

Connect to the relational database described by: $ARGUMENTS

If the MCP tools are missing, provide the OS-appropriate setup instructions first. Ask only for missing connection details. For pasted dotenv text or a .local/ env file, use db_connect_env(connect_only=true); for direct credentials use db_connect. Then optionally use db_inspect. Never export merely because this command explicitly requests a connection. Report targets and limitations. Never echo, log, or save passwords.
