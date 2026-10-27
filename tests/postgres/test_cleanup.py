import os
import subprocess
import uuid
from contextlib import contextmanager

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn, parse_dsn
import pytest
import yaml


@contextmanager
def connection(dsn):
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def cluster():
    dsn = os.environ.get('LDAP2PG_TEST_DSN')
    if not dsn:
        pytest.skip('LDAP2PG_TEST_DSN must point to an isolated test cluster')
    prefix = 'cleanup_' + uuid.uuid4().hex[:12]
    roles = dict(
        sync=prefix + '_sync',
        stale=prefix + '_stale',
        later=prefix + '_later',
        keep=prefix + '_keep',
        unmanaged=prefix + '_unmanaged',
        owner0=prefix + '_owner0',
        owner1=prefix + '_owner1',
    )
    databases = [prefix + '_db0', prefix + '_db1']
    params = parse_dsn(dsn)
    with connection(dsn) as admin:
        try:
            yield admin, params, roles, databases
        finally:
            with admin.cursor() as cur:
                for database in databases:
                    cur.execute(sql.SQL('DROP DATABASE IF EXISTS {}').format(
                        sql.Identifier(database)))
                for role in roles.values():
                    cur.execute('SELECT 1 FROM pg_roles WHERE rolname = %s',
                                (role,))
                    if cur.fetchone():
                        cur.execute(sql.SQL('DROP OWNED BY {}').format(
                            sql.Identifier(role)))
                cur.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(
                    sql.SQL(', ').join(map(sql.Identifier, roles.values()))))


@pytest.mark.parametrize('inherited,owns_database', [
    (False, False),
    (True, False),
    (False, True),
])
def test_cleanup_preserves_data_and_handles_future_departures(
        cluster, tmp_path, inherited, owns_database):
    admin, params, roles, databases = cluster
    version = admin.server_version
    with admin.cursor() as cur:
        cur.execute(sql.SQL('CREATE ROLE {} LOGIN CREATEROLE {}').format(
            sql.Identifier(roles['sync']),
            sql.SQL('CREATEDB' if owns_database else 'NOCREATEDB')))
        for key in ('stale', 'keep', 'unmanaged', 'owner0', 'owner1'):
            cur.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(
                sql.Identifier(roles[key])))
        cur.execute(sql.SQL('COMMENT ON ROLE {} IS %s').format(
            sql.Identifier(roles['keep'])), ('fixture',))
        options = ' WITH ADMIN OPTION'
        if version >= 160000:
            options = (' WITH ADMIN TRUE, INHERIT %s, SET FALSE' %
                       ('TRUE' if inherited else 'FALSE'))
        for key in ('stale', 'keep', 'owner0', 'owner1'):
            grant_options = options
            if version >= 160000 and key.startswith('owner') and inherited:
                grant_options = ' WITH ADMIN FALSE, INHERIT TRUE, SET FALSE'
            cur.execute(sql.SQL('GRANT {} TO {}' + grant_options).format(
                sql.Identifier(roles[key]), sql.Identifier(roles['sync'])))
        for i, database in enumerate(databases):
            owner = roles['owner%d' % i]
            if owns_database and i == 0:
                owner = roles['stale']
            cur.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(
                sql.Identifier(database), sql.Identifier(owner)))

    for i, database in enumerate(databases):
        with connection(make_dsn(**dict(params, dbname=database))) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'CREATE TABLE retained_data (id integer PRIMARY KEY)')
                cur.execute('INSERT INTO retained_data VALUES (%s)', (40 + i,))
                cur.execute(sql.SQL(
                    'ALTER TABLE retained_data OWNER TO {}').format(
                        sql.Identifier(roles['stale'])))

    config = dict(
        version=5,
        postgres=dict(
            dsn=make_dsn(**dict(
                params, user=roles['sync'], dbname=databases[0])),
            databases_query=databases,
            managed_roles_query=[roles['stale'], roles['keep']],
            fallback_owner=roles['owner0'],
        ),
        privileges={},
        sync_map=[dict(role=dict(
            name=roles['keep'], options='NOLOGIN', comment='fixture'))],
    )
    path = tmp_path / 'ldap2pg.yml'

    def synchronize(real=True):
        path.write_text(yaml.safe_dump(config))
        command = ['ldap2pg', '-c', str(path)]
        if real:
            command.append('-N')
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        assert result.returncode == 0, result.stderr

    with admin.cursor() as cur:
        cur.execute("SELECT pg_has_role(%s, %s, 'USAGE')",
                    (roles['sync'], roles['owner0']))
        owner_privileges = cur.fetchone()
    synchronize(real=False)
    with admin.cursor() as cur:
        cur.execute("SELECT pg_has_role(%s, %s, 'USAGE')",
                    (roles['sync'], roles['owner0']))
        assert cur.fetchone() == owner_privileges
        cur.execute('SELECT 1 FROM pg_roles WHERE rolname = %s',
                    (roles['stale'],))
        assert cur.fetchone() == (1,)

    synchronize()
    with admin.cursor() as cur:
        cur.execute('SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)',
                    ([roles['stale'], roles['unmanaged']],))
        assert cur.fetchall() == [(roles['unmanaged'],)]
        cur.execute(
            'SELECT datname, pg_get_userbyid(datdba) FROM pg_database '
            'WHERE datname = ANY(%s) ORDER BY datname', (databases,))
        assert cur.fetchall() == [
            (databases[0], roles['owner0']),
            (databases[1], roles['owner1']),
        ]

    for i, database in enumerate(databases):
        with connection(make_dsn(**dict(params, dbname=database))) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id FROM retained_data')
                assert cur.fetchall() == [(40 + i,)]
                cur.execute(
                    "SELECT tableowner FROM pg_tables "
                    "WHERE schemaname = 'public' "
                    "AND tablename = 'retained_data'")
                assert cur.fetchone() == (roles['owner%d' % i],)

    # New roles on PG16+ can have ADMIN without inherited ownership privileges.
    with connection(config['postgres']['dsn']) as sync:
        with sync.cursor() as cur:
            cur.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(
                sql.Identifier(roles['later'])))
    with connection(make_dsn(**dict(params, dbname=databases[1]))) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE later_data (id integer PRIMARY KEY)')
            cur.execute('INSERT INTO later_data VALUES (99)')
            cur.execute(sql.SQL('ALTER TABLE later_data OWNER TO {}').format(
                sql.Identifier(roles['later'])))
    config['postgres']['managed_roles_query'] = [roles['keep'], roles['later']]
    synchronize()
    with admin.cursor() as cur:
        cur.execute('SELECT 1 FROM pg_roles WHERE rolname = %s',
                    (roles['later'],))
        assert cur.fetchone() is None
    with connection(make_dsn(**dict(params, dbname=databases[1]))) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM later_data')
            assert cur.fetchall() == [(99,)]
            cur.execute(
                "SELECT tableowner FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = 'later_data'")
            assert cur.fetchone() == (roles['owner1'],)
