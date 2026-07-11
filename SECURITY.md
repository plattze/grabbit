# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/plattze/grabbit/security/advisories/new).
You should get a response within a week. Please do not open public issues for
security problems.

## Scope

Grabbit is designed to run behind a reverse proxy on a private network. Reports
are especially welcome for:

- Authentication or scope bypasses on `/api/*`
- SSRF: getting the engine to fetch private/link-local/metadata addresses
- Secret leakage (API keys, cookies, signed URLs) into logs or responses
- Container escape / privilege issues in the shipped image

## Hardening checklist for deployments

- Keep auth on (`security.require_auth: true`, the default).
- Publish the container only to your proxy or localhost, never `0.0.0.0` on a
  public interface.
- Give the Chrome extension and agents `submit`-scoped keys, not `admin`.
- Mount cookies read-only; treat them like passwords.
