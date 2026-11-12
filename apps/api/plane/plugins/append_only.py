# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Database-level append-only guard for ``plugin_audit_events``.

The DDL lives here (not inline in the migration) so the migration and the
contract tests run the exact same statements: the pytest suite uses
``--nomigrations``, so the test installs the trigger itself and then fires
raw SQL at it.

``TRUNCATE`` is deliberately still possible — it is a table-level operator
used by test teardown / restore, not a way for application code to rewrite
a row, and ``FOR EACH ROW`` triggers do not fire on it.
"""

AUDIT_TABLE = "plugin_audit_events"
AUDIT_FUNCTION = "plugin_audit_events_append_only"
AUDIT_TRIGGER = "plugin_audit_events_append_only_trigger"

CREATE_AUDIT_APPEND_ONLY_SQL = f"""
CREATE OR REPLACE FUNCTION {AUDIT_FUNCTION}() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'plugin_audit_events is append-only: % is refused', TG_OP
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS {AUDIT_TRIGGER} ON {AUDIT_TABLE};

CREATE TRIGGER {AUDIT_TRIGGER}
    BEFORE UPDATE OR DELETE ON {AUDIT_TABLE}
    FOR EACH ROW
    EXECUTE FUNCTION {AUDIT_FUNCTION}();
"""

DROP_AUDIT_APPEND_ONLY_SQL = f"""
DROP TRIGGER IF EXISTS {AUDIT_TRIGGER} ON {AUDIT_TABLE};
DROP FUNCTION IF EXISTS {AUDIT_FUNCTION}();
"""
