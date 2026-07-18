# Logs

Runtime logs are ignored by Git.

Never commit logs containing account IDs, order IDs, API responses, or credentials.

Optional session logs are rotated JSON-lines files containing local simulation
events only. The default limit is 2 MB with two backups. Keep them under this
ignored directory.
