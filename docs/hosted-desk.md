# Host the complaints desk behind the firm's SSO

A platform administrator can give a complaints team one internal HTTPS address. The firm's gateway authenticates staff. Telryn checks the gateway credential, role and identity on every request. The backend listens on IPv4 loopback on the same host or within the same pod network namespace as the gateway. A lead can issue audits, attach records, review policy changes, adjudicate and download bank statements. A reviewer can open blind assignments for any audit on that desk and return primary or second grades. Set up separate instances, data directories and trust stores for teams that need separate case access.

## Start the backend

Run Telryn as a dedicated service account. Initialize its trust store once. Keep the state, registry and backups in private, persistent storage. Provision a separate random proxy credential for the gateway and the service. A 32-byte random value encoded as hex works. The service reads a mode 0400 or 0600 regular file at startup. A rotation requires reloading the gateway and restarting the backend.

```sh
export TELRYN_TRUST_DIR=/srv/telryn/trust
telryn trust init
telryn desk --out /srv/telryn/weeks --hosted-origin https://desk.bank.internal \
  --listen-port 8765 --proxy-secret-file /run/secrets/telryn-proxy --no-browser
```

The public URL has no path prefix or session token. Each forwarded request must contain exactly one copy of each header below.

| Header | Gateway value |
| --- | --- |
| Host | Authority of the configured HTTPS origin, including an explicit port if present. |
| X-Telryn-Proxy | Backend credential from the secret store. |
| X-Telryn-Subject | Stable, individual staff identifier from the authenticated session. Use an issuer-scoped opaque ID. Allowed characters are ASCII letters, digits, dot, underscore, colon, at sign, pipe and hyphen. |
| X-Telryn-Role | Exactly `lead` or `reviewer`, derived from the organization's group mapping. |
| Origin on POST | The browser's original Origin. Telryn compares it with its configured HTTPS origin. |

For a hosted grade or link confirmation the signed reviewer identifier records `sso:`, the first 48 characters of the subject, and a SHA-256 of the complete case-sensitive ASCII subject. This keeps differently cased or long IDs distinct in the review ledger. Use the same identifier for that person if the team also imports their grades through the CLI. The browser returns the authenticated identity even if a submitted CSV names someone else.

## Reverse-proxy example

This NGINX example assumes the firm already runs an authenticated identity gateway on loopback port 4180. The gateway's auth endpoint must reject expired or unauthorized sessions and return the authenticated subject and group membership in response headers. For OAuth2 Proxy, configure an approved OIDC issuer, a registered callback on the desk host, secure cookies, allowed groups and `--set-xauthrequest`. Confirm its user claim is stable and unique for the issuer. Configure login, callback and session refresh routes according to the gateway's installation guide. [OAuth2 Proxy configuration](https://oauth2-proxy.github.io/oauth2-proxy/configuration/overview/) · [NGINX auth request](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html)

```nginx
# http context. Replace the exact group names with the IdP's agreed values.
map $qa_groups $qa_role {
    default "";
    ~(^|,)complaints-leads(,|$) lead;
    ~(^|,)complaints-reviewers(,|$) reviewer;
}

# In the TLS virtual host for desk.bank.internal.
location = /_check_session {
    internal;
    proxy_pass http://127.0.0.1:4180/oauth2/auth;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header X-Original-URI $request_uri;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Host desk.bank.internal;
}
location / {
    auth_request /_check_session;
    auth_request_set $qa_subject $upstream_http_x_auth_request_user;
    auth_request_set $qa_groups $upstream_http_x_auth_request_groups;
    client_max_body_size 64m;
    proxy_set_header Host desk.bank.internal;
    proxy_set_header X-Telryn-Subject $qa_subject;
    proxy_set_header X-Telryn-Role $qa_role;
    # The confined include contains one proxy_set_header X-Telryn-Proxy directive
    # with the provisioned credential. Do not add that value to source control.
    include /run/secrets/telryn-proxy-header.conf;
    proxy_set_header Cookie "";
    proxy_set_header Authorization "";
    proxy_pass http://127.0.0.1:8765;
}
```

Forward identity values solely from the auth subrequest. Leave the browser's Origin untouched. Restrict the backend port to loopback. Apply gateway concurrency limits appropriate for sensitive uploads of up to 24 MiB per file and 64 MiB per request. Restrict access to gateway logs, configuration dumps, temporary request bodies and backups. Put TLS termination and identity-provider communication inside the firm's approved network boundary. The desk itself makes no outbound authentication request.

## Check the deployment

Sign in as a lead, preview a scored audit and share a blank packet with a reviewer. Sign in independently as the reviewer. The desk should show assignments, allow a grade return under that identity and deny access to the signed lead report, case manifest, bank export and policy controls. A reviewer cannot independently second-grade their own primary return. Check that a signed-out or unmapped user cannot open the desk. Keep the direct source-system permissions needed to read each original conversation separate from desk access.

The default local desk still uses a private session URL. Use that mode on a single operator's machine. Hosted mode trusts the designated gateway and filesystem administrators. Deploy one Telryn backend per trust store on storage supporting SQLite and file locking. [QA lead workflow](weekly-qa.md)

## Review access events

Hosted mode writes `desk-access.sqlite3` under `TELRYN_TRUST_DIR`. The owner-only database records UTC time, the verified SSO subject and role, the HTTP method, a fixed operation name and the response status. The private report, records manifest, grading packet and lead worklist record the case IDs in the response. Linked episode contacts in the private report are included. The event carries the weekly publication ID and any revision that was verified for that response. Use the publication ID to distinguish identical case IDs in different audits. Older events and admissions that precede verification can have empty publication or revision fields. A policy preview or bank aggregate can have no case IDs. A POST has an admission event with a null status and a separate response event.

The backend records access before sending the response. That shows which evidence the service authorized for delivery. It cannot establish whether someone read it. If a response cannot be recorded, the desk returns an unavailable message. Requests rejected by the identity gateway never reach the backend. Requests without verified proxy identity are tracked by the gateway. Local desk URLs remain out of the log. The database contains no raw paths, protected request bodies, transcripts or proxy secret.

Use an owner-authorized SQLite client to query the database. For example:

```sql
SELECT utc, subject, role, route, status, publication_id, revision, case_id
FROM access_events
JOIN access_cases ON access_events.id = access_cases.event_id
WHERE case_id = 'C102'
ORDER BY access_events.id;
```

Keep this database under the firm's access, backup and retention policy. Its case IDs and SSO subjects are private records. The database is a local operational log under the hosted service account. Use an independently controlled copy for review outside that trust boundary. Give auditors controlled query or export access through the firm’s existing process.
