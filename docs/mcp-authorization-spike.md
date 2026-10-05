# MCP authorization spike

This is the browser entry for
[issue #38](https://github.com/django-trusts/django-trusts-gh-permissions/issues/38).
An MCP client can finish an OAuth authorization-code + PKCE handshake
against the runnable example. The access token is **only a test
credential for the example MCP endpoint**. It does not grant repository
access, and it is not a delegation model.

The consent page still has inert controls for:

- account or organization selection
- all repositories or only select repositories
- a requested-permissions summary taken from the OAuth scopes on the request
- Approve and Cancel

Nothing on that page is saved. The consent page and the callback
page each include a read-only "Raw handshake request" box with the
query Django received for that step. Approve redirects to the client's
callback with an authorization code. Cancel redirects with
`error=access_denied` and issues nothing. The callback page does not
exchange the code. The client posts the code to the token endpoint.

The token endpoint returns an access token that expires in one hour.
There is no refresh token. `POST /mcp` accepts that bearer token for
`initialize` and an empty `tools/list`. A missing or unknown token is
rejected.

## Package

`django-oauth-toolkit` **3.4.1**
([PyPI](https://pypi.org/project/django-oauth-toolkit/3.4.1/),
[3.4.0 MCP note](https://django-oauth-toolkit.readthedocs.io/en/3.4.1/changelog.html)).

It is a reasonable fit because it is the maintained Django OAuth 2
authorization server, and 3.4.0 added the authorization-server role the
Model Context Protocol uses: PKCE required by default, OAuth 2.0
Authorization Server Metadata (RFC 8414), and Protected Resource
Metadata (RFC 9728). The example uses that server. It does not add a
second one. The package is isolated to the runnable example
(`oauth2_provider` in `tests.settings`, `example/mcp_authorization.py`,
the seed command, and these templates). `gh_permissions` does not
import it. Replacing the package means replacing that example module
and the settings block.

Django REST framework is not installed. The toolkit's DRF authenticator
is unused.

### What the package supplies

- `AuthorizationView` validates `client_id`, redirect URI, response
  type, scope, and PKCE before a page is shown.
- `LoginRequiredMixin` sends an anonymous human to `LOGIN_URL` and
  preserves the authorize URL in `next`.
- `AllowForm` round-trips the validated request, including the RFC 8707
  resource, on the consent POST.
- `Application` stores the example public client (authorization-code
  grant, no client secret).
- Approve uses the toolkit to redirect with an authorization code.
  Cancel uses its deny path: `access_denied` and no code.
- `TokenView` exchanges that code, with PKCE, for an access token.
- RFC 8414 and RFC 9728 metadata views advertise the mounted authorize
  and token endpoints. Dynamic client registration stays off. The
  example client is the static client `example-mcp-client`.

### Mismatch

The published support matrix lists Django 4.2 through 6.0. This example
runs Django 6.1. The dependency is `django>=4.2` with no upper pin, and
3.4.1 moved one system check so a plain `manage.py check` still reports
it on Django 6.1. That is a classifier gap, not a second package.

The toolkit's consent screen is a generic scope list with Authorize.
This spike needs a connected-app layout (account or organization, all
repositories versus selected repositories, a permissions summary). The
example template supplies that layout. The controls do not write rows.

`AuthorizationView.get` will issue an authorization code without showing
the page when the application's `skip_authorization` flag is set, or
when `approval_prompt=auto` finds an existing access token. The example
view replaces `get` so the picker still appears. Approve then uses the
toolkit's normal allow path.

An authorization-code grant also stores a refresh token. This example
does not add a refresh-token or long-lived grant, so
`ExampleAccessTokenValidator` drops `refresh_token` before the toolkit
saves the bearer token. The access token expires in 3600 seconds.

The toolkit does not know about organizations, repositories, or
django-trusts grants. That mapping is later work. This spike does not
add a grant schema and does not encode a delegation rule. The example
also unregisters the toolkit's admin so those tables are not an issuing
UI.

## Start locally

From this checkout, with Django, django-trusts, and this package
installed as the README describes, install the example extra and start
the integration:

```console
python -m pip install "django-oauth-toolkit==3.4.1"
python manage.py migrate
python manage.py seed_example
python manage.py seed_mcp_authorization
python manage.py runserver
```

`runserver` must stay on port 8000. The MCP URL Cursor should use is:

```text
http://localhost:8000/mcp
```

`seed_mcp_authorization` prints that URL, one authorize URL for a
browser check, and this Cursor snippet. The access token from either
path is only a test credential for that MCP URL.

```json
{
  "mcpServers": {
    "django-trusts-example": {
      "url": "http://localhost:8000/mcp",
      "auth": {
        "CLIENT_ID": "example-mcp-client",
        "scopes": ["read_repository", "write_repository"]
      }
    }
  }
}
```

Put that in Cursor's MCP configuration (`~/.cursor/mcp.json`, or the
project `.cursor/mcp.json`). Cursor's static OAuth client id is
`example-mcp-client`. No client secret. Registered redirect URIs include
the desktop callback `http://localhost:8787/callback`, the loopback
callback `http://127.0.0.1:8787/callback`, and the web callback
`https://www.cursor.com/agents/mcp/oauth/callback`.

When Cursor opens the consent page, sign in as `example-owner` with the
development-only password `example-owner-dev-only` from the README
table. Approve returns the code Cursor exchanges. Account and
repository controls on that page do nothing.

Discovery is the toolkit's own documents:

- `http://localhost:8000/.well-known/oauth-authorization-server`
- `http://localhost:8000/.well-known/oauth-protected-resource/mcp`

`tests.settings` stores that development database in `db.sqlite3` at
the repository root (gitignored). The test runner does not use that
file.
