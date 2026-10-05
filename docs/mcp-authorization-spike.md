# MCP authorization spike

This is the browser entry for
[issue #38](https://github.com/django-trusts/django-trusts-gh-permissions/issues/38).
An MCP authorization request goes through normal Django login and stops
on a blank connected-app page. It is not a delegation model. Approve
does not save a selection and does not issue a credential.

The page has inert controls for:

- account or organization selection
- all repositories or only select repositories
- a requested-permissions summary taken from the OAuth scopes on the request
- Approve and Cancel

Cancel redirects to the client's callback with `error=access_denied`.
That callback does not exchange an authorization code. Approve
re-renders the same page and records that no delegated authority was
issued.

## Package

`django-oauth-toolkit` **3.4.1**
([PyPI](https://pypi.org/project/django-oauth-toolkit/3.4.1/),
[3.4.0 MCP note](https://django-oauth-toolkit.readthedocs.io/en/3.4.1/changelog.html)).

It is a reasonable fit because it is the maintained Django OAuth 2
authorization server, and 3.4.0 added the authorization-server role the
Model Context Protocol uses: PKCE required by default, OAuth 2.0
Authorization Server Metadata (RFC 8414), and Protected Resource
Metadata (RFC 9728). The example only needs the browser half of that
role. The package is isolated to the runnable example
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
- `AllowForm` round-trips the validated request on the consent POST.
- `Application` stores the example public client (authorization-code
  grant, no client secret).
- Cancel uses the toolkit's deny path: redirect with `access_denied`
  and no authorization code.
- Discovery, dynamic client registration, and the token endpoint exist
  in the package and are not mounted here.

### Mismatch

The published support matrix lists Django 4.2 through 6.0. This example
runs Django 6.1. The dependency is `django>=4.2` with no upper pin, and
3.4.1 moved one system check so a plain `manage.py check` still reports
it on Django 6.1. That is a classifier gap, not a second package.

The toolkit's consent screen is a generic scope list with Authorize.
This spike needs a connected-app layout (account or organization, all
repositories versus selected repositories, a permissions summary). The
example template supplies that layout. The controls do not write rows.

`AuthorizationView.get` will issue an authorization code when the
application's `skip_authorization` flag is set, or when
`approval_prompt=auto` finds an existing access token. `form_valid`
issues a code on approve. A code can be exchanged at the token
endpoint. This slice must not do that, so `example/mcp_authorization.py`
replaces `get` and the approve branch, and refuses `allow=True` inside
`create_authorization_response`. The token endpoint is not mounted.
The example also unregisters the toolkit's admin so those tables are
not an issuing UI.

The toolkit does not know about organizations, repositories, or
django-trusts grants. That mapping is later work. This spike does not
add a grant schema and does not encode a delegation rule.

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

`seed_mcp_authorization` prints one authorize URL on `localhost` port
8000. Open that URL. With no session, the next page is Django's login
form (`/accounts/login/`). Sign in as `example-owner` with the
development-only password `example-owner-dev-only` from the README
table. The following page is the blank picker.

`runserver` must stay on port 8000. The registered redirect URI is
`http://localhost:8000/mcp/callback/`.

`tests.settings` stores that development database in `db.sqlite3` at
the repository root (gitignored). The test runner does not use that
file.
