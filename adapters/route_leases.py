"""Release only positively resolved part attempts, never by age or lease timeout."""


def release_resolved_route_leases(connection, scope_id):
    connection.execute("DELETE FROM authorized_route_leases AS l WHERE l.scope_id=? AND EXISTS ("
        "SELECT 1 FROM report_parts p WHERE p.outbox_id=l.outbox_id AND p.ordinal=l.ordinal AND ("
        "(p.state='sent' AND p.generation=l.generation) OR "
        "(p.state IN ('queued','failed') AND p.error_code='provider_not_sent' AND p.generation=l.generation) OR "
        "(p.state IN ('queued','failed') AND p.error_code='verified_absent' AND p.generation=l.generation+1)))", (scope_id,))
