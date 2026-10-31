# BluePaper Security Policy

BluePaper is Bluethroat Labs' network service for converting untrusted documents into PDFs rebuilt from pixels. This policy covers the BluePaper service, its API and browser console, the Bluethroat CLI integration, and BluePaper-specific code and deployment configuration in this repository. The bundled Dangerzone desktop application has its own [security policy](https://github.com/freedomofpress/dangerzone/security/policy).

## Report a vulnerability privately

Email **[saxenism@bluethroatlabs.com](mailto:saxenism@bluethroatlabs.com)** with the subject `BluePaper security report`. Do not post vulnerability details or proof-of-concept code in a public issue, discussion, or pull request. We will acknowledge the report and coordinate investigation, remediation, and disclosure with you.

Please include the affected URL, branch, commit or release; the security impact; concise reproduction steps; and any non-sensitive logs or proof of concept. Tell us whether you tested the deployed service or only local source. Remove credentials, personal documents, and other private data from attachments.

## Testing boundaries

Use your own accounts and disposable documents. Local reproductions and low-impact checks against the service are welcome. **Do not deliberately exhaust the shared queue, workers, storage, third-party API quotas, or other users' availability.** Coordinate with us before load testing, testing denial of service, or running a proof of concept that may affect other users. Do not access or retain another user's data.

## Supported versions

BluePaper does not publish versioned releases yet. Report issues in the currently deployed service, the active development branches, or the default branch, and include the exact revision when possible. A source-only issue may or may not affect the deployed service; we will check both during triage.
