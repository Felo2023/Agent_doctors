"""Run *real* OpenCode models on one fictional solved-case scoring smoke test.

This is not a medical benchmark. The `team` is a bounded-round HTTP Mail Bus panel.
"""

import argparse
import json
from pathlib import Path
import tempfile

from validation.harness import SequentialSuite
from validation.model_runner import run_active


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-shared-auth", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not args.demo_shared_auth:
        parser.error("only explicit fictional-data runs may reuse existing OpenCode credentials")
    credential = Path.home() / ".local/share/opencode/auth.json"
    if not credential.is_file():
        parser.error("OpenCode credentials are not configured")
    root = Path(tempfile.mkdtemp(prefix="synthetic-model-evaluation-"))
    root.chmod(0o700)
    inputs = root / "operator-inputs"
    answers = root / "operator-sealed-answers"
    inputs.mkdir()
    answers.mkdir()
    source = {"presentation": "Purely fictional exercise: BLUE marker is present and pulse is SQUARE. GREEN, RED and ROUND markers are absent. No acute alert is present.",
              "candidate_definitions": {
                  "CAUSE-A": "Occurs with BLUE and SQUARE together.",
                  "CAUSE-B": "Occurs with GREEN and ROUND together.",
                  "CAUSE-C": "Occurs with RED regardless of pulse."},
              "allowed_diagnosis_ids": ["CAUSE-A", "CAUSE-B", "CAUSE-C"]}
    (inputs / "fictional-931.txt").write_text(json.dumps(source), encoding="utf-8")
    manifest = inputs / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "kind": "synthetic",
                                    "cases": [{"case_id": "SYNTH-931", "input_file": "fictional-931.txt"}]}))
    (answers / "SYNTH-931.json").write_text(json.dumps({"case_id": "SYNTH-931",
                                                     "accepted_ids": ["CAUSE-A"], "critical_flags": []}))
    suite = SequentialSuite(manifest, answers, root / "one-arm-at-a-time", root / "operator-results")
    print("Synthetic model evaluation root:", root, flush=True)
    while (active := suite.prepare_next()) is not None:
        print("Running:", active["case_id"], active["arm"], flush=True)
        result = run_active(suite, credential, args.timeout)
        submission = json.loads(result.read_text())
        print("Completed:", active["arm"], submission["diagnoses"], flush=True)
    report = suite.score()
    print("Status:", report["status"], flush=True)
    print("Clinical validation:", report["clinical_validation"], flush=True)
    print("Result:", root / "operator-results/score.json", flush=True)


if __name__ == "__main__":
    main()
