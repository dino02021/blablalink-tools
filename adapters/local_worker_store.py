"""Local mock worker capabilities share one coordinator connection and migrations."""

from adapters.local_healthcheck_workflows import LocalHealthcheckWorkflows
from adapters.local_system_status_publications import LocalSystemStatusPublications


class LocalWorkerStore(LocalHealthcheckWorkflows,LocalSystemStatusPublications):
    pass
