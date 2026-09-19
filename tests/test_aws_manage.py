"""Exercise the operator wrapper without contacting AWS or issuing real keys."""

import os
import shutil
import stat
import subprocess
from pathlib import Path


def run_manage(tmp_path, *, failed=False, existing=False):
    root = tmp_path / "repo"
    deploy = root / "deploy" / "aws"
    deploy.mkdir(parents=True)
    shutil.copy(Path(__file__).parents[1] / "deploy/aws/manage.sh", deploy / "manage.sh")
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "aws"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "with Path(os.environ['CALL_LOG']).open('a') as f: f.write(json.dumps(args)+'\\n')\n"
        "if args[:2] == ['cloudformation','describe-stacks']: print('test-value')\n"
        "elif args[:2] == ['ecs','run-task']:\n"
        " print(json.dumps({'failures':[{'reason':'capacity unavailable'}]} if os.environ.get('FAIL_TASK') else "
        "{'tasks':[{'taskArn':'arn:aws:ecs:us-east-1:123:task/test-task'}]}))\n"
        "elif args[:2] == ['ecs','describe-tasks']: print('0' if 'exitCode' in ' '.join(args) else 'exited')\n"
        "elif args[:2] == ['logs','get-log-events']: print('{\"access_key\":\"test-secret\"}')\n"
    )
    fake.chmod(0o700)
    target = tmp_path / "key.json"
    if existing:
        target.write_text("keep existing key")
    calls = tmp_path / "calls.jsonl"
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"], "CALL_LOG": str(calls)}
    if failed:
        env["FAIL_TASK"] = "1"
    result = subprocess.run(
        ["bash", str(deploy / "manage.sh"), "--output-file", str(target), "create-workspace"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, target, calls.read_text() if calls.exists() else ""


def test_private_key_output_and_cloud_log_cleanup(tmp_path):
    result, target, calls = run_manage(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "test-secret" not in result.stdout + result.stderr
    assert "test-secret" in target.read_text()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert '"delete-log-stream"' in calls


def test_task_launch_failure_stops_before_wait_or_log_deletion(tmp_path):
    result, target, calls = run_manage(tmp_path, failed=True)
    assert result.returncode != 0
    assert "capacity unavailable" in result.stderr
    assert not target.exists()
    assert '"wait"' not in calls and '"delete-log-stream"' not in calls


def test_existing_key_file_is_never_overwritten(tmp_path):
    result, target, calls = run_manage(tmp_path, existing=True)
    assert result.returncode != 0
    assert target.read_text() == "keep existing key"
    assert not calls
