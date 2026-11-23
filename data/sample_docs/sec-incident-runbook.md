<!-- acl: eng,security -->
# Security Incident Runbook

## Severity levels
SEV1 incidents require an incident commander within 15 minutes and an executive update every 60 minutes.
SEV2 incidents require acknowledgement within 30 minutes.

## Credential leaks
If an API key is leaked, revoke it in the secrets manager first, then rotate dependent services, then open a SEV2.
