// Thin offline-tested bridge. No clasp deployment or installed triggers are provided.
// BBL_CONTROL_CONFIG must explicitly enable this bridge; missing config is disabled.
function onEdit(event) {
  const properties = PropertiesService.getScriptProperties();
  const config = _bridgeConfig(properties);
  if (!config || config.enabled !== true) return {state: "disabled"};
  if (!event || !event.range) return {state: "ignored"};
  const range = event.range;
  const sheet = range.getSheet();
  if (sheet.getParent().getId() !== config.spreadsheet_id || sheet.getSheetId() !== config.sheet_id) return {state: "ignored"};
  const watched = config.input_range;
  if (!watched || range.getLastRow() < watched.start_row || range.getRow() > watched.end_row ||
      range.getLastColumn() < watched.start_column || range.getColumn() > watched.end_column) return {state: "ignored"};
  return _submitReconcileHint(config, properties);
}

function reconcileTimer() {
  const properties = PropertiesService.getScriptProperties();
  const config = _bridgeConfig(properties);
  if (!config || config.enabled !== true) return {state: "disabled"};
  return _submitReconcileHint(config, properties);
}

function _bridgeConfig(properties) {
  const value = properties.getProperty("BBL_CONTROL_CONFIG");
  if (!value) return null;
  try { return JSON.parse(value); } catch (error) { return null; }
}

function _submitReconcileHint(config, properties) {
  if (!/^https:\/\/[^?#]+\/v1\/requests$/.test(config.control_url || "") ||
      ![config.principal_id, config.key_id, config.scope_id, config.code_sha, config.route_snapshot_id].every(v => typeof v === "string" && v.length > 0)) {
    return {state: "configuration_invalid"};
  }
  const key = properties.getProperty("BBL_CONTROL_SIGNING_KEY");
  if (!key || Utilities.newBlob(key).getBytes().length < 32) return {state: "configuration_invalid"};
  const requestId = Utilities.getUuid();
  const task = {command: "reconcile", scope_id: config.scope_id, code_sha: config.code_sha,
    versions: {reconcile_route: config.route_snapshot_id}, family: "config_reconcile", intent_id: "hint:" + requestId};
  const body = _bridgeCanonical({task});
  const envelope = {schema_version: 1, principal_id: config.principal_id, key_id: config.key_id,
    timestamp: Math.floor(Date.now() / 1000), nonce: Utilities.getUuid(), request_id: requestId,
    method: "POST", path: "/v1/requests",
    body_sha256: _bridgeHex(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, body, Utilities.Charset.UTF_8))};
  envelope.signature = _bridgeHex(Utilities.computeHmacSha256Signature(_bridgeCanonical(envelope), key, Utilities.Charset.UTF_8));
  try {
    const response = UrlFetchApp.fetch(config.control_url, {method: "post", contentType: "application/json; charset=utf-8",
      headers: {"X-Control-Envelope": _bridgeCanonical(envelope)}, payload: body, muteHttpExceptions: true, followRedirects: false});
    if (response.getResponseCode() !== 202) return {state: "not_accepted"};
    const receipt = JSON.parse(response.getContentText());
    return receipt.state === "accepted" && typeof receipt.job_id === "string" && receipt.job_id.length > 0
      ? {state: "accepted", job_id: receipt.job_id} : {state: "acceptance_unknown"};
  } catch (error) { return {state: "acceptance_unknown"}; }
}

function _bridgeCanonical(value) {
  if (Array.isArray(value)) return "[" + value.map(_bridgeCanonical).join(",") + "]";
  if (value !== null && typeof value === "object") return "{" + Object.keys(value).sort().map(k => JSON.stringify(k) + ":" + _bridgeCanonical(value[k])).join(",") + "}";
  return JSON.stringify(value);
}

function _bridgeHex(bytes) {
  return bytes.map(value => ((value + 256) % 256).toString(16).padStart(2, "0")).join("");
}
