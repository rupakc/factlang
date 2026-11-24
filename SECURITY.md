# Security Policy

## Supported versions

Only the latest released minor version receives security fixes.

## Reporting a vulnerability

Please do not open a public issue. Report privately through GitHub's
"Report a vulnerability" button on the repository's Security tab
(https://github.com/rupakc/factlang/security/advisories/new).

Include a description, reproduction steps and the affected version. You can expect
an acknowledgement within a few days and a fix or mitigation plan after triage.

## Scope

FactLang generates read-only SQL from claims. Anything that lets a claim inject SQL,
bypass bind parameters, write to a warehouse or leak credentials is in scope.
