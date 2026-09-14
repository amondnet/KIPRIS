# Security Policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub Security Advisories](https://github.com/amondnet/kipris/security/advisories/new)
rather than a public issue.

Expect an initial response within 7 days.

## Scope

This repository ships a Claude skill that calls the KIPRIS Plus Open API. The
most relevant concerns are:

- **API key handling.** The skill reads keys from environment variables, a local
  `.env`/`.env.local`, or `~/.config/kipris/`. Keys are never written to output:
  `kipris.py` redacts the key from the request URL it reports. If you find a path
  where a key leaks into stdout, logs, or the packaged artifact, please report it.
- **Response parsing.** Responses are parsed with the Python standard library
  `xml.etree.ElementTree` against a single known host. Reports of parser abuse
  reachable from a redirected or substituted endpoint are in scope.

Vulnerabilities in the KIPRIS service itself are out of scope here — report those
to [KIPRIS](https://plus.kipris.or.kr).
