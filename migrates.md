# migrates.md — django-trusts-gh-permissions

This file is the mechanical checklist for the GH-owned
`GhPermissionsConfig` settings cutover. It does **not** add a new
public GH API or change stored identity. Examples and Windows stay
out of scope.

Implemented revision: GH-owned owner lifecycle for
[django-trusts-gh-permissions#6](https://github.com/django-trusts/django-trusts-gh-permissions/issues/6),
now paired with final core.

## Companion kernel

| Item | Value |
| --- | --- |
| GH package | `0.1.0.dev0` (unchanged) |
| Core requirement | `django-trusts>=1.0.0.dev3,<2` |
| Paired final core | [django-trusts#116](https://github.com/django-trusts/django-trusts/pull/116) merge [`1e19b5d464c067186aada58943c3ee67c44b2aa0`](https://github.com/django-trusts/django-trusts/commit/1e19b5d464c067186aada58943c3ee67c44b2aa0) |
| Zero | **absent** (not a dependency) |

## Public changes

Stored GH schema and authorization **data** stay compatible. Public
**dependency and settings** change at IIb.

| Surface | Old (G1 on C2 + #98) | New (IIb) |
| --- | --- |
| Core pin | git `@595e2f9f` / `>=1.0.0.dev2,<2` | `django-trusts>=1.0.0.dev3,<2` (pair SHA `1e19b5d`) |
| `INSTALLED_APPS` | `'trusts'` then `'gh_permissions'` | **`'gh_permissions.apps.GhPermissionsConfig'` only** (no `'trusts'`) |
| Core AppConfig | installed (`label='trusts_core'`) | **not installed** |
| Registry owner | `kernel_config().configured_backend()` | `implementation_for_path('gh_permissions.backends.GhAuthorizationBackend')` |
| Missing kernel / path | `ready()` silently returned | `ImproperlyConfigured` / owner lifecycle fail-loud |
| Backend | `gh_permissions.backends.GhAuthorizationBackend` | **unchanged** mixin-only path |
| Models / migrations | `gh_permissions.0001_initial` | **unchanged** |
| Tables / content types | `gh_permissions_*` | **unchanged** |

### Old / new settings

```python
# Old (G1)
INSTALLED_APPS = [
    'trusts',
    'gh_permissions',
]
AUTHENTICATION_BACKENDS = [
    'gh_permissions.backends.GhAuthorizationBackend',
]

# New (IIb)
INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'gh_permissions.apps.GhPermissionsConfig',
]
AUTHENTICATION_BACKENDS = [
    'gh_permissions.backends.GhAuthorizationBackend',
]
```

```python
# Old donations
from trusts.apps import kernel_config
kernel_config().configured_backend().registry

# New donations
from trusts.apps import implementation_for_path
implementation_for_path('gh_permissions.backends.GhAuthorizationBackend')
```

### Startup / failure

| Situation | IIb behavior |
| --- | --- |
| Supported settings above | populate succeeds; `GhPermissionsConfig` is the sole `TrustsImplementationConfig` |
| `'trusts'` listed | not the supported install |
| Canonical backend path missing | `ImproperlyConfigured` from `_validate_ownership` (no silent return) |
| Core below `1.0.0.dev3` / missing helper | `ImproperlyConfigured` at import / ready |
| `kernel_config()` on final core | **absent** (`ImportError`); do not restore it |

## Unchanged identity

- App label `gh_permissions`
- Migration key `('gh_permissions', '0001_initial')`
- Tables, content types, permissions, and representative rows
- Two separately reviewable atoms: `register_direct` then `register_team`
- Independent-root OR, fail-closed, fixed query counts
- Mixin-only backend; generic `PlanQueryCompiler`; no Zero historical compiler

`makemigrations gh_permissions --check` is quiet. Already-applied GH DBs
keep matching `django_migrations` rows.

## Migration-bot checklist

Search application code and settings for:

```text
INSTALLED_APPS.*trusts
from trusts.apps import kernel_config
kernel_config(
from trusts.core_backends
from trusts.zero
django-trusts-zero
register_gh_policy
```

Then:

- [ ] Remove `'trusts'` from `INSTALLED_APPS`. Keep `'gh_permissions.apps.GhPermissionsConfig'` (or `'gh_permissions'` with `default=True`).
- [ ] Keep `'gh_permissions.backends.GhAuthorizationBackend'`. Do not list `'trusts.backends.TrustModelBackend'`.
- [ ] Replace `kernel_config()` donations with `implementation_for_path('gh_permissions.backends.GhAuthorizationBackend')`.
- [ ] Confirm `ready()` no longer returns silently when the owner/path is missing.
- [ ] Pin `django-trusts>=1.0.0.dev3,<2`. Pair CI uses merge `1e19b5d`.
- [ ] Do not add `django-trusts-zero`. Do not import `trusts.zero` or `trusts.core_backends`.
- [ ] Keep `register_direct` and `register_team` as separate functions. Do not add `register_gh_policy()`.
- [ ] `python -m django migrate --plan` — no GH operations on an already-current database.
- [ ] `python -m django makemigrations gh_permissions --check` — quiet.
- [ ] Confirm GH content types, table names, and representative rows are unchanged.
- [ ] Confirm object / list / enumeration still OR both roots and fail closed with the same query counts.
- [ ] Leave package version at `0.1.0.dev0`.
- [ ] Do not begin Windows #17 or examples.

## Out of scope

- Zero follow-up
- Windows #17
- Examples
