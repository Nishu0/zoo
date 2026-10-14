# Security

## Reporting a vulnerability

Open a [private security advisory](https://github.com/chann44/zoo/security/advisories/new) on GitHub. Please don't open a public issue.

Include what you found, how to reproduce it, and what an attacker gains. A proof of concept helps but isn't required.

What to expect:

- an acknowledgement within 3 working days;
- an assessment, with a severity and a planned fix, within 10 working days;
- a fix released as fast as the severity calls for, and credit in the release notes unless you'd rather not be named.

Please give us 90 days, or until a fix ships if that's sooner, before publishing details.

## Scope

In scope:

- the API, dashboard, MCP server and SDKs in this repository;
- getting out of a sandbox: reaching the host, another sandbox, or the network against the sandbox's policy, including as root or admin inside it;
- reading or changing another user's sandboxes, secrets, profiles or audit log;
- recovering stored secrets without `ZOO_SECRETS_KEY` or the configured KMS.

Out of scope:

- the agent doing what its permissions allow (for example, running commands when `shell.exec` is allowed);
- denial of service by a user against their own sandboxes;
- weaknesses that need an attacker who already controls the host, the Docker daemon or the KMS;
- macOS admin sandboxes and the Windows guest's own firewall, which an admin inside the VM can change by design (see `macos/README.md` and `windows/README.md`).

## How sandboxes are isolated

Linux sandboxes run in Kata VMs. Network policy is enforced on the host by the egress service (`egress/`), app policy by a root service the agent can't reach, and secrets use envelope encryption. The full model is in the [security docs](site/src/content/docs/docs/concepts/security-model.md).

## Supported versions

Security fixes go into the latest release only.
