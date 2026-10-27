<h1>Managing roles</h1>

ldap2pg synchronizes Postgres roles in three steps:

1. Inspect Postgres for existing roles, their options and their membership.
2. Loop `sync_map` and generate wanted roles list from `role` rules.
3. Compare the two roles sets and apply to the Postgres cluster using `CREATE`,
   `DROP` and `ALTER`.

Each [role] entry in `sync_map` is a rule to generate zero or more roles with
the corresponding parameters. A `role` rule is like a template. `role` rules
allows to deduplicate membership and options by setting a list of names.

You can mix static roles and roles generated with LDAP attributes in the same
file.

[role]: config.md#sync-map-role


## Non-superuser cleanup

Before removing a managed role, ldap2pg ensures that its connecting role can
inherit the departing role and the destination database owners. `ADMIN OPTION`
allows role administration but does not provide the ownership privileges needed
by `REASSIGN OWNED` or `DROP OWNED`.

On PostgreSQL 16 and later, cleanup explicitly grants `INHERIT TRUE` and `SET TRUE`
when the required privileges are missing. Existing usable memberships are left
alone. `SET` is required on a fallback owner when database ownership changes.
Older PostgreSQL versions use ordinary membership grants.

The connecting role needs `CREATEROLE` and, on PostgreSQL 16 and later,
`ADMIN OPTION` on roles it removes. It must also be authorized to grant any
missing owner memberships. Reassigning a database additionally requires
`CREATEDB`. A native superuser needs none of these membership grants.

Destination-owner memberships persist between runs; departing-role memberships
disappear when the role is dropped. Destination owners can be blacklisted from
role management without blocking the memberships required for cleanup.
No memberships are changed in dry-run mode.

The database regression suite requires an isolated cluster and a test account
allowed to create databases and roles:

``` console
LDAP2PG_TEST_DSN='host=localhost user=postgres dbname=postgres' python -m pytest tests/postgres/
```


## Ignoring roles

ldap2pg totally ignores roles matching one of the glob pattern defined in
[roles_blacklist_query]:

``` yaml
postgres:
  # This is the default value.
  blacklist: [postgres, pg_*]
```

The role blacklist also excludes configured privilege grants and revocations.
Cleanup may still inherit a blacklisted database owner as described above.

[roles_blacklist_query]: config.md#postgres-roles-blacklist-query

ldap2pg never drop its connecting role.


## Disabling Role Management

You can tell ldap2pg to manage only privileges and never `CREATE` or `DROP` a
role. Set [roles_query] to `null` and never define a `role` rule in `sync_map`.

``` yaml
postgres:
  roles_query: null

privileges:
  ro: [__connect__, ...]

sync_map:
- grant:
    privilege: ro
    database: mydb
    role: toto
```

[roles_query]: config.md#postgres-roles-query
