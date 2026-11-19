"""Evidence and operator decisions for the asset and animation gates."""

import hashlib

from . import render, review


class GateError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def new_review():
    return {"assets": {}, "animation": {}, "prepared": None}


def checks(project, scope):
    if not project.get("plan") or not project["plan"]["approved"]:
        return []
    return review.asset_checks(project) if scope == "assets" else review.animation_checks(project)


def proof_path(folder, scope, check_id):
    return folder / "review" / scope / f"{check_id}.png"


def proof_hash(path):
    try:
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()
    except OSError:
        return None


def has_proof(folder, scope, check, record, *, verify=True):
    if not record.get("proofHash"):
        return False
    path = proof_path(folder, scope, check["id"])
    if verify:
        return proof_hash(path) == record["proofHash"]
    return path.is_file()


def view(project, folder, scope):
    records = project["review"][scope]
    output = []
    for check in checks(project, scope):
        record = records.get(check["id"], {})
        result = {**check, "status": "pending"}
        if has_proof(folder, scope, check, record, verify=False):
            result.update({key: record[key] for key in ("status", "reason", "advice") if key in record})
            result["proofUrl"] = f"/api/projects/{project['id']}/{scope}/checks/{check['id']}/proof"
        elif record.get("status") == "error":
            result.update(status="error", reason=record["reason"])
        output.append(result)
    return output


def require_approved(project, folder, scope, *, verify=True):
    label = "planned artwork uses" if scope == "assets" else "scenes and transitions"
    records = project["review"][scope]
    required = checks(project, scope)
    remaining = [
        check["scene"] for check in required
        if records.get(check["id"], {}).get("status") != "passed"
        or not has_proof(folder, scope, check, records[check["id"]], verify=verify)
    ]
    if remaining:
        scenes = ", ".join(str(scene) for scene in remaining[:8])
        extra = " and more" if len(remaining) > 8 else ""
        raise GateError(f"Review and pass the {label} for scenes {scenes}{extra}.")


def run(project, folder, scope, write_file):
    records = project["review"][scope]
    prove = review.asset_proof_png if scope == "assets" else review.animation_proof_png
    failures = []
    pending = [check for check in checks(project, scope) if not has_proof(folder, scope, check, records.get(check["id"], {}))]
    if scope == "animation" and pending and render.render_backend() == "canvas":
        try:
            batch = review.animation_proofs_batch(project, folder, pending)
            for check in pending:
                check_id = check["id"]
                proof = batch.get(check_id)
                if not proof:
                    reason = f"Scene {check['scene']}: proof could not be rendered."
                    failures.append(reason)
                    records[check_id] = {"status": "error", "reason": reason}
                    continue
                path = proof_path(folder, scope, check_id)
                write_file(path, proof)
                records[check_id] = {"status": "ready", "proofHash": hashlib.sha256(proof).hexdigest()}
            return failures
        except render.RenderError:
            # Fall back to per-scene proofs so exact failing scene errors are isolated
            pass
    for check in pending:
        check_id = check["id"]
        try:
            proof = prove(project, folder, check)
        except render.RenderError as error:
            reason = f"Scene {check['scene']}: {error}"
            failures.append(reason)
            records[check_id] = {"status": "error", "reason": reason}
            continue
        path = proof_path(folder, scope, check_id)
        write_file(path, proof)
        records[check_id] = {"status": "ready", "proofHash": hashlib.sha256(proof).hexdigest()}
    return failures


def selected(project, folder, scope, check_id):
    for check in checks(project, scope):
        if check["id"] == check_id:
            record = project["review"][scope].get(check_id, {})
            if not has_proof(folder, scope, check, record):
                raise GateError("Run checks to create valid proof before reviewing this item.")
            return check, record
    raise GateError("This review item no longer applies to the current plan.", 404)


def decide(record, passed, reason, recorded_at):
    findings = (record.get("advice") or {}).get("findings", [])
    unresolved = any(item["status"] in ("fail", "unclear") for item in findings)
    if passed and unresolved and not reason:
        raise GateError("Record how you resolved Claude’s findings before passing this check.")
    if not passed and not reason:
        raise GateError("Record the reason this check failed.")
    record.update(status="passed" if passed else "failed", reason=reason, reviewedAt=recorded_at)
