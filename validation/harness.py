"""Sequential and answer-separated scoring harness for synthetic solved cases.

This scaffolds an evaluation protocol, NOT clinical validation. It does not
run a diagnostic model or supply reference answers to an agent.
"""

import argparse
import fcntl
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


ARMS = ("team", "opus", "astra", "sol")
CASE_ID = re.compile(r"SYNTH-[0-9]{3,6}\Z")
PROJECT = Path(__file__).resolve().parents[1]


def exclusive_step(method):
    """Serialize operator steps across concurrent CLI processes for one suite."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        self.result_root.mkdir(parents=True, exist_ok=True)
        path = self.result_root / ".suite.lock"
        if path.is_symlink():
            raise ValueError("suite lock cannot be a symlink")
        with path.open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("another operator step is active for this suite") from exc
            try:
                return method(self, *args, **kwargs)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    return guarded


def write_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".validation-", delete=False) as file:
        temporary = Path(file.name)
        json.dump(value, file, indent=2, ensure_ascii=False, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def distinct_paths(paths):
    resolved = [Path(path).resolve() for path in paths]
    for i, left in enumerate(resolved):
        if left.is_relative_to(PROJECT):
            raise ValueError("validation answers/work/results must stay outside the repository")
        for right in resolved[i + 1:]:
            if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError("validation answer, workspace and results directories must be disjoint")
    return resolved


class SequentialSuite:
    def __init__(self, manifest_path, answer_root, work_root, result_root):
        self.manifest_path = Path(manifest_path).resolve(strict=True)
        self.answer_root, self.work_root, self.result_root = distinct_paths((answer_root, work_root, result_root))
        if self.manifest_path.is_relative_to(self.answer_root) or self.manifest_path.is_relative_to(self.work_root):
            raise ValueError("manifest cannot be inside sealed answers or an agent workspace")
        if any(path.is_symlink() for path in (self.answer_root, self.work_root, self.result_root)):
            raise ValueError("validation roots cannot be symlinks")
        self.manifest_bytes = self.manifest_path.read_bytes()
        self.manifest = json.loads(self.manifest_bytes)
        if (self.manifest.get("schema_version") != 1 or self.manifest.get("kind") != "synthetic"
                or not isinstance(self.manifest.get("cases"), list) or not self.manifest["cases"]):
            raise ValueError("synthetic case manifest required")
        cases = self.manifest["cases"]
        if (len(cases) > 100 or len({case.get("case_id") for case in cases}) != len(cases)
                or any(not isinstance(case.get("case_id"), str) or not CASE_ID.fullmatch(case["case_id"]) for case in cases)):
            raise ValueError("invalid or duplicate synthetic case IDs")
        self.inputs = {}
        for case in cases:
            file = self.manifest_path.parent / case.get("input_file", "")
            if (Path(case.get("input_file", "")).name != case.get("input_file")
                    or not file.is_file() or file.is_symlink()
                    or file.resolve().is_relative_to(self.answer_root)
                    or file.resolve().is_relative_to(self.work_root)):
                raise ValueError("case input must be a local regular file in manifest directory")
            self.inputs[case["case_id"]] = file
        self.input_hashes = {cid: hashlib.sha256(path.read_bytes()).hexdigest()
                             for cid, path in self.inputs.items()}
        self.sequence = [(case["case_id"], arm) for case in cases for arm in ARMS]
        self.progress_path = self.result_root / "progress.json"

    def ensure_progress(self):
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.result_root.mkdir(parents=True, exist_ok=True)
        if not self.progress_path.exists():
            write_atomic(self.progress_path, {"schema_version": 1, "kind": "synthetic_operational_test",
                                              "manifest_sha256": hashlib.sha256(self.manifest_bytes).hexdigest(),
                                              "input_hashes": self.input_hashes,
                                              "next_index": 0, "active": None, "completed": []})
        progress = json.loads(self.progress_path.read_text())
        if progress["manifest_sha256"] != hashlib.sha256(self.manifest_path.read_bytes()).hexdigest():
            raise ValueError("manifest changed during evaluation; start a new suite")
        if progress["input_hashes"] != {cid: hashlib.sha256(path.read_bytes()).hexdigest()
                                         for cid, path in self.inputs.items()}:
            raise ValueError("prepared case input changed during evaluation")
        return progress

    @exclusive_step
    def prepare_next(self):
        progress = self.ensure_progress()
        if progress["active"] is not None:
            raise ValueError("finish the active arm before starting another")
        if progress["next_index"] == len(self.sequence):
            return None
        case_id, arm = self.sequence[progress["next_index"]]
        workspace = self.work_root / case_id / arm
        if workspace.exists():
            raise ValueError("workspace already exists; inspect before retry")
        workspace.mkdir(parents=True)
        source = self.inputs[case_id]
        input_bytes = source.read_bytes()
        (workspace / "case-input.txt").write_bytes(input_bytes)
        if len(list(workspace.iterdir())) != 1:
            raise AssertionError("agent workspace must contain only the current case input")
        progress["active"] = {"case_id": case_id, "arm": arm,
                              "input_sha256": hashlib.sha256(input_bytes).hexdigest(),
                              "workspace": str(workspace)}
        write_atomic(self.progress_path, progress)
        return progress["active"]

    @exclusive_step
    def submit(self, submission_path, *, provenance=None):
        progress = self.ensure_progress()
        active = progress["active"]
        if not active:
            raise ValueError("no active arm")
        if hashlib.sha256((Path(active["workspace"]) / "case-input.txt").read_bytes()).hexdigest() != active["input_sha256"]:
            raise ValueError("case input changed during this arm")
        submission_path = Path(submission_path).resolve(strict=True)
        if (submission_path.is_relative_to(self.answer_root) or submission_path.is_relative_to(self.work_root)
                or submission_path.is_symlink()):
            raise ValueError("submit a separate output file, never an answer key or agent input")
        data = json.loads(submission_path.read_text())
        if (data.get("case_id"), data.get("arm")) != (active["case_id"], active["arm"]):
            raise ValueError("submission does not match active case/arm")
        diagnoses, alerts = data.get("diagnoses"), data.get("critical_flags")
        if (not isinstance(diagnoses, list) or len(diagnoses) > 10
                or any(not isinstance(value, str) or not value.strip() for value in diagnoses)
                or len(set(diagnoses)) != len(diagnoses)
                or not isinstance(alerts, list) or any(not isinstance(value, str) for value in alerts)):
            raise ValueError("diagnoses and critical_flags must be bounded string lists")
        prepared_input = (Path(active["workspace"]) / "case-input.txt").read_text(encoding="utf-8")
        try:
            catalog = json.loads(prepared_input).get("allowed_diagnosis_ids")
        except (ValueError, AttributeError):
            catalog = None
        if catalog is not None:
            if (not isinstance(catalog, list) or not catalog
                    or any(not isinstance(value, str) for value in catalog)
                    or len(set(catalog)) != len(catalog)):
                raise ValueError("synthetic diagnosis catalog is invalid")
            if not set(diagnoses).issubset(catalog):
                raise ValueError("submission contains an ID outside the prepared diagnosis catalog")
        provenance = provenance or {"kind": "simulated"}
        if provenance.get("kind") not in ("simulated", "model_backed"):
            raise ValueError("unknown execution provenance")
        destination = self.result_root / "submissions" / active["case_id"] / (active["arm"] + ".json")
        if destination.exists():
            raise ValueError("submission already exists")
        write_atomic(destination, {"case_id": active["case_id"], "arm": active["arm"],
                                    "diagnoses": diagnoses, "critical_flags": alerts,
                                   "input_sha256": active["input_sha256"], "provenance": provenance})
        progress["completed"].append({"case_id": active["case_id"], "arm": active["arm"],
                                       "submission_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
        progress["active"] = None
        progress["next_index"] += 1
        write_atomic(self.progress_path, progress)
        workspace = Path(active["workspace"])
        if not workspace.is_relative_to(self.work_root) or workspace.is_symlink():
            raise ValueError("invalid workspace cleanup target")
        shutil.rmtree(workspace)
        return destination

    @exclusive_step
    def score(self):
        progress = self.ensure_progress()
        if progress["active"] or progress["next_index"] != len(self.sequence):
            raise ValueError("all case/arm runs must finish before scoring")
        scores = []
        execution_modes = set()
        for case_id, arm in self.sequence:
            answer = self.answer_root / (case_id + ".json")
            if not answer.is_file() or answer.is_symlink():
                raise ValueError("sealed answer missing for " + case_id)
            gold = json.loads(answer.read_text())
            if (gold.get("case_id") != case_id or not isinstance(gold.get("accepted_ids"), list)
                    or not gold["accepted_ids"] or not isinstance(gold.get("critical_flags"), list)):
                raise ValueError("invalid scorer-owned answer for " + case_id)
            try:
                catalog = json.loads(self.inputs[case_id].read_text(encoding="utf-8")).get("allowed_diagnosis_ids")
            except (ValueError, AttributeError):
                catalog = None
            if catalog is not None and not set(gold["accepted_ids"]).issubset(catalog):
                raise ValueError("sealed answer cannot be expressed using the candidate catalog")
            file = self.result_root / "submissions" / case_id / (arm + ".json")
            recorded = next((entry for entry in progress["completed"]
                             if entry["case_id"] == case_id and entry["arm"] == arm), None)
            if (not recorded or file.is_symlink()
                    or recorded.get("submission_sha256") != hashlib.sha256(file.read_bytes()).hexdigest()):
                raise ValueError("frozen submission changed since arm completion")
            submission = json.loads(file.read_text())
            execution_modes.add(submission.get("provenance", {}).get("kind", "simulated"))
            hits = [submission["diagnoses"][:limit] for limit in (1, 3, 10)]
            scores.append({"case_id": case_id, "arm": arm,
                           "top_1": bool(set(hits[0]) & set(gold["accepted_ids"])),
                           "top_3": bool(set(hits[1]) & set(gold["accepted_ids"])),
                           "top_10": bool(set(hits[2]) & set(gold["accepted_ids"])),
                           "critical_found": sorted(set(submission["critical_flags"]) & set(gold["critical_flags"])),
                           "critical_missed": sorted(set(gold["critical_flags"]) - set(submission["critical_flags"])),
                           "critical_extra": sorted(set(submission["critical_flags"]) - set(gold["critical_flags"]))})
        summary = {arm: {metric: sum(row[metric] for row in scores if row["arm"] == arm)
                         for metric in ("top_1", "top_3", "top_10")} for arm in ARMS}
        report = {"status": "synthetic_model_backed_operational_test" if execution_modes == {"model_backed"}
                  else "synthetic_scoring_smoke_test_only", "clinical_validation": False,
                  "execution_modes": sorted(execution_modes),
                  "cases": len(self.manifest["cases"]), "summary_counts": summary,
                  "scores": scores, "limitations": ["No clinical cases or verified clinical labels",
                                                 "Bounded evaluation panel is not the full clinical diagnostic workflow",
                                                 "Human-adjudicated published cases not evaluated"]}
        write_atomic(self.result_root / "score.json", report)
        return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "submit", "score"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--answers", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--submission")
    args = parser.parse_args()
    suite = SequentialSuite(args.manifest, args.answers, args.work, args.results)
    if args.action == "prepare":
        result = suite.prepare_next()
    elif args.action == "submit":
        if not args.submission:
            parser.error("--submission required for submit")
        result = str(suite.submit(args.submission))
    else:
        result = suite.score()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
