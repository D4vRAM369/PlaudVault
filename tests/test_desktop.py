"""El lanzador de escritorio no hereda credenciales ni rutas persistentes."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from plaudvault.desktop import runtime_environment, webkit_process_problem, path_in_ram


def test_private_environment():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch.dict(os.environ, {"PLAUD_TOKEN": "synthetic-credential", "WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS": "1", "TMPDIR": "/tmp", "DISPLAY": ":0", "PULSE_SERVER": "tcp:example.invalid"}):
            env = runtime_environment(root)
        assert "PLAUD_TOKEN" not in env
        assert "PULSE_SERVER" not in env
        assert "WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS" not in env
        assert env["WEBKIT_FORCE_SANDBOX"] == "1"
        assert env["DISPLAY"] == ":0"
        for key in ("HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "TMPDIR", "GST_REGISTRY", "MESA_SHADER_CACHE_DIR"):
            assert Path(env[key]).is_relative_to(root), key
    print("✅ test_private_environment")


def test_renderer_sandbox():
    assert webkit_process_problem("Name:\tWebKitWebProces\nNoNewPrivs:\t1\nSeccomp:\t2\n") is None
    assert webkit_process_problem("NoNewPrivs:\t0\nSeccomp:\t2\n")
    assert webkit_process_problem("NoNewPrivs:\t1\nSeccomp:\t0\n")
    assert webkit_process_problem("")
    print("✅ test_renderer_sandbox")


def test_runtime_mounts():
    mounts = ("1 0 0:1 / / rw - ext4 /dev/root rw\n"
              "2 1 0:2 / /ram rw - tmpfs tmpfs rw\n"
              "3 2 0:3 / /ram/disk rw - ext4 /dev/test rw\n")
    with patch.object(Path, "read_text", return_value=mounts):
        assert path_in_ram(Path('/ram/session'))
        assert not path_in_ram(Path('/ram/disk/session'))
        assert not path_in_ram(Path('/disk'))
    with patch.object(Path, "read_text", side_effect=OSError):
        assert not path_in_ram(Path('/ram'))
    print("✅ test_runtime_mounts")


if __name__ == "__main__":
    test_private_environment()
    test_renderer_sandbox()
    test_runtime_mounts()
