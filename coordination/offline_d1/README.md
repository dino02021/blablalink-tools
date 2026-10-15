# Local D1 contract runtime

Development fixture only. No account, remote binding, deployment configuration or business database is used.

Requires Node.js 22+ and the exact dependencies in package-lock.json. Install locally with `npm ci --ignore-scripts --no-audit --no-fund` in this directory. Dependencies remain in ignored node_modules.

Run through the owned Windows process supervisor: `python -m executors.local.d1_contract_runtime` from the repository root. The supervisor permits at most four processes and 512 MiB aggregate committed memory, with a bounded deadline and output. The ordinary Python executor still permits only one process.

The fixture imports only the independent schema in coordination/d1_contracts. Its compatibility date comes from the pinned workerd package; it is not a deployment compatibility-date recommendation. Miniflare data is ephemeral, the Worker fetch entry returns disabled, and the one intentional outbound probe is intercepted locally.

See docs/spec/102-local-miniflare-d1-runtime.md for the evidence boundaries, observed guard write cost, positive receipt lookup, and remaining production adapter work.
